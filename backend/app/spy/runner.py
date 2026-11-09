"""SPY worker process. It never takes the options worker lock."""

from __future__ import annotations

import fcntl
import signal
import time
from datetime import datetime, timedelta, timezone

import structlog

from app.spy.broker import SpyBroker
from app.spy.config import SYMBOL, SpyConfig, load_config, startup_mode
from app.spy.engine import Engine, bars_from_rows, write_idle_status
from app.spy.store import Store

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


def _sync(broker: SpyBroker, store: Store, symbol: str, now: datetime) -> list:
    latest = store.latest_bar_start(symbol)
    start = latest or (now - timedelta(days=45)).astimezone(timezone.utc).isoformat()
    try:
        rows = broker.minute_bars(symbol, start, now.astimezone(timezone.utc).isoformat())
    except Exception as exc:
        log.warning("spy.bars_failed", symbol=symbol, error=type(exc).__name__)
        rows = []
    if rows:
        store.upsert_bars(
            symbol,
            [
                (
                    symbol,
                    str(row.get("t")),
                    str(row.get("o")),
                    str(row.get("h")),
                    str(row.get("l")),
                    str(row.get("c")),
                    str(row.get("v") or 0),
                )
                for row in rows
                if row.get("t")
            ],
        )
    stored = []
    for row in store.bars(symbol):
        stored.append(
            {
                "t": row["start"],
                "o": row["open"],
                "h": row["high"],
                "l": row["low"],
                "c": row["close"],
                "v": row["volume"],
            }
        )
    return bars_from_rows(stored)


def run(cfg: SpyConfig | None = None) -> None:
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    current = cfg or load_config()
    mode = startup_mode(current)
    log.info("spy.boot", **{key: value for key, value in current.public().items() if key != "shadow"}, mode=mode, shadow=list(current.shadow))
    if mode != "running":
        while not _stop:
            write_idle_status(current, mode)
            time.sleep(30)
        return
    lock = _lock(current.db.with_name("spy.lock"))
    if lock is None:
        log.warning("spy.lock_held")
        return
    broker = SpyBroker(current)
    store = Store(current.db)
    engine = Engine(current, store, broker)
    try:
        while not _stop:
            now = datetime.now(timezone.utc)
            try:
                account = broker.account()
                clock = broker.clock()
                symbols = [SYMBOL, *current.shadow]
                bars = {symbol: _sync(broker, store, symbol, now) for symbol in symbols}
                engine.cycle(now, bars, clock, account)
            except Exception as exc:
                log.warning("spy.cycle_failed", error=type(exc).__name__)
                try:
                    current.db.with_name("spy_heartbeat.txt").write_text(now.astimezone(timezone.utc).isoformat(), encoding="utf-8")
                except Exception:
                    pass
            time.sleep(15)
    finally:
        broker.close()
        store.close()
        lock.close()


if __name__ == "__main__":
    run()
