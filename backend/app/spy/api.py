"""Read-only SPY views. The API process does not hold the SPY keys and does not trade."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI

from app.spy.config import SYMBOL, load_config, status_path
from app.spy.engine import bars_from_rows
from app.spy.strategy import rth_sessions, series_for_day
from app.spy.store import connect_read


def _row(item) -> dict:
    return {key: item[key] for key in item.keys()}


def _snapshot() -> dict:
    path = status_path()
    if not path.exists():
        cfg = load_config()
        return {
            "ok": True,
            "enabled": False,
            "mode": "disabled" if not cfg.enabled else "starting",
            "paper_only": True,
            "symbol": SYMBOL,
            "account_last4": cfg.account[-4:] if cfg.account else "",
            "pdt_guard": cfg.pdt_guard,
            "allow_short": cfg.allow_short,
            "day_trades_left": None,
            "position": {"side": "flat", "qty": 0},
            "shadow": [{"symbol": symbol, "hypothetical": True, "orders": False, "qty": 0, "day_pnl": None} for symbol in cfg.shadow],
            "brakes": {"daily_loss": False, "drawdown": False, "kill": False, "entries_today": 0, "entries_max": 4},
        }
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        body = {"ok": False, "enabled": False, "error": "spy_unavailable"}
    heartbeat = path.with_name("spy_heartbeat.txt")
    if heartbeat.exists():
        age = max(0, int(datetime.now(timezone.utc).timestamp() - heartbeat.stat().st_mtime))
        body["heartbeat_age_seconds"] = age
        body["stale"] = age > 180
    return body


def _bars(symbol: str) -> list:
    cfg = load_config()
    conn = connect_read(cfg.db)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT start, open, high, low, close, volume FROM spy_bars WHERE symbol = ? ORDER BY start",
            (symbol,),
        ).fetchall()
    except Exception:
        return []
    finally:
        conn.close()
    return bars_from_rows(
        [
            {"t": row["start"], "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]}
            for row in rows
        ]
    )


def status(symbol: str = "") -> dict:
    try:
        body = _snapshot()
        names = [symbol] if symbol else [row.get("symbol") for row in body.get("symbols") or [] if row.get("symbol")]
        if not names:
            names = [SYMBOL]
        charts = {}
        for name in names:
            bars = _bars(name)
            sessions = rth_sessions(bars)
            charts[name] = series_for_day(sessions, max(sessions)) if sessions else []
        body["charts"] = charts
        first = names[0]
        if charts.get(first):
            body["session_date"] = body.get("session_date") or max(rth_sessions(_bars(first)))
        body["series"] = charts.get(first) or []
        body["markers"] = _markers(str(body.get("session_date") or ""), first if symbol or len(names) == 1 else "")
        body.setdefault("paper_only", True)
        body.setdefault("symbols", [{"symbol": name} for name in names])
        return body
    except Exception:
        return {"ok": False, "enabled": False, "error": "spy_unavailable", "paper_only": True, "symbol": SYMBOL}


def _markers(session_date: str | None, symbol: str = "") -> list[dict]:
    if not session_date:
        return []
    rows = signals(symbol=symbol)["items"]
    markers = []
    for row in rows:
        if row.get("session_date") != session_date or int(row.get("shadow") or 0):
            continue
        if symbol and row.get("symbol") != symbol:
            continue
        if row.get("action") not in {"ENTER_LONG", "ENTER_SHORT", "EXIT"}:
            continue
        stamp = str(row.get("decision_at") or "")
        try:
            from app.spy.clock import NY

            clock = datetime.fromisoformat(stamp).astimezone(NY).strftime("%H:%M")
        except Exception:
            clock = stamp[11:16]
        markers.append({"t": clock, "kind": "entry" if str(row.get("action", "")).startswith("ENTER") else "exit", "price": row.get("price"), "reason": row.get("reason")})
    return markers


def signals(limit: int = 200, symbol: str = "") -> dict:
    if symbol:
        return {"items": _filtered("spy_signals", symbol, limit)}
    return {"items": _table("SELECT * FROM spy_signals ORDER BY id DESC LIMIT ?", limit)}


def trades(limit: int = 200, symbol: str = "") -> dict:
    if symbol:
        return {"items": _filtered("spy_trades", symbol, limit)}
    return {"items": _table("SELECT * FROM spy_trades ORDER BY id DESC LIMIT ?", limit)}


def daily() -> dict:
    items = _table("SELECT * FROM spy_days ORDER BY session_date", None)
    equity = Decimal(0)
    curve = []
    for row in items:
        pnl = row.get("pnl")
        if pnl not in {None, ""}:
            equity += Decimal(str(pnl))
        curve.append({**row, "cumulative_pnl": str(equity.quantize(Decimal("0.01")))})
    return {"items": curve}


def _filtered(table: str, symbol: str, limit: int) -> list[dict]:
    if table not in {"spy_signals", "spy_trades"}:
        return []
    return _table(f"SELECT * FROM {table} WHERE symbol = ? ORDER BY id DESC LIMIT ?", limit, symbol)


def _table(sql: str, limit: int | None, symbol: str = "") -> list[dict]:
    cfg = load_config()
    conn = connect_read(cfg.db)
    if conn is None:
        return []
    try:
        if limit is None:
            rows = conn.execute(sql).fetchall()
        elif symbol:
            rows = conn.execute(sql, (symbol, limit)).fetchall()
        else:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [_row(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


def register(app: FastAPI) -> None:
    @app.get("/api/v1/spy/status")
    def spy_status(symbol: str = ""):
        return status(symbol.strip().upper())

    @app.get("/api/v1/spy/trades")
    def spy_trades(symbol: str = ""):
        try:
            return trades(symbol=symbol.strip().upper())
        except Exception:
            return {"items": []}

    @app.get("/api/v1/spy/daily")
    def spy_daily():
        try:
            return daily()
        except Exception:
            return {"items": []}

    @app.get("/api/v1/spy/signals")
    def spy_signals(symbol: str = ""):
        try:
            return signals(symbol=symbol.strip().upper())
        except Exception:
            return {"items": []}
