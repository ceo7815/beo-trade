from __future__ import annotations

import signal
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
from app.workers.loops import monitor_once, reconcile_once, scan_once

log = structlog.get_logger()
_stop = False
_lock_handle = None


def beat(path, phase: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{datetime.now(timezone.utc).isoformat()} {phase}\n", encoding="utf-8")
    temporary.replace(path)


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


def run_forever() -> None:
    global _lock_handle
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
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    calendar = settings.calendar()
    trading = settings.trading()
    last_scan = 0.0
    last_monitor = 0.0
    last_reconcile = 0.0
    log.info("worker.started", service="worker", trading_mode="PAPER", paper_only=True)
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
            monitor_due=clock - last_monitor >= trading.monitor_interval_seconds,
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
            except Exception:
                log.warning("worker.scan_failed", service="worker")
            last_scan = clock
        if plan["monitor"]:
            try:
                monitor_once(settings)
            except Exception:
                log.warning("worker.monitor_failed", service="monitor")
            beat(MONITOR_HEARTBEAT, phase)
            last_monitor = clock
        if _stop:
            log.info("worker.stopped", service="worker")
            return
        time.sleep(min(5, trading.monitor_interval_seconds))


if __name__ == "__main__":
    run_forever()
