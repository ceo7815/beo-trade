from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from app.config.settings import Settings
from app.integrations.secrets import resolve_secret, scrub
from app.providers.base import ProviderNotConfigured, ProviderUnavailable

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
SERIES_URL = "https://api.stlouisfed.org/fred/series"
EXCHANGE = ZoneInfo("America/New_York")
CACHE_OK = timedelta(hours=6)
CACHE_MISS = timedelta(seconds=60)


class FredError(ProviderUnavailable):
    pass


@dataclass
class _FredState:
    authenticated: bool | None = None
    last_success: str | None = None
    last_fetch: str | None = None
    series_count: int = 0
    last_error: str | None = None
    snapshot: dict | None = None


_state = _FredState()


def reset_fred_state() -> None:
    global _state
    _state = _FredState()


def fred_status() -> dict:
    return {
        "provider": "fred",
        "authenticated": _state.authenticated,
        "last_success": _state.last_success,
        "last_fetch": _state.last_fetch,
        "series_count": _state.series_count,
        "last_error": _state.last_error,
    }


def observation_ready(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    rows = payload.get("observations")
    if not isinstance(rows, list):
        return False
    return any(_numeric(row.get("value")) is not None for row in rows if isinstance(row, dict))


class FredFeed:
    name = "fred"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        key, _origin = resolve_secret(settings, "fred_api_key")
        if not key:
            raise ProviderNotConfigured("FRED_API_KEY")
        self.settings = settings
        self.transport = transport
        self._key = key

    def status_detail(self) -> dict:
        return fred_status()

    def latest(self, as_of: datetime) -> dict | None:
        fetched = _parse_fetch(_state.last_fetch)
        if fetched is not None:
            age = datetime.now(EXCHANGE) - fetched
            if _state.authenticated and age < CACHE_OK:
                return _state.snapshot
            if not _state.authenticated and age < CACHE_MISS:
                return None
        return self.load(as_of)

    def load(self, as_of: datetime) -> dict | None:
        session_day = as_of.astimezone(EXCHANGE).date().isoformat()
        series = []
        errors: list[str] = []
        for series_id in self.settings.trading().fred_series:
            try:
                item = self._series(series_id, session_day)
            except FredError as exc:
                errors.append(str(exc))
                continue
            if item is not None:
                series.append(item)
        now = datetime.now(EXCHANGE).isoformat()
        ready = bool(series)
        _state.last_fetch = now
        _state.series_count = len(series)
        _state.authenticated = True if ready else False
        _state.last_success = now if ready else None
        _state.last_error = None if ready else (errors[0] if errors else "אין תצפית מספרית")
        _state.snapshot = {"source": "fred", "series": series} if ready else None
        return _state.snapshot

    def _series(self, series_id: str, session_day: str) -> dict | None:
        meta_rows = self._get(SERIES_URL, {"series_id": series_id}).get("seriess") or []
        meta = meta_rows[0] if meta_rows and isinstance(meta_rows[0], dict) else {}
        payload = self._get(
            OBSERVATIONS_URL,
            {
                "series_id": series_id,
                "sort_order": "desc",
                "limit": 5,
                "observation_end": session_day,
                "realtime_start": session_day,
                "realtime_end": session_day,
            },
        )
        for row in payload.get("observations") or []:
            if not isinstance(row, dict):
                continue
            observed = str(row.get("date") or "")
            value = _numeric(row.get("value"))
            if value is None or not observed or observed > session_day:
                continue
            return {
                "id": series_id,
                "title": str(meta.get("title") or series_id),
                "units": str(meta.get("units_short") or meta.get("units") or ""),
                "date": observed,
                "value": float(value),
            }
        return None

    def _get(self, url: str, params: dict) -> dict:
        try:
            with httpx.Client(transport=self.transport, timeout=20) as client:
                response = client.get(url, params={"api_key": self._key, "file_type": "json", **params})
        except httpx.HTTPError as exc:
            raise FredError(scrub(str(exc), [self._key])) from exc
        if response.status_code != 200:
            raise FredError(f"FRED החזיר {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise FredError("FRED החזיר תשובה לא תקינה") from exc
        if not isinstance(payload, dict):
            raise FredError("FRED החזיר תשובה לא תקינה")
        return payload


def _numeric(value: object) -> Decimal | None:
    if not isinstance(value, str) or not value.strip() or value.strip() == ".":
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


def _parse_fetch(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=EXCHANGE)
    return parsed.astimezone(EXCHANGE)
