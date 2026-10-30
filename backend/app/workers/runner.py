from __future__ import annotations

import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import structlog

from app.config.settings import get_settings
from app.control.halt import is_halted
from app.core.sessions import SCAN_PHASES, phase_at
from app.models.db import configure_database, init_db
from app.ops.lock import acquire_single_worker_lock
from app.ops.paths import HEARTBEAT, MONITOR_HEARTBEAT, RECONCILE_HEARTBEAT, WORKER_LOCK
from app.ops.safety import assert_boot_safe
from app.workers.loops import monitor_once, reconcile_once, record_scan_blocked, scan_once

log = structlog.get_logger()
_stop = False
_lock_handle = None


def _line_buffered() -> None:
    """Stdout is line-buffered so a kill between steps still leaves the last log line."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(line_buffering=True)
        except (OSError, ValueError):
            continue
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def _log_cgroup() -> None:
    """memory.max, memory.current, memory.peak, and oom_kill. Missing files log nulls."""
    try:
        from app.ops.memory import cgroup_fields

        log.info("worker.cgroup", service="worker", **cgroup_fields())
    except Exception as exc:
        log.info("worker.cgroup", service="worker", readable=False, error=type(exc).__name__)
    _line_buffered()


def beat(path, phase: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
    temporary.write_text(f"{datetime.now(timezone.utc).isoformat()} {phase}\n", encoding="utf-8")
    temporary.replace(path)


def worker_process_alive(path, *, limit: int = 180, now: float | None = None) -> bool:
    """Same rule as the Docker healthcheck on worker_heartbeat.txt.

    This is process liveness only. A failed account read must not make it false.
    """
    if not path.exists():
        return False
    moment = time.time() if now is None else now
    return moment - path.stat().st_mtime < limit


def cycle_plan(*, stop: bool, halted: bool, phase: str, scan_due: bool, monitor_due: bool, reconcile_due: bool) -> dict[str, bool]:
    """New entries stop on shutdown or halt. Protective monitoring still runs."""
    if stop:
        return {"reconcile": False, "scan": False, "monitor": True}
    return {
        "reconcile": reconcile_due,
        "scan": (not halted) and phase in SCAN_PHASES and scan_due,
        "monitor": monitor_due,
    }


def _request_stop(signum, _frame) -> None:
    global _stop
    _stop = True
    log.info("worker.shutdown", signal=signum, service="worker")


def record_monitor_result(ok: bool, moment: datetime | None = None) -> None:
    """Remember whether the protective pass finished. A failure does not move last_success."""
    from app.ops.paths import MONITOR_CYCLE

    moment = moment or datetime.now(timezone.utc)
    prior: dict = {}
    if MONITOR_CYCLE.exists():
        try:
            loaded = json.loads(MONITOR_CYCLE.read_text(encoding="utf-8"))
            prior = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            prior = {}
    payload = {
        "last_attempt": moment.isoformat(),
        "last_success": moment.isoformat() if ok else prior.get("last_success"),
        "ok": ok,
    }
    MONITOR_CYCLE.parent.mkdir(parents=True, exist_ok=True)
    temporary = MONITOR_CYCLE.with_suffix(f".{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(MONITOR_CYCLE)


def run_monitor_cycle(settings, phase: str) -> bool:
    """One protective pass.

    worker_heartbeat.txt is rewritten either way so Docker sees a live process.
    last_success moves only when the pass finishes.
    """
    try:
        monitor_once(settings)
        ok = True
    except Exception as exc:
        from app.ops.failure import log_failure

        log_failure("worker.monitor_failed", exc, service="monitor")
        ok = False
    phase_name = "HALTED" if is_halted() else phase
    beat(HEARTBEAT, phase_name)
    beat(MONITOR_HEARTBEAT, phase)
    record_monitor_result(ok)
    return ok


def _monitor_forever(settings, calendar, interval: int) -> None:
    """Exits and liveness do not wait for a long scan."""
    while not _stop:
        now = datetime.now(timezone.utc)
        phase = phase_at(now, calendar)
        run_monitor_cycle(settings, phase)
        time.sleep(interval)


def run_forever() -> None:
    global _lock_handle
    _line_buffered()
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    assert_boot_safe(settings)
    _lock_handle = acquire_single_worker_lock(WORKER_LOCK)
    configure_database(settings)
    init_db(settings)
    try:
        from app.options.day_floor import run_false_hard_stop_release_once

        run_false_hard_stop_release_once(settings)
    except Exception:
        pass
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    calendar = settings.calendar()
    trading = settings.trading()
    last_scan = 0.0
    last_reconcile = 0.0
    log.info("worker.started", service="worker", trading_mode="PAPER", paper_only=True)
    _log_cgroup()
    monitor = threading.Thread(target=_monitor_forever, args=(settings, calendar, trading.monitor_interval_seconds), name="monitor", daemon=True)
    monitor.start()
    while True:
        now = datetime.now(timezone.utc)
        phase = phase_at(now, calendar)
        halted = is_halted()
        beat(HEARTBEAT, "HALTED" if halted else phase)
        clock = time.monotonic()
        plan = cycle_plan(
            stop=_stop,
            halted=halted,
            phase=phase,
            scan_due=clock - last_scan >= trading.scan_interval_seconds,
            monitor_due=False,
            reconcile_due=clock - last_reconcile >= trading.scan_interval_seconds,
        )
        if plan["reconcile"]:
            try:
                reconcile_once(settings)
            except Exception:
                log.warning("worker.reconcile_failed", service="reconciliation")
            beat(RECONCILE_HEARTBEAT, phase)
            last_reconcile = clock
        if plan["scan"]:
            try:
                scan_once(settings, now)
            except Exception as exc:
                from app.ops.failure import log_failure

                log_failure("worker.scan_failed", exc, service="worker")
                record_scan_blocked(now, f"שגיאה פנימית בסריקה: {type(exc).__name__}: {str(exc)[:200]}")
            last_scan = clock
        if _stop:
            monitor.join(timeout=20)
            log.info("worker.stopped", service="worker")
            return
        time.sleep(min(5, trading.monitor_interval_seconds))


if __name__ == "__main__":
    run_forever()
