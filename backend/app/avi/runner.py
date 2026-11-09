"""Avi worker process. It never takes the options worker lock and never opens spy.sqlite."""

from __future__ import annotations

import fcntl
import signal
import time
from datetime import datetime, timedelta, timezone

import structlog

from app.avi.broker import AviBroker
from app.avi.config import SYMBOL, AviConfig, load_config, startup_mode
from app.avi.engine import Engine, bars_from_rows, write_idle_status
from app.avi.store import Store
from app.avi.vix import read_vix

log = structlog.get_logger()
_stop = False


def _handle_stop(signum, _frame) -> None:
    del signum
    global _stop
    _stop = True


def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _sync(broker: AviBroker, store: Store, now: datetime) -> list:
    latest = store.latest_bar_start(SYMBOL)
    start = latest or (now - timedelta(days=40)).astimezone(timezone.utc).isoformat()
    try:
        rows = broker.minute_bars(SYMBOL, start, now.astimezone(timezone.utc).isoformat())
    except Exception as exc:
        log.warning("avi.bars_failed", error=type(exc).__name__)
        rows = []
    if rows:
        store.upsert_bars(
            [
                (
                    SYMBOL,
                    str(row.get("t")),
                    str(row.get("o")),
                    str(row.get("h")),
                    str(row.get("l")),
                    str(row.get("c")),
                    str(row.get("v") or 0),
                )
                for row in rows
                if row.get("t")
            ]
        )
    stored = [
        {"t": row["start"], "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]}
        for row in store.bars(SYMBOL)
    ]
    return bars_from_rows(stored)


def run(cfg: AviConfig | None = None) -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    current = cfg or load_config()
    mode = startup_mode(current)
    log.info("avi.boot", **current.public(), mode=mode)
    if mode != "running":
        while not _stop:
            write_idle_status(current, mode)
            time.sleep(30)
        return
    lock = _lock(current.db.with_name("avi.lock"))
    if lock is None:
        log.warning("avi.lock_held")
        return
    broker = AviBroker(current)
    store = Store(current.db)
    engine = Engine(current, store, broker)
    try:
        while not _stop:
            now = datetime.now(timezone.utc)
            try:
                account = broker.account()
                clock = broker.clock()
                try:
                    vix, vix_source = read_vix(now, store)
                except Exception:
                    vix, vix_source = None, ""
                bars = _sync(broker, store, now)
                engine.cycle(now, bars, vix, clock, account, vix_source=vix_source)
            except Exception as exc:
                log.warning("avi.cycle_failed", error=type(exc).__name__)
                try:
                    current.db.with_name("avi_heartbeat.txt").write_text(now.isoformat(), encoding="utf-8")
                except Exception:
                    pass
            time.sleep(15)
    finally:
        broker.close()
        store.close()
        lock.close()


if __name__ == "__main__":
    run()
