from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx

from app.config.settings import Settings
from app.core.sessions import SCAN_PHASES, phase_at
from app.models.db import database_ready
from app.models.utc_time import parse_utc
from app.ops.paths import FEED_STATUS, HEARTBEAT, LAST_SCAN
from app.ops.safety import policy_errors

_BROKER_CACHE: tuple[float, dict] | None = None
_BROKER_CACHE_SECONDS = 30.0


def clear_broker_cache() -> None:
    global _BROKER_CACHE
    _BROKER_CACHE = None


def _age(path) -> int | None:
    if not path.exists():
        return None
    return max(0, int(datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return parse_utc(value)
    except ValueError:
        return None


def _read_json(path) -> dict | None:
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _heartbeat_meta(path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    if not text:
        return {}
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}
    beat, _, phase = text.partition(" ")
    return {"beat_at": beat, "phase": phase}


def worker_status(age: int | None, limit: int) -> dict:
    alive = age is not None and age <= limit
    return {
        "status": "HEALTHY" if alive else "FAILED",
        "alive": alive,
        "heartbeat_age_seconds": age,
    }


def cycle_status(scan: dict | None, beat: dict | None, now: datetime, limit: int) -> dict:
    """The last cycle succeeded only from a success timestamp, not from the heartbeat.

    ``last_success`` wins when a writer recorded it (including on the heartbeat
    record). Otherwise a scan file with ``finished_at`` and an empty ``blocked``
    is the existing success stamp.
    """
    record = dict(scan or {})
    extra = beat or {}
    if extra.get("last_success") and not record.get("last_success"):
        record["last_success"] = extra["last_success"]
    blocked = str(record.get("blocked") or "").strip()
    explicit = record.get("last_success")
    if explicit:
        moment = _parse(explicit)
        source = "last_success"
    elif not blocked and record.get("finished_at"):
        moment = _parse(record.get("finished_at"))
        source = "finished_at"
    else:
        moment = None
        source = None
    observed = _parse(record.get("observed_at")) if record.get("observed_at") else None
    age = None if moment is None else max(0, int((now - moment).total_seconds()))
    fresh = moment is not None and age is not None and age <= limit
    # A blocked file is the last cycle, unless a success stamp is newer than that failure.
    if blocked and not (moment is not None and observed is not None and moment > observed):
        fresh = False
    ok = fresh
    return {
        "status": "HEALTHY" if ok else "FAILED",
        "succeeded": ok,
        "last_success": None if moment is None else moment.isoformat(),
        "age_seconds": age,
        "source": source,
        "blocked": blocked,
    }


def _quote_moment(block: object) -> tuple[datetime | None, datetime | None]:
    if not isinstance(block, dict):
        return None, None
    quote = _parse(block.get("newest_quote")) if block.get("newest_quote") else None
    success = _parse(block.get("last_success")) if block.get("last_success") else None
    return quote, success


def market_status(feed: dict | None, now: datetime, limit: int) -> dict:
    body = feed or {}
    market_quote, market_success = _quote_moment(body.get("market_status"))
    option_quote, option_success = _quote_moment(body.get("options_status"))
    quotes = [item for item in (market_quote, option_quote) if item is not None]
    successes = [item for item in (market_success, option_success) if item is not None]
    if quotes:
        moment = max(quotes)
        basis = "newest_quote"
    elif successes:
        moment = max(successes)
        basis = "last_success"
    else:
        moment = None
        basis = None
    success = max(successes) if successes else None
    age = None if moment is None else max(0, int((now - moment).total_seconds()))
    fresh = moment is not None and age is not None and age <= limit
    return {
        "status": "HEALTHY" if fresh else "FAILED",
        "fresh": fresh,
        "last_success": None if success is None else success.isoformat(),
        "newest_quote": None if not quotes else max(quotes).isoformat(),
        "age_seconds": age,
        "basis": basis,
    }


def probe_broker(settings: Settings) -> dict:
    """Paper account read. Cached so a readiness poll does not hammer the broker."""
    global _BROKER_CACHE
    now = time.monotonic()
    if _BROKER_CACHE is not None and now - _BROKER_CACHE[0] < _BROKER_CACHE_SECONDS:
        return dict(_BROKER_CACHE[1])
    result = _probe_broker(settings)
    _BROKER_CACHE = (now, result)
    return dict(result)


def _probe_broker(settings: Settings) -> dict:
    from app.integrations.alpaca import auth_headers, paper_base
    from app.integrations.secrets import resolve_secret

    key, _key_origin = resolve_secret(settings, "alpaca_api_key")
    secret, _secret_origin = resolve_secret(settings, "alpaca_api_secret")
    if not key or not secret:
        return {"status": "FAILED", "reachable": False, "detail": "missing_key", "latency_ms": None}
    started = time.perf_counter()
    try:
        base = paper_base(settings.trading_mode)
        with httpx.Client(base_url=base, headers=auth_headers(key, secret), timeout=2.5) as client:
            response = client.get("/v2/account")
            response.raise_for_status()
            account = response.json()
    except Exception:
        latency = int((time.perf_counter() - started) * 1000)
        return {"status": "FAILED", "reachable": False, "detail": "unreachable", "latency_ms": latency}
    latency = int((time.perf_counter() - started) * 1000)
    reachable = isinstance(account, dict) and bool(account)
    return {
        "status": "HEALTHY" if reachable else "FAILED",
        "reachable": reachable,
        "detail": "account" if reachable else "empty",
        "latency_ms": latency,
    }


def build_health(settings: Settings) -> tuple[int, dict]:
    database = "HEALTHY" if database_ready() else "FAILED"
    paper = "HEALTHY" if settings.trading_mode.upper() == "PAPER" and settings.paper_only else "FAILED"
    policy = "HEALTHY" if not policy_errors(settings.trading()) else "FAILED"
    core_ok = database == "HEALTHY" and paper == "HEALTHY" and policy == "HEALTHY"
    body = {
        "status": "ok" if core_ok else "failed",
        "app": settings.app_name,
        "overall": "HEALTHY" if core_ok else "FAILED",
        "components": {
            "api": "HEALTHY",
            "database": database,
            "paper": paper,
            "live": "DISABLED",
            "risk_engine": policy,
            "exit_engine": policy,
        },
    }
    return (200 if core_ok else 503), body


def build_ready(settings: Settings, *, now: datetime | None = None, broker_probe=None) -> tuple[int, dict]:
    """Readiness is the four live components, not a hardcoded provider label.

    HTTP 200 means this process could write the report (Docker probes ``/health``).
    ``ready`` / ``status`` follow the component breakdown.
    """
    code, health = build_health(settings)
    moment = now or datetime.now(timezone.utc)
    trading = settings.trading()
    limit = trading.scan_interval_seconds * 3
    worker = worker_status(_age(HEARTBEAT), limit)
    beat = _heartbeat_meta(HEARTBEAT)
    cycle = cycle_status(_read_json(LAST_SCAN), beat, moment, limit)
    market = market_status(_read_json(FEED_STATUS), moment, limit)
    broker = broker_probe(settings) if broker_probe is not None else probe_broker(settings)
    phase = phase_at(moment, settings.calendar())
    reasons: list[str] = []
    if health["overall"] != "HEALTHY":
        reasons.append("core")
    if not worker["alive"]:
        reasons.append("worker")
    if not cycle["succeeded"]:
        reasons.append("last_cycle")
    if not market["fresh"]:
        reasons.append("market_data")
    if not broker.get("reachable"):
        reasons.append("broker")
    session_open = phase in SCAN_PHASES
    if not session_open:
        reasons.append("session_closed")
    ready = not any(item in reasons for item in ("core", "worker", "last_cycle", "market_data", "broker"))
    autonomous = "READY" if ready and session_open else "BLOCKED"
    if code != 200:
        autonomous = "BLOCKED"
        ready = False
    body = {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "database": health["components"]["database"],
        "paper_only": settings.paper_only and settings.trading_mode.upper() == "PAPER",
        "trading_mode": "PAPER",
        "live": "DISABLED",
        "autonomous": autonomous,
        "blocked_reasons": reasons,
        "market_phase": phase,
        "fresh_within_seconds": limit,
        "components": {
            "worker": worker,
            "last_cycle": cycle,
            "market_data": market,
            "broker": broker,
        },
    }
    return code, body
