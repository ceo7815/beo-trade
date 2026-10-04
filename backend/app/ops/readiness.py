from __future__ import annotations

from datetime import datetime, timezone

from app.config.settings import Settings
from app.core.sessions import SCAN_PHASES, phase_at
from app.models.db import database_ready
from app.ops.paths import HEARTBEAT, MONITOR_HEARTBEAT, RECONCILE_HEARTBEAT
from app.ops.safety import policy_errors


def _age(path) -> int | None:
    if not path.exists():
        return None
    return max(0, int(datetime.now(timezone.utc).timestamp() - path.stat().st_mtime))


def _fresh(age: int | None, limit: int) -> str:
    if age is None:
        return "DEGRADED"
    if age <= limit:
        return "HEALTHY"
    return "FAILED"


def _configured(value: str) -> bool:
    return bool(value.strip())


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


def build_ready(settings: Settings) -> tuple[int, dict]:
    code, health = build_health(settings)
    trading = settings.trading()
    limit = trading.scan_interval_seconds * 3
    worker = _fresh(_age(HEARTBEAT), limit)
    monitor = _fresh(_age(MONITOR_HEARTBEAT), limit)
    reconcile = _fresh(_age(RECONCILE_HEARTBEAT), max(limit, trading.scan_interval_seconds * 3))
    phase = phase_at(datetime.now(timezone.utc), settings.calendar())
    session_open = phase in SCAN_PHASES
    theta = "BLOCKED"
    benzinga = "AUTH_ONLY" if _configured(settings.benzinga_api_key) else "DEGRADED"
    openai = "AUTH_ONLY" if _configured(settings.openai_api_key) else "BLOCKED"
    fred = "AUTH_ONLY" if _configured(settings.fred_api_key) else "DEGRADED"
    sec = "HEALTHY" if _configured(settings.sec_user_agent) else "DEGRADED"
    broker = "DEGRADED" if _configured(settings.alpaca_api_key) and _configured(settings.alpaca_api_secret) else "BLOCKED"
    reasons: list[str] = []
    if health["overall"] != "HEALTHY":
        reasons.append("core")
    if worker != "HEALTHY":
        reasons.append("worker_heartbeat")
    if not session_open:
        reasons.append("session_closed")
    if theta == "BLOCKED":
        reasons.append("BLOCKED_BY_ENTITLEMENT")
    if openai == "BLOCKED":
        reasons.append("ai")
    if broker == "BLOCKED":
        reasons.append("broker")
    autonomous = "READY" if not reasons else "BLOCKED"
    if code != 200:
        autonomous = "BLOCKED"
    body = {
        "status": "ready" if code == 200 else "not_ready",
        "database": health["components"]["database"],
        "paper_only": settings.paper_only and settings.trading_mode.upper() == "PAPER",
        "trading_mode": "PAPER",
        "live": "DISABLED",
        "autonomous": autonomous,
        "blocked_reasons": reasons,
        "market_phase": phase,
        "components": {
            **health["components"],
            "worker": worker,
            "monitor": monitor,
            "reconciliation": reconcile,
            "alpaca_paper": broker,
            "thetadata": theta,
            "benzinga": benzinga,
            "openai": openai,
            "fred": fred,
            "sec": sec,
        },
        "thetadata_detail": "BLOCKED_BY_ENTITLEMENT until a real quote and an option chain succeed. This endpoint does not call Theta.",
        "openai_detail": "AUTH_ONLY means a key is present. A decision call has not been verified here.",
        "risk_policy_version": trading.risk_policy_version,
        "exit_policy_version": trading.exit_policy_version,
    }
    return code, body
