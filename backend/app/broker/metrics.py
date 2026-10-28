"""Alpaca call and cache counters.

The API and the worker are separate processes. Each keeps its own counters and
flushes them to ``data/broker_metrics.json`` under a file lock. The health
endpoint sums every process that has written in the last few minutes.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

WINDOW_SECONDS = 60.0
PROCESS_STALE_SECONDS = 180.0

_lock = threading.Lock()
_calls: list[float] = []
_http_429 = 0
_http_5xx = 0
_hits = 0
_misses = 0
_last_flush = 0.0
_directory: Path | None = None


def isolate(path: Path | None) -> None:
    """Point persistence at a test directory and clear this process's counters."""
    global _directory
    with _lock:
        _directory = path
        _reset_unlocked()


def reset() -> None:
    with _lock:
        _reset_unlocked()


def _reset_unlocked() -> None:
    global _http_429, _http_5xx, _hits, _misses, _last_flush
    _calls.clear()
    _http_429 = 0
    _http_5xx = 0
    _hits = 0
    _misses = 0
    _last_flush = 0.0


def note_call() -> None:
    _bump(call=True)


def note_429() -> None:
    _bump(rate_limit=True, flush=True)


def note_5xx() -> None:
    _bump(server=True, flush=True)


def note_hit() -> None:
    _bump(hit=True)


def note_miss() -> None:
    _bump(miss=True, flush=True)


def _bump(*, call: bool = False, rate_limit: bool = False, server: bool = False, hit: bool = False, miss: bool = False, flush: bool = False) -> None:
    global _http_429, _http_5xx, _hits, _misses
    try:
        with _lock:
            now = time.time()
            if call:
                _calls.append(now)
                _trim(now)
            if rate_limit:
                _http_429 += 1
            if server:
                _http_5xx += 1
            if hit:
                _hits += 1
            if miss:
                _misses += 1
            due = flush or (now - _last_flush) >= 1.0
        if due:
            _flush()
    except Exception:
        return


def _trim(now: float) -> None:
    cutoff = now - WINDOW_SECONDS
    while _calls and _calls[0] < cutoff:
        _calls.pop(0)


def public_counters() -> dict:
    """Counters for this process plus any sibling process that flushed recently."""
    try:
        _flush()
        with _lock:
            mine = _mine_unlocked()
        shared = _read_shared()
        processes = shared.get("processes") if isinstance(shared.get("processes"), dict) else {}
        processes[str(os.getpid())] = mine
        return _sum(processes, time.time())
    except Exception:
        return _empty()


def _mine_unlocked() -> dict:
    now = time.time()
    _trim(now)
    return {
        "calls": list(_calls),
        "http_429": _http_429,
        "http_5xx": _http_5xx,
        "hits": _hits,
        "misses": _misses,
        "touched": now,
    }


def _sum(processes: dict, now: float) -> dict:
    calls = 0
    http_429 = 0
    http_5xx = 0
    hits = 0
    misses = 0
    for row in processes.values():
        if not isinstance(row, dict):
            continue
        try:
            touched = float(row.get("touched") or 0)
        except (TypeError, ValueError):
            continue
        if now - touched > PROCESS_STALE_SECONDS:
            continue
        stamps = row.get("calls") if isinstance(row.get("calls"), list) else []
        calls += sum(1 for stamp in stamps if _recent(stamp, now))
        http_429 += _int(row.get("http_429"))
        http_5xx += _int(row.get("http_5xx"))
        hits += _int(row.get("hits"))
        misses += _int(row.get("misses"))
    samples = hits + misses
    return {
        "calls_per_minute": calls,
        "http_429": http_429,
        "http_5xx": http_5xx,
        "cache_hits": hits,
        "cache_misses": misses,
        "cache_hit_ratio": None if samples == 0 else round(hits / samples, 4),
    }


def _recent(stamp: object, now: float) -> bool:
    try:
        return now - float(stamp) <= WINDOW_SECONDS
    except (TypeError, ValueError):
        return False


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _empty() -> dict:
    return {
        "calls_per_minute": 0,
        "http_429": 0,
        "http_5xx": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_hit_ratio": None,
    }


def _metrics_path() -> Path:
    if _directory is not None:
        return _directory / "broker_metrics.json"
    from app.ops.paths import BROKER_METRICS

    return BROKER_METRICS


def _flush() -> None:
    global _last_flush
    path = _metrics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        with _FileLock(lock_path):
            with _lock:
                mine = _mine_unlocked()
                _last_flush = mine["touched"]
            shared = _read_file(path)
            processes = shared.get("processes") if isinstance(shared.get("processes"), dict) else {}
            now = time.time()
            live = {}
            for key, row in processes.items():
                if not isinstance(row, dict):
                    continue
                try:
                    touched = float(row.get("touched") or 0)
                except (TypeError, ValueError):
                    continue
                if now - touched <= PROCESS_STALE_SECONDS:
                    live[str(key)] = row
            live[str(os.getpid())] = mine
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"processes": live}), encoding="utf-8")
            temporary.replace(path)
    except (OSError, TypeError, ValueError):
        return


def _read_shared() -> dict:
    try:
        return _read_file(_metrics_path())
    except (OSError, ValueError):
        return {}


def _read_file(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


class _FileLock:
    def __init__(self, path: Path) -> None:
        import fcntl

        self._fcntl = fcntl
        self._path = path
        self._handle = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+")
        self._fcntl.flock(self._handle.fileno(), self._fcntl.LOCK_EX)
        return self

    def __exit__(self, *_args) -> None:
        if self._handle is not None:
            self._fcntl.flock(self._handle.fileno(), self._fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
