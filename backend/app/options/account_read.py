"""Consecutive Alpaca account reads. This does not change a stop, a trail, or the kill threshold.

Three missed reads block new buys. A later good read clears that block and the
open alert. The dollar kill is still decided by day_flatten_reason.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

FAILURES_BEFORE_BLOCK = 3
BLOCK_REASON = "ACCOUNT_DATA_UNAVAILABLE"


def store_path():
    from app.ops.paths import DATA_DIR

    return DATA_DIR / "account_read.json"


def apply_failure(state: dict | None, moment: datetime) -> dict:
    """One missed account read. The day-loss state is unknown from the first miss."""
    prior = state or _blank()
    failures = int(prior.get("consecutive_failures") or 0) + 1
    blocked = failures >= FAILURES_BEFORE_BLOCK
    was_blocked = bool(prior.get("blocked"))
    return {
        "consecutive_failures": failures,
        "last_success_at": prior.get("last_success_at"),
        "last_failure_at": _stamp(moment),
        "blocked": blocked,
        "alert_open": blocked or bool(prior.get("alert_open")),
        "status": "unknown",
        "newly_blocked": blocked and not was_blocked,
    }


def apply_success(state: dict | None, moment: datetime) -> dict:
    """A good read. The caller still has to evaluate the kill on this same snapshot."""
    prior = state or _blank()
    return {
        "consecutive_failures": 0,
        "last_success_at": _stamp(moment),
        "last_failure_at": prior.get("last_failure_at"),
        "blocked": False,
        "alert_open": False,
        "status": "ok",
        "alert_was_open": bool(prior.get("alert_open")),
    }


def entry_block_reason() -> str | None:
    """New buys only. An open position is not this function's concern."""
    if int(public_status().get("consecutive_failures") or 0) >= FAILURES_BEFORE_BLOCK:
        return BLOCK_REASON
    return None


def public_status() -> dict:
    raw = _read()
    return {
        "consecutive_failures": int(raw.get("consecutive_failures") or 0),
        "last_success_at": raw.get("last_success_at"),
        "last_failure_at": raw.get("last_failure_at"),
        "blocked": bool(raw.get("blocked")),
        "alert_open": bool(raw.get("alert_open")),
        "status": "unknown" if int(raw.get("consecutive_failures") or 0) else "ok",
    }


def note_failure(moment: datetime) -> dict:
    updated = apply_failure(_read(), moment)
    _write(updated)
    if updated.get("newly_blocked"):
        _emit(moment, resolved=False, failures=int(updated["consecutive_failures"]))
    return updated


def note_success(moment: datetime) -> dict:
    updated = apply_success(_read(), moment)
    _write(updated)
    if updated.get("alert_was_open"):
        _emit(moment, resolved=True, failures=0)
    return updated


def _blank() -> dict:
    return {
        "consecutive_failures": 0,
        "last_success_at": None,
        "last_failure_at": None,
        "blocked": False,
        "alert_open": False,
        "status": "ok",
    }


def _stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.isoformat()


def _persisted(state: dict) -> dict:
    return {
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_success_at": state.get("last_success_at"),
        "last_failure_at": state.get("last_failure_at"),
        "blocked": bool(state.get("blocked")),
        "alert_open": bool(state.get("alert_open")),
        "status": state.get("status") or "ok",
    }


def _read() -> dict:
    path = store_path()
    if not path.exists():
        return _blank()
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank()
    return body if isinstance(body, dict) else _blank()


def _write(state: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(_persisted(state)), encoding="utf-8")
    temporary.replace(path)


def _emit(moment: datetime, *, resolved: bool, failures: int) -> None:
    try:
        from app.models.db import database_ready, session_scope
        from app.models.tables import SystemEvent

        if not database_ready():
            return
        with session_scope() as session:
            session.add(
                SystemEvent(
                    level="info" if resolved else "warning",
                    event_type="ACCOUNT_DATA_RESOLVED" if resolved else BLOCK_REASON,
                    message="קריאת החשבון חזרה. הבלם היומי נבדק." if resolved else "אין נתוני חשבון מאלפקה. כניסות חדשות חסומות עד שהנתונים חוזרים.",
                    payload={"consecutive_failures": failures, "resolved": resolved},
                    created_at=moment,
                )
            )
            session.commit()
    except Exception:
        return
