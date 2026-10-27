"""Server-sent snapshots of positions, day P&L, and system status.

The trading loop is not on this path. Reads reuse the PR #4 memory cache.
A cache miss may refresh Alpaca with GET only: no database write, no halt
file write, and no lock held across the network call.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, Header, Request
from fastapi.responses import StreamingResponse

from app.config.settings import Settings

INTERVAL = 1.5
HEARTBEAT_SECONDS = 15
# Tests stop the generator. Production stays at 0 (no cap).
MAX_EVENTS = 0

_lock = threading.Lock()
_mem: dict[str, tuple[float, dict | None]] = {"positions": (0.0, None), "account": (0.0, None)}


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_plain)


def _dec(value) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _slim_position(item: dict) -> dict:
    keys = (
        "symbol",
        "underlying",
        "right",
        "strike",
        "expiration",
        "qty",
        "avg_entry_price",
        "current_price",
        "market_value",
        "cost_basis",
        "unrealized_pl",
        "unrealized_plpc",
    )
    return {key: _plain(item.get(key)) for key in keys if key in item}


def _slim_account(account: dict) -> dict:
    keys = ("equity", "cash", "last_equity", "buying_power", "portfolio_value")
    return {key: _plain(account.get(key)) for key in keys}


def _fetch_positions(settings: Settings) -> dict:
    from app.broker.runtime import open_paper_broker

    adapter = open_paper_broker(settings)
    try:
        items = adapter.get_positions() or []
    finally:
        adapter.close()
    return {"items": [_slim_position(item) for item in items if isinstance(item, dict)]}


def _fetch_account(settings: Settings) -> dict:
    from app.broker.runtime import open_paper_broker

    adapter = open_paper_broker(settings)
    try:
        account = adapter.get_account() or {}
    finally:
        adapter.close()
    return _slim_account(account if isinstance(account, dict) else {})


def _remember(name: str, payload: dict, cache_key: tuple) -> dict:
    from app.analytics.read_cache import BROKER_TTL_SECONDS, read_cache

    with _lock:
        _mem[name] = (time.monotonic(), payload)
    if read_cache.get(cache_key) is None:
        read_cache.set(cache_key, payload, BROKER_TTL_SECONDS)
    return payload


def _load(name: str, ttl: float, cache_key: tuple, fetch) -> dict | None:
    now = time.monotonic()
    with _lock:
        at, local = _mem[name]
        if local is not None and now - at < ttl:
            return local
    from app.analytics.read_cache import read_cache

    cached = read_cache.get(cache_key)
    try:
        payload = fetch()
    except Exception:
        return cached if isinstance(cached, dict) else local
    if not isinstance(payload, dict):
        return cached if isinstance(cached, dict) else local
    return _remember(name, payload, cache_key)


def _quotes(positions: dict | None) -> list[dict]:
    context: dict = {}
    try:
        from app.providers.thetadata import _state

        raw = getattr(_state, "context", None) or {}
        if isinstance(raw, dict):
            context = raw
    except Exception:
        context = {}
    symbols = ["SPY", "QQQ", "VIX"]
    for item in (positions or {}).get("items") or []:
        name = str(item.get("underlying") or "").strip().upper()
        if name and name not in symbols:
            symbols.append(name)
    rows = []
    for symbol in symbols:
        row = context.get(symbol)
        if not isinstance(row, dict) or row.get("price") in {None, ""}:
            continue
        rows.append(
            {
                "symbol": symbol,
                "price": _plain(row.get("price")),
                "change_percent": _plain(row.get("change_percent")),
            }
        )
    return rows


def _scan() -> dict:
    from app.analytics.routes import read_last_scan, read_scan_progress

    last = read_last_scan() or {}
    progress = read_scan_progress() or {}
    universe = last.get("universe") if isinstance(last.get("universe"), dict) else {}
    names: list[str] = []
    try:
        from app.universe.builder import _MEMORY

        for book in _MEMORY.values():
            symbols = getattr(book, "symbols", ()) or ()
            if symbols:
                names = [str(item) for item in list(symbols)[:8]]
                break
    except Exception:
        names = []
    return {
        "stage": progress.get("stage_done"),
        "discovered": universe.get("discovered"),
        "filtered": universe.get("filtered"),
        "recommendations": last.get("recommendations"),
        "buys": last.get("buys"),
        "names": names,
    }


def _day(settings: Settings, account: dict | None, risk: dict | None) -> dict:
    trading = settings.trading()
    equity = _dec((account or {}).get("equity"))
    last_equity = _dec((account or {}).get("last_equity"))
    from app.analytics.kill_line import display_kill_usd

    kill = display_kill_usd(trading, equity)
    mark = None if equity is None or last_equity is None else str((equity - last_equity).quantize(Decimal("0.01")))
    pnl = None
    source = "none"
    state = None
    if isinstance(risk, dict) and risk.get("today_pnl") not in {None, ""}:
        pnl = str(risk.get("today_pnl"))
        source = "risk"
        state = risk.get("daily_loss_state")
    elif mark is not None and equity is not None:
        pnl = mark
        source = "mark"
        from app.options.risk_limits import daily_loss_state

        state = daily_loss_state(equity, equity - last_equity, trading)  # type: ignore[operator]
    floor = None
    try:
        from app.options.day_floor import public_from_store

        floor = public_from_store(settings, datetime.now(timezone.utc))
    except Exception:
        floor = None
    return {
        "pnl": pnl,
        "mark_pnl": mark,
        "pnl_source": source,
        "daily_loss_used": None if not isinstance(risk, dict) else risk.get("daily_loss_used"),
        "daily_loss_limit": None if not isinstance(risk, dict) else risk.get("daily_loss_limit"),
        "daily_kill_loss_usd": kill if kill is not None else (None if not isinstance(risk, dict) else risk.get("daily_kill_loss_usd")),
        "daily_loss_state": state if state is not None else (None if not isinstance(risk, dict) else risk.get("daily_loss_state")),
        "open_positions": None if not isinstance(risk, dict) else risk.get("open_positions"),
        "day_floor": floor if floor is not None else (None if not isinstance(risk, dict) else risk.get("day_floor")),
    }


def _status(settings: Settings) -> dict:
    from app.control.halt import is_halted
    from app.core.sessions import phase_at, session_bounds
    from app.ops.paths import HEARTBEAT
    from app.providers.thetadata import feed_status

    now = datetime.now(timezone.utc)
    calendar = settings.calendar()
    phase = phase_at(now, calendar)
    bounds = session_bounds(now, calendar)
    age = None
    if HEARTBEAT.exists():
        age = max(0, int(now.timestamp() - HEARTBEAT.stat().st_mtime))
    theta = {}
    try:
        theta = feed_status()
    except Exception:
        theta = {}
    return {
        "market_phase": phase,
        "session_open": None if bounds is None else bounds[0].isoformat(),
        "session_close": None if bounds is None else bounds[1].isoformat(),
        "heartbeat_age_seconds": age,
        "trading_halted": is_halted(),
        "paper_only": True,
        "feeds": [
            {
                "id": "theta",
                "ok": bool(theta.get("last_success")),
                "last_success": theta.get("last_success"),
                "last_error": theta.get("last_error"),
            },
            {"id": "worker", "ok": age is not None and age <= settings.trading().scan_interval_seconds * 3, "age_seconds": age},
        ],
    }


def build_snapshot(settings: Settings) -> dict:
    """One JSON-safe frame. Safe to call from a worker thread."""
    from app.analytics.read_cache import read_cache

    positions = _load("positions", INTERVAL, ("broker", "positions"), lambda: _fetch_positions(settings)) or {"items": []}
    account = _load("account", INTERVAL, ("broker", "account"), lambda: _fetch_account(settings))
    risk = read_cache.get(("risk",))
    if not isinstance(risk, dict):
        risk = None
    if isinstance(positions, dict) and "items" not in positions:
        positions = {"items": positions.get("items") or []}
    # The broker route stores {"items": [...]} . A raw list never lands here.
    if isinstance(positions, dict) and isinstance(positions.get("account"), dict):
        positions = {"items": []}
    cached_positions = positions if isinstance(positions, dict) else {"items": []}
    if "items" not in cached_positions and isinstance(cached_positions.get("items"), list) is False:
        cached_positions = {"items": cached_positions.get("items") or []}
    # Route cache shape is {environment, source, items}. Pass items through slim if needed.
    items = cached_positions.get("items") if isinstance(cached_positions, dict) else []
    if not isinstance(items, list):
        items = []
    slim_items = [_slim_position(item) if isinstance(item, dict) else {} for item in items]
    position_frame = {"items": slim_items}
    account_frame = account if isinstance(account, dict) and "equity" in account else None
    if isinstance(account, dict) and isinstance(account.get("account"), dict):
        account_frame = _slim_account(account["account"])
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "positions": position_frame,
        "account": account_frame,
        "day": _day(settings, account_frame, risk),
        "status": _status(settings),
        "quotes": _quotes(position_frame),
        "scan": _scan(),
    }


def _changed(previous: dict, current: dict) -> dict:
    changed = {}
    for key in ("positions", "account", "day", "status", "quotes", "scan"):
        if previous.get(key) != current.get(key):
            changed[key] = current[key]
    if changed:
        changed["ts"] = current.get("ts")
    return changed


def _frame(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {_dump(payload)}\n\n"


def register(app: FastAPI, settings: Settings) -> None:
    @app.get("/api/v1/stream")
    async def stream(request: Request, authorization: str | None = Header(default=None)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)

        async def generate():
            from app.live import stream as live

            previous = None
            last_beat = 0.0
            sent = 0
            while True:
                if await request.is_disconnected():
                    break
                snap = await asyncio.to_thread(live.build_snapshot, settings)
                now = time.monotonic()
                if previous is None:
                    yield live._frame("snapshot", snap)
                    previous = snap
                    last_beat = now
                else:
                    changed = live._changed(previous, snap)
                    if changed:
                        yield live._frame("diff", changed)
                        previous = snap
                        last_beat = now
                    elif now - last_beat >= live.HEARTBEAT_SECONDS:
                        yield live._frame("heartbeat", {"ts": snap.get("ts")})
                        last_beat = now
                sent += 1
                if live.MAX_EVENTS and sent >= live.MAX_EVENTS:
                    break
                await asyncio.sleep(live.INTERVAL)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
