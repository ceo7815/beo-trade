"""Daily USD/ILS rate from Frankfurter (free). Config 3.14 is the offline fallback.

Source note (2026-11-02 Asia/Dubai): frankfurter 3.1393, er-api 3.1405.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

import httpx

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=ILS"
CACHE_TTL_SECONDS = 20 * 60 * 60  # refresh about once per trading day
_memory: dict[str, object] = {"rate": None, "fetched_at": 0.0}


def _cache_path() -> Path:
    from app.ops.paths import DATA_DIR

    return DATA_DIR / "fx_ils_per_usd.json"


def live_ils_per_usd(*, force: bool = False) -> Decimal | None:
    """Return a cached or freshly fetched ILS-per-USD rate, or None on failure."""
    now = time.time()
    cached = _memory.get("rate")
    fetched_at = float(_memory.get("fetched_at") or 0)
    if not force and cached is not None and now - fetched_at < CACHE_TTL_SECONDS:
        return Decimal(str(cached))

    disk = _read_disk()
    if not force and disk is not None:
        rate, at = disk
        if now - at < CACHE_TTL_SECONDS:
            _memory["rate"] = rate
            _memory["fetched_at"] = at
            return rate

    rate = _fetch_frankfurter()
    if rate is None:
        return Decimal(str(cached)) if cached is not None else (disk[0] if disk else None)
    _memory["rate"] = rate
    _memory["fetched_at"] = now
    _write_disk(rate, now)
    return rate


def _fetch_frankfurter() -> Decimal | None:
    try:
        with httpx.Client(timeout=httpx.Timeout(4.0)) as client:
            response = client.get(FRANKFURTER_URL)
            response.raise_for_status()
            body = response.json()
        raw = (body.get("rates") or {}).get("ILS")
        if raw in {None, ""}:
            return None
        rate = Decimal(str(raw))
        return rate if rate > 0 else None
    except Exception:
        return None


def _read_disk() -> tuple[Decimal, float] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
        rate = Decimal(str(body["rate"]))
        at = float(body["fetched_at"])
        if rate <= 0:
            return None
        return rate, at
    except Exception:
        return None


def _write_disk(rate: Decimal, fetched_at: float) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"rate": str(rate), "fetched_at": fetched_at, "source": "frankfurter.dev"}),
            encoding="utf-8",
        )
    except Exception:
        pass
