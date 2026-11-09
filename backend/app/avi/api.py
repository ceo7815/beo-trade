"""Read-only Avi views plus the kill flag. This process does not place orders."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI, Request

from app.avi.config import kill_flag_path, load_config, startup_mode, status_path
from app.avi.store import connect_read


def _row(item) -> dict:
    return {key: item[key] for key in item.keys()}


def _idle() -> dict:
    cfg = load_config()
    mode = startup_mode(cfg)
    return {
        "ok": True,
        "enabled": cfg.enabled,
        "mode": mode if cfg.enabled else "disabled",
        "paper_only": True,
        "symbol": "QQQ",
        "account_last4": cfg.account_last4,
        "account_match": False,
        "signal": {"state": "none", "reason": mode, "at": None, "price": None, "vix": None, "detail": None},
        "position": {"open": False},
        "day_pnl": None,
        "week_pnl": None,
        "bankroll": None if cfg.bankroll is None else str(cfg.bankroll),
        "brakes": {
            "kill": kill_flag_path().exists(),
            "weekly": False,
            "daily": False,
            "entries_today": 0,
            "entries_max": 1,
            "account": mode == "not_configured",
            "halt_reason": "",
        },
        "_from_file": False,
    }


def snapshot() -> dict:
    path = status_path()
    if not path.exists():
        body = _idle()
    else:
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(body, dict):
                body = _idle()
            else:
                body["_from_file"] = True
        except (OSError, json.JSONDecodeError):
            body = {"ok": False, "enabled": False, "error": "avi_unavailable", "paper_only": True, "_from_file": False}
    heartbeat = path.with_name("avi_heartbeat.txt")
    if heartbeat.exists():
        age = max(0, int(datetime.now(timezone.utc).timestamp() - heartbeat.stat().st_mtime))
        body["heartbeat_age_seconds"] = age
        body["stale"] = age > 180
    body.setdefault("paper_only", True)
    brakes = body.setdefault("brakes", {})
    if kill_flag_path().exists():
        brakes["kill"] = True
    return body


def ready() -> bool:
    """True once the engine flag is on. A status file written by the process wins."""
    body = snapshot()
    if body.get("_from_file"):
        return body.get("enabled") is True
    return load_config().enabled is True


def trades(limit: int = 200) -> dict:
    cfg = load_config()
    conn = connect_read(cfg.db)
    if conn is None:
        return {"items": []}
    try:
        rows = conn.execute("SELECT * FROM avi_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"items": [_row(row) for row in rows]}
    except Exception:
        return {"items": []}
    finally:
        conn.close()


def set_kill(armed: bool) -> dict:
    path = kill_flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if armed:
        path.write_text("1", encoding="utf-8")
    elif path.exists():
        path.unlink()
    return {"killed": path.exists(), "paper_only": True}


def _public(body: dict) -> dict:
    shown = dict(body)
    shown.pop("_from_file", None)
    return shown


def register(app: FastAPI) -> None:
    @app.get("/api/v1/avi/status")
    def avi_status():
        try:
            return _public(snapshot())
        except Exception:
            return {"ok": False, "enabled": False, "error": "avi_unavailable", "paper_only": True}

    @app.get("/api/v1/avi/trades")
    def avi_trades():
        try:
            return trades()
        except Exception:
            return {"items": []}

    @app.post("/api/v1/avi/kill")
    async def avi_kill(request: Request):
        armed = True
        try:
            payload = await request.json()
            if isinstance(payload, dict) and "killed" in payload:
                armed = bool(payload["killed"])
        except Exception:
            armed = True
        return set_kill(armed)
