"""Read-only day-floor / monitor diagnostics for ops. No trading side effects."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_DIAG_LOG_LOCK = threading.Lock()
_last_diag_log_at = 0.0
_last_diag_fingerprint: str | None = None
_DIAG_LOG_INTERVAL = 60.0

_CYCLE_LOG_LOCK = threading.Lock()
_last_cycle_log_at = 0.0
_CYCLE_LOG_INTERVAL = 60.0

_WATCHDOG_LOCK = threading.Lock()


def diag_path() -> Path:
    from app.ops.paths import DAY_FLOOR_DIAG

    return DAY_FLOOR_DIAG


def write_day_floor_diag(payload: dict) -> None:
    """Atomically write the latest open-session floor diag snapshot."""
    path = diag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.setdefault("written_at", datetime.now(timezone.utc).isoformat())
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(body, default=str), encoding="utf-8")
    temporary.replace(path)


def maybe_log_day_floor_diag(payload: dict) -> None:
    """Structlog once per 60s or when the fingerprint changes."""
    global _last_diag_log_at, _last_diag_fingerprint
    fingerprint = _fingerprint(payload)
    now = time.monotonic()
    with _DIAG_LOG_LOCK:
        changed = fingerprint != _last_diag_fingerprint
        due = (now - _last_diag_log_at) >= _DIAG_LOG_INTERVAL
        if not changed and not due:
            return
        _last_diag_log_at = now
        _last_diag_fingerprint = fingerprint
    try:
        import structlog

        log = structlog.get_logger()
        log.info("worker.day_floor_diag", service="monitor", **_log_fields(payload))
    except Exception:
        pass


def should_log_monitor_cycle(*, force: bool = False) -> bool:
    """True at most once per 60s, or when force (e.g. failure / hang)."""
    global _last_cycle_log_at
    now = time.monotonic()
    with _CYCLE_LOG_LOCK:
        if force or _last_cycle_log_at == 0.0 or (now - _last_cycle_log_at) >= _CYCLE_LOG_INTERVAL:
            _last_cycle_log_at = now
            return True
        return False


def log_monitor_cycle(*, phase: str, ok: bool, duration_ms: int, event: str) -> None:
    try:
        import structlog

        log = structlog.get_logger()
        log.info(
            "worker.monitor_cycle",
            service="monitor",
            event=event,
            phase=phase,
            ok=ok,
            duration_ms=duration_ms,
        )
    except Exception:
        pass


def arm_monitor_watchdog(timeout_seconds: float = 120.0) -> tuple[threading.Timer | None, dict]:
    """If a cycle runs longer than timeout, log the monitor thread stack once for this arming."""
    state = {"fired": False}

    def _fire() -> None:
        with _WATCHDOG_LOCK:
            if state["fired"]:
                return
            state["fired"] = True
        try:
            import sys
            import traceback

            import structlog

            frames = sys._current_frames()
            stacks: dict[str, str] = {}
            for thread in threading.enumerate():
                frame = frames.get(thread.ident)
                if frame is None:
                    continue
                name = thread.name or ""
                if name != "monitor" and not name.startswith("monitor"):
                    continue
                stacks[name] = "".join(traceback.format_stack(frame))
            if not stacks:
                # Fall back to the thread that armed the watchdog (the monitor loop).
                current = threading.current_thread()
                frame = frames.get(current.ident)
                if frame is not None:
                    stacks[current.name or "current"] = "".join(traceback.format_stack(frame))
            log = structlog.get_logger()
            clipped = {name: body[-4000:] for name, body in stacks.items()}
            log.warning(
                "worker.monitor_watchdog",
                service="monitor",
                timeout_seconds=timeout_seconds,
                stacks=clipped,
            )
        except Exception:
            pass

    try:
        timer = threading.Timer(timeout_seconds, _fire)
        timer.daemon = True
        timer.start()
        return timer, state
    except Exception:
        return None, state


def cancel_monitor_watchdog(timer: threading.Timer | None) -> None:
    if timer is None:
        return
    try:
        timer.cancel()
    except Exception:
        pass


def collect_day_floor_snapshot() -> dict:
    """Assemble the read-only /diag/day-floor payload. Never raises into the caller."""
    from app.ops.paths import (
        DATA_DIR,
        HEARTBEAT,
        MONITOR_CYCLE,
        MONITOR_HEARTBEAT,
    )
    from app.options.day_floor import FALSE_HARD_STOP_RELEASE_DATE, _release_marker_path, store_path

    now = datetime.now(timezone.utc)
    store = _safe_json(store_path())
    marker = _release_marker_path()
    marker_info = _file_meta(marker, now)
    if marker.exists():
        try:
            marker_info["content_preview"] = marker.read_text(encoding="utf-8")[:200]
        except OSError:
            marker_info["content_preview"] = None
    account_path = DATA_DIR / "account_read.json"
    return {
        "as_of": now.isoformat(),
        "session_date_target": FALSE_HARD_STOP_RELEASE_DATE,
        "day_floor_store": store,
        "release_marker": marker_info,
        "monitor_cycle": _safe_json(MONITOR_CYCLE),
        "monitor_heartbeat": _text_file_meta(MONITOR_HEARTBEAT, now),
        "worker_heartbeat": _text_file_meta(HEARTBEAT, now),
        "account_read": _safe_json(account_path),
        "day_latch_released_audit": _latch_released_audit(),
        "day_floor_diag": _safe_json(diag_path()),
    }


def build_floor_cycle_diag(
    *,
    moment: datetime,
    session_open: bool | None,
    read_ok: bool | None,
    already_closed: bool,
    pnl: Decimal | None,
    equity: Decimal | None,
    flatten_reason: str | None,
    maybe_release_called: bool,
    maybe_release_returned: bool | None,
    release_exception: str | None,
) -> dict:
    return {
        "moment": moment.isoformat() if moment.tzinfo else moment.replace(tzinfo=timezone.utc).isoformat(),
        "session_open": session_open,
        "read_ok": read_ok,
        "already_closed": already_closed,
        "pnl": None if pnl is None else str(pnl),
        "equity": None if equity is None else str(equity),
        "day_flatten_reason": flatten_reason,
        "maybe_release_called": maybe_release_called,
        "maybe_release_returned": maybe_release_returned,
        "release_exception": release_exception,
    }


def _safe_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(body, (dict, list)):
        return body
    return None


def _file_meta(path: Path, now: datetime) -> dict:
    if not path.exists():
        return {"exists": False, "mtime": None, "age_seconds": None, "path_name": path.name}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {"exists": True, "mtime": None, "age_seconds": None, "path_name": path.name}
    stamp = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return {
        "exists": True,
        "mtime": stamp.isoformat(),
        "age_seconds": max(0, int(now.timestamp() - stamp.timestamp())),
        "path_name": path.name,
    }


def _text_file_meta(path: Path, now: datetime) -> dict:
    meta = _file_meta(path, now)
    if not meta["exists"]:
        meta["content"] = None
        return meta
    try:
        meta["content"] = path.read_text(encoding="utf-8")[:240].strip()
    except OSError:
        meta["content"] = None
    return meta


def _latch_released_audit() -> dict:
    try:
        from sqlalchemy import select

        from app.models.db import SessionLocal, database_ready
        from app.models.tables import AuditLog
        from app.options.day_floor import FALSE_HARD_STOP_RELEASE_DATE

        if not database_ready() or SessionLocal is None:
            return {"available": False, "exists": False}
        entity_id = f"latch-rel2:{FALSE_HARD_STOP_RELEASE_DATE}"[:36]
        with SessionLocal() as session:
            row = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "DAY_LATCH_RELEASED",
                    AuditLog.entity == "day_floor",
                    AuditLog.entity_id == entity_id,
                )
            )
            if row is None:
                return {"available": True, "exists": False}
            created = row.created_at
            if created is not None and getattr(created, "tzinfo", None) is None:
                created = created.replace(tzinfo=timezone.utc)
            return {
                "available": True,
                "exists": True,
                "entity_id": row.entity_id,
                "created_at": created.isoformat() if created is not None else None,
                "payload": row.payload if isinstance(row.payload, dict) else {},
            }
    except Exception as exc:
        return {"available": False, "exists": False, "error": type(exc).__name__}


def _fingerprint(payload: dict) -> str:
    keys = (
        "session_open",
        "read_ok",
        "already_closed",
        "pnl",
        "equity",
        "day_flatten_reason",
        "maybe_release_called",
        "maybe_release_returned",
        "release_exception",
    )
    return "|".join(f"{key}={payload.get(key)!s}" for key in keys)


def _log_fields(payload: dict) -> dict:
    return {
        "moment": payload.get("moment"),
        "session_open": payload.get("session_open"),
        "read_ok": payload.get("read_ok"),
        "already_closed": payload.get("already_closed"),
        "pnl": payload.get("pnl"),
        "equity": payload.get("equity"),
        "day_flatten_reason": payload.get("day_flatten_reason"),
        "maybe_release_called": payload.get("maybe_release_called"),
        "maybe_release_returned": payload.get("maybe_release_returned"),
        "release_exception": payload.get("release_exception"),
    }
