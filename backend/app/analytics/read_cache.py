"""Short-lived memory cache for heavy dashboard reads.

The trading loop is untouched. This only remembers GET results so the next
identical request, often a few seconds later from the same screen, does not
rebuild the trade history or call Alpaca again.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable

REPORT_TTL_SECONDS = 45
RISK_TTL_SECONDS = 30
FINANCE_TTL_SECONDS = 45
BROKER_TTL_SECONDS = 8
FILL_TTL_SECONDS = 45

# A new fill or an open/close event drops these families so the next read is fresh.
_HEAVY_FAMILIES = {"trades", "learning-trades", "learning-lab", "finance", "risk", "fills", "broker"}


class ReadCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[tuple, tuple[float, object]] = {}
        self._flights: dict[tuple, threading.Event] = {}

    def get(self, key: tuple):
        with self._lock:
            item = self._items.get(key)
            if item is None or item[0] <= time.monotonic():
                if item is not None:
                    self._items.pop(key, None)
                return None
            return copy.deepcopy(item[1])

    def set(self, key: tuple, value: object, ttl: float) -> None:
        if ttl <= 0 or not _keep(value):
            return
        with self._lock:
            self._items[key] = (time.monotonic() + ttl, copy.deepcopy(value))

    def get_or_compute(self, key: tuple, ttl: float, produce: Callable[[], object]):
        """One computation for concurrent callers. Failures are not stored."""
        cached = self.get(key)
        if cached is not None:
            return cached
        with self._lock:
            cached_item = self._items.get(key)
            if cached_item is not None and cached_item[0] > time.monotonic():
                return copy.deepcopy(cached_item[1])
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
            return produce()
        try:
            value = produce()
        except Exception:
            raise
        else:
            self.set(key, value, ttl)
            return value
        finally:
            with self._lock:
                self._flights.pop(key, None)
            flight.set()

    def invalidate_heavy(self) -> None:
        with self._lock:
            for key in list(self._items):
                if key and key[0] in _HEAVY_FAMILIES:
                    self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def _keep(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("available") is False:
        return False
    return True


read_cache = ReadCache()


def invalidate_heavy_reads() -> None:
    read_cache.invalidate_heavy()
