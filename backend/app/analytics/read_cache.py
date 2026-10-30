"""Short-lived memory cache for heavy dashboard reads.

The trading loop is untouched. This only remembers GET results so the next
identical request, often a few seconds later from the same screen, does not
rebuild the trade history or call Alpaca again.

A failed or unavailable read is never stored. If a good payload is still
here, that read is served again with stale=true and the time it was read.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import NamedTuple

REPORT_TTL_SECONDS = 45
RISK_TTL_SECONDS = 60
FINANCE_TTL_SECONDS = 45
BROKER_TTL_SECONDS = 8
# Fill pages are the slow part of report/trades. A new fill drops this entry,
# so a longer success TTL does not hide a trade that just happened.
FILL_TTL_SECONDS = 180

# A new fill, an open/close event, or a write to the day's latch drops these families.
_HEAVY_FAMILIES = {"trades", "learning-trades", "learning-lab", "finance", "risk", "fills", "broker"}


class _Entry(NamedTuple):
    expires: float
    value: object
    stored_at: float


class ReadCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple, _Entry] = {}
        self._flights: dict[tuple, threading.Event] = {}

    def get(self, key: tuple):
        with self._lock:
            item = self._items.get(key)
            if item is None or item.expires <= time.monotonic():
                return None
            return copy.deepcopy(item.value)

    def get_stale(self, key: tuple):
        """Last good payload even after TTL / invalidate_heavy. Never hits the broker."""
        return self._stale(key)

    def set(self, key: tuple, value: object, ttl: float) -> None:
        if ttl <= 0 or not _keep(value):
            return
        with self._lock:
            self._items[key] = _Entry(time.monotonic() + ttl, copy.deepcopy(value), time.time())

    def keep_or_stale(self, key: tuple, value: object, ttl: float):
        """Store a good payload. On a failed one, return the last good read instead."""
        if _keep(value):
            self.set(key, value, ttl)
            return value
        if isinstance(value, dict) and value.get("stale") is True:
            return value
        stale = self._stale(key)
        if stale is not None:
            return stale
        return value

    def get_or_compute(self, key: tuple, ttl: float, produce: Callable[[], object]):
        """One computation for concurrent callers. Failures are not stored."""
        cached = self.get(key)
        if cached is not None:
            return cached
        with self._lock:
            item = self._items.get(key)
            if item is not None and item.expires > time.monotonic():
                return copy.deepcopy(item.value)
            flight = self._flights.get(key)
            if flight is None:
                flight = threading.Event()
                self._flights[key] = flight
                leader = True
            else:
                leader = False
        if not leader:
            flight.wait()
            cached = self.get(key)
            if cached is not None:
                return cached
            return self.keep_or_stale(key, produce(), ttl)
        try:
            value = produce()
        except Exception:
            raise
        else:
            return self.keep_or_stale(key, value, ttl)
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.set()

    def invalidate_heavy(self) -> None:
        """End the fresh window. The last good payload stays for a failed refresh."""
        with self._lock:
            expired = time.monotonic()
            for key in list(self._items):
                if key and key[0] in _HEAVY_FAMILIES:
                    item = self._items[key]
                    self._items[key] = _Entry(expired, item.value, item.stored_at)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _stale(self, key: tuple):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value = copy.deepcopy(item.value)
            stored_at = item.stored_at
        if not isinstance(value, dict):
            return None
        value["stale"] = True
        value["as_of"] = datetime.fromtimestamp(stored_at, timezone.utc).isoformat()
        return value


def _keep(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("available") is False:
        return False
    if value.get("stale") is True:
        return False
    return True


read_cache = ReadCache()


def invalidate_heavy_reads() -> None:
    read_cache.invalidate_heavy()
