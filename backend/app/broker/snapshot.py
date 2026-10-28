"""Shared Alpaca snapshot for the API process and the worker process.

Choice
------
``api`` and ``worker`` are separate processes (see ``docker-compose.yml``) that
share the ``beo_data`` volume. Redis is not part of this desk. SQLite is
already the worker's write path, so the snapshot is a JSON file
(``data/broker_snapshot.json``) rather than another database writer.

* Inside one process, concurrent callers join a single in-flight refresh
  (``threading.Event``). ``fcntl.flock`` is per-process on Linux, so it cannot
  coalesce threads.
* Across processes, the refresh holds ``broker_snapshot.lock``. The waiter
  re-reads the file and skips Alpaca when the other process just filled it.
* If the file cannot be created, the process keeps an in-memory snapshot and
  still coalesces its own callers.

The file holds account, positions, open orders, and the market clock. A refresh
loads every part that is older than the TTL, so a cycle makes at most one
clock request. The TTL is ``BROKER_SNAPSHOT_TTL_SECONDS``, clamped to 5–10
seconds (default 5).

``live/stream.py`` only peeks. It never opens Alpaca. While a screen is
connected, one daemon thread refreshes the file on the TTL so prices lag by
at most that window.

Exit and order code calls ``get_positions(fresh=True)`` (monitor sync and
``submit_exit_order``). That uses this same coalesced refresh and does not
accept a snapshot that has reached the TTL. A failed refresh leaves the last
value for the screen peek and raises to the exit caller.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

import structlog

from app.broker import metrics

log = structlog.get_logger()

PARTS = ("account", "positions", "open_orders", "clock")
_MISS = object()

_settings = None
_subscribers = 0
_screen_lock = threading.Lock()
_screen_stop = threading.Event()
_screen_thread: threading.Thread | None = None


class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.errors: dict[str, BaseException] = {}


class SnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mem: dict[str, tuple[float, object]] = {}
        self._flight: _Flight | None = None
        self._directory: Path | None = None
        self._disk_token: tuple | None = None
        self._disk_body: dict = {}
        self._now = time.time

    def isolate(self, path: Path | None) -> None:
        with self._lock:
            self._directory = path
            self._mem.clear()
            self._flight = None
            self._disk_token = None
            self._disk_body = {}
            self._now = time.time

    def now(self) -> float:
        return float(self._now())

    def peek(self, name: str):
        """Last stored value, including one past the TTL. Does not call Alpaca."""
        with self._lock:
            item = self._newest_unlocked(name)
        if item is None:
            return None
        metrics.note_hit()
        return copy.deepcopy(item[1])

    def ages(self) -> dict[str, float | None]:
        now = self.now()
        ages: dict[str, float | None] = {}
        with self._lock:
            for name in PARTS:
                item = self._newest_unlocked(name)
                ages[name] = None if item is None else round(max(0.0, now - item[0]), 3)
        return ages

    def stale_parts(self, ttl: float) -> list[str]:
        now = self.now()
        with self._lock:
            return [name for name in PARTS if not self._fresh_unlocked(name, now, ttl)]

    def prime(self, values: dict, *, at: float | None = None) -> None:
        stamp = self.now() if at is None else at
        stored = {name: (stamp, value) for name, value in values.items() if name in PARTS}
        with self._lock:
            self._mem.update(stored)
        self._merge_disk(stored)

    def invalidate(self, *names: str) -> None:
        """Mark parts expired but keep the last payload so a screen does not go blank."""
        expired = self.now() - 10_000
        updates: dict[str, tuple[float, object]] = {}
        with self._lock:
            for name in names:
                if name not in PARTS:
                    continue
                item = self._newest_unlocked(name)
                if item is None:
                    continue
                updates[name] = (expired, item[1])
            self._mem.update(updates)
        if updates:
            self._merge_disk(updates)

    def value(self, name: str, *, ttl: float, fetch, fresh: bool = False):
        """Return a snapshot younger than ``ttl``, refreshing through one in-flight call.

        ``fresh=True`` is the exit/order hook. It uses this same path: a value
        that has reached the TTL is refreshed, and a failed refresh raises
        instead of handing back the expired snapshot. ``fresh`` is part of the
        contract so a later non-fresh fallback cannot be wired into exits by
        accident; both paths refuse expired data today.
        """
        del fresh  # see docstring — expired data is never returned here
        cached = self._copy_if_fresh(name, ttl)
        if cached is not _MISS:
            metrics.note_hit()
            return cached
        return self._coalesce(name, ttl, fetch)

    def _coalesce(self, name: str, ttl: float, fetch):
        with self._lock:
            cached = self._copy_if_fresh_unlocked(name, ttl)
            if cached is not _MISS:
                metrics.note_hit()
                return cached
            leader = self._flight is None
            if leader:
                self._flight = _Flight()
            flight = self._flight
        if not leader:
            # Reads may sleep on Retry-After across several parts. Wait longer than that burst.
            if not flight.event.wait(120):
                raise TimeoutError("broker snapshot refresh timed out")
            cached = self._copy_if_fresh(name, ttl)
            if cached is not _MISS:
                metrics.note_hit()
                return cached
            error = flight.errors.get(name)
            if error is not None:
                raise error
            raise TimeoutError("broker snapshot refresh produced no data")
        try:
            return self._lead(name, ttl, fetch, flight)
        finally:
            with self._lock:
                if self._flight is flight:
                    self._flight = None
            flight.event.set()

    def _lead(self, name: str, ttl: float, fetch, flight: _Flight):
        try:
            with self._exclusive():
                cached = self._copy_if_fresh(name, ttl)
                if cached is not _MISS:
                    metrics.note_hit()
                    return cached
                now = self.now()
                with self._lock:
                    stale = [part for part in PARTS if not self._fresh_unlocked(part, now, ttl)]
                if name not in stale:
                    stale.append(name)
                metrics.note_miss()
                try:
                    loaded, errors = fetch(set(stale))
                except Exception as exc:
                    loaded, errors = {}, {part: exc for part in stale}
                if not isinstance(loaded, dict):
                    loaded = {}
                if not isinstance(errors, dict):
                    errors = {}
                stamp = self.now()
                stored: dict[str, tuple[float, object]] = {}
                for part, payload in loaded.items():
                    if part in PARTS and not isinstance(errors.get(part), BaseException):
                        stored[part] = (stamp, payload)
                if stored:
                    with self._lock:
                        self._mem.update(stored)
                    self._write_locked(stored)
                    self._disk_token = None
                flight.errors = {part: err for part, err in errors.items() if isinstance(err, BaseException)}
                for part in stale:
                    if part not in stored and part not in flight.errors:
                        flight.errors[part] = RuntimeError(f"broker snapshot missing {part}")
                self._log_refresh(stored, flight.errors)
            if name in flight.errors:
                raise flight.errors[name]
            cached = self._copy_if_fresh(name, ttl)
            if cached is _MISS:
                raise flight.errors.get(name) or RuntimeError("broker snapshot missing")
            return cached
        except Exception as exc:
            if name not in flight.errors:
                flight.errors[name] = exc
            raise

    def _log_refresh(self, stored: dict, errors: dict) -> None:
        counters = metrics.public_counters()
        fields = {
            "updated": sorted(stored),
            "clock_fetched": "clock" in stored,
            "failed": [f"{name}:{type(err).__name__}" for name, err in sorted(errors.items())],
            "calls_per_minute": counters.get("calls_per_minute"),
            "http_429": counters.get("http_429"),
            "http_5xx": counters.get("http_5xx"),
            "cache_hit_ratio": counters.get("cache_hit_ratio"),
        }
        if errors and not stored:
            log.warning("broker.snapshot_refresh_failed", **fields)
        else:
            log.info("broker.snapshot_refresh", **fields)

    def _copy_if_fresh(self, name: str, ttl: float):
        with self._lock:
            return self._copy_if_fresh_unlocked(name, ttl)

    def _copy_if_fresh_unlocked(self, name: str, ttl: float):
        item = self._newest_unlocked(name)
        if item is None or not self._stamp_fresh(item[0], ttl):
            return _MISS
        self._mem[name] = item
        return copy.deepcopy(item[1])

    def _fresh_unlocked(self, name: str, now: float, ttl: float) -> bool:
        item = self._newest_unlocked(name)
        return item is not None and now - item[0] < ttl

    def _stamp_fresh(self, stamp: float, ttl: float) -> bool:
        return self.now() - stamp < ttl

    def _newest_unlocked(self, name: str):
        best = None
        for item in (self._mem.get(name), self._disk_part(name)):
            if item is None:
                continue
            if best is None or item[0] >= best[0]:
                best = item
        return best

    def _disk_part(self, name: str):
        raw = self._disk_body_loaded().get(name)
        if not isinstance(raw, dict) or "value" not in raw:
            return None
        try:
            stamp = float(raw.get("fetched_at"))
        except (TypeError, ValueError):
            return None
        return stamp, raw["value"]

    def _disk_body_loaded(self) -> dict:
        path = self._snapshot_path()
        try:
            stat = path.stat()
            token = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            self._disk_token = None
            self._disk_body = {}
            return {}
        if token == self._disk_token:
            return self._disk_body
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        self._disk_token = token
        self._disk_body = raw if isinstance(raw, dict) else {}
        return self._disk_body

    def _merge_disk(self, updates: dict[str, tuple[float, object]]) -> None:
        if not updates:
            return
        try:
            path = self._snapshot_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._exclusive():
                self._write_locked(updates)
            self._disk_token = None
        except (OSError, TypeError, ValueError):
            log.warning("broker.snapshot_persist_failed")

    def _write_locked(self, updates: dict[str, tuple[float, object]]) -> None:
        """Replace the snapshot file. Caller holds the cross-process lock."""
        path = self._snapshot_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        body: dict = {}
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                body = current
        except (OSError, ValueError):
            body = {}
        for name, (stamp, payload) in updates.items():
            body[name] = {"fetched_at": stamp, "value": payload}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, default=str), encoding="utf-8")
        temporary.replace(path)

    def _exclusive(self):
        try:
            path = self._lock_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            return _FileLock(path)
        except OSError:
            from contextlib import nullcontext

            log.warning("broker.snapshot_lock_unavailable")
            return nullcontext()

    def _snapshot_path(self) -> Path:
        return self._directory_path() / "broker_snapshot.json"

    def _lock_path(self) -> Path:
        return self._directory_path() / "broker_snapshot.lock"

    def _directory_path(self) -> Path:
        if self._directory is not None:
            return self._directory
        from app.ops.paths import DATA_DIR

        return DATA_DIR


class _FileLock:
    def __init__(self, path: Path) -> None:
        import fcntl

        self._fcntl = fcntl
        self._path = path
        self._handle = None

    def __enter__(self):
        self._handle = self._path.open("a+")
        self._fcntl.flock(self._handle.fileno(), self._fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args) -> None:
        if self._handle is not None:
            self._fcntl.flock(self._handle.fileno(), self._fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


cache = SnapshotCache()


def remember_settings(settings) -> None:
    global _settings
    _settings = settings


def isolate(path: Path | None) -> None:
    global _settings
    _settings = None
    cache.isolate(path)


def peek(name: str):
    return cache.peek(name)


def prime(values: dict, *, at: float | None = None) -> None:
    cache.prime(values, at=at)


def invalidate(*names: str) -> None:
    cache.invalidate(*names)


def read_shared(name: str, *, fresh: bool = False):
    settings = _current_settings()
    ttl = _ttl(settings)
    return cache.value(name, ttl=ttl, fetch=lambda parts: _fetch_parts(settings, parts), fresh=fresh)


def refresh_due(settings) -> None:
    """One refresh cycle: every stale part, and therefore at most one clock call."""
    remember_settings(settings)
    from app.integrations.secrets import resolve_secret

    key, _ = resolve_secret(settings, "alpaca_api_key")
    secret, _ = resolve_secret(settings, "alpaca_api_secret")
    if not key or not secret:
        return
    ttl = _ttl(settings)
    stale = cache.stale_parts(ttl)
    if not stale:
        return
    cache.value(stale[0], ttl=ttl, fetch=lambda parts: _fetch_parts(settings, parts))


def public_broker_status() -> dict:
    try:
        ttl = _ttl(_current_settings())
    except Exception:
        ttl = 5.0
    try:
        body = metrics.public_counters()
    except Exception:
        body = {
            "calls_per_minute": 0,
            "http_429": 0,
            "http_5xx": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_hit_ratio": None,
        }
    try:
        ages = cache.ages()
    except Exception:
        ages = {}
    body["snapshot_ttl_seconds"] = ttl
    body["snapshot_age_seconds"] = ages
    body["shared_cache"] = "file"
    return body


def note_screen(settings, delta: int) -> None:
    """Count live screens. One thread refreshes the snapshot; the SSE loop only peeks."""
    global _subscribers
    with _screen_lock:
        _subscribers = max(0, _subscribers + delta)
        if _subscribers <= 0:
            _screen_stop.set()
            return
        if _screen_thread is None or not _screen_thread.is_alive():
            _start_screen(settings)
        else:
            _screen_stop.set()


def _start_screen(settings) -> None:
    global _screen_thread
    _screen_stop.clear()
    _screen_thread = threading.Thread(target=_screen_loop, args=(settings,), name="broker-snapshot", daemon=True)
    _screen_thread.start()


def _screen_loop(settings) -> None:
    global _screen_thread
    try:
        while True:
            with _screen_lock:
                if _subscribers <= 0:
                    return
            try:
                refresh_due(settings)
            except Exception:
                # refresh_due already logged the failed parts and the counters.
                pass
            if _screen_stop.wait(_ttl(settings)):
                _screen_stop.clear()
    finally:
        with _screen_lock:
            if _subscribers > 0 and _screen_thread is threading.current_thread():
                _start_screen(settings)
            elif _screen_thread is threading.current_thread():
                _screen_thread = None


def _current_settings():
    if _settings is not None:
        return _settings
    from app.config.settings import get_settings

    return get_settings()


def _ttl(settings) -> float:
    method = getattr(settings, "broker_snapshot_ttl", None)
    if callable(method):
        return float(method())
    return 5.0


def _fetch_parts(settings, parts: set[str]):
    """Read each requested part once. Clock is one GET when it is in ``parts``."""
    from app.broker.runtime import open_paper_broker

    adapter = open_paper_broker(settings)
    adapter.client.shared_cache = False
    loaded: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    readers = {
        "account": adapter.get_account,
        "positions": adapter.get_positions,
        "open_orders": lambda: adapter.get_orders("open"),
        "clock": adapter.get_market_clock,
    }
    try:
        for name in PARTS:
            if name not in parts:
                continue
            try:
                loaded[name] = readers[name]()
            except Exception as exc:
                errors[name] = exc
    finally:
        adapter.close()
    return loaded, errors
