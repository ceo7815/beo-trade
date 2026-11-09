"""VIX for the Avi engine. Alpaca's free plan does not serve the index.

Order: Cboe delayed quote, then FRED VIXCLS, then the last value stored in
avi.sqlite from today or the previous session. A hit is reused for five
minutes. The FRED key is the existing FRED_API_KEY env and is never logged.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.spy.clock import as_ny

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRESH = timedelta(minutes=5)
STALE_DAYS = 5
_SOURCES = {"cboe", "fred", "cache"}


def read_vix(now: datetime, store, *, transport: httpx.BaseTransport | None = None, fred_key: str | None = None) -> tuple[Decimal | None, str]:
    """Return (price, source). Source is cboe, fred, cache, or empty."""
    fresh = _fresh(store, now)
    if fresh is not None:
        return fresh
    key = os.environ.get("FRED_API_KEY", "").strip() if fred_key is None else fred_key.strip()
    try:
        with httpx.Client(transport=transport, timeout=httpx.Timeout(8.0), follow_redirects=True) as client:
            price = _cboe(client)
            if price is not None:
                _remember(store, now, price, "cboe")
                return price, "cboe"
            if key:
                price = _fred(client, key)
                if price is not None:
                    _remember(store, now, price, "fred")
                    return price, "fred"
    except Exception:
        pass
    cached = _stale(store, now)
    if cached is None:
        return None, ""
    _remember(store, now, cached, "cache")
    return cached, "cache"


def _fresh(store, now: datetime) -> tuple[Decimal, str] | None:
    stamp = _parse(store.meta("vix_cached_at"))
    if stamp is None or now - stamp >= FRESH:
        return None
    value = _positive(store.meta("vix_value"))
    source = store.meta("vix_source") or ""
    if value is None or source not in _SOURCES:
        return None
    return value, source


def _stale(store, now: datetime) -> Decimal | None:
    value = _positive(store.meta("vix_value"))
    if value is None:
        return None
    try:
        day = datetime.fromisoformat(str(store.meta("vix_session") or "")).date()
    except ValueError:
        return None
    age = (as_ny(now).date() - day).days
    if age < 0 or age > STALE_DAYS:
        return None
    return value


def _remember(store, now: datetime, price: Decimal, source: str) -> None:
    store.set_meta("vix_value", str(price))
    store.set_meta("vix_source", source)
    store.set_meta("vix_cached_at", now.astimezone(timezone.utc).isoformat())
    store.set_meta("vix_session", as_ny(now).date().isoformat())


def _cboe(client: httpx.Client) -> Decimal | None:
    response = client.get(CBOE_URL)
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    return _positive(data.get("current_price"))


def _fred(client: httpx.Client, key: str) -> Decimal | None:
    response = client.get(
        FRED_URL,
        params={"series_id": "VIXCLS", "api_key": key, "file_type": "json", "sort_order": "desc", "limit": "5"},
    )
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except Exception:
        return None
    rows = body.get("observations") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _positive(row.get("value"))
        if price is not None:
            return price
    return None


def _positive(value) -> Decimal | None:
    if value in {None, "", "."}:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if number <= 0:
        return None
    return number


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
