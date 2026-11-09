"""VIX fallback chain and indicative option-quote retries. HTTP is mocked."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.avi.broker import AviBroker
from app.avi.config import AviConfig
from app.avi.store import Store
from app.avi.vix import CBOE_URL, read_vix

NOW = datetime(2026, 11, 9, 15, 30, tzinfo=timezone.utc)
SECRET = "fred-secret-value"


def _cfg(tmp_path) -> AviConfig:
    return AviConfig(
        True,
        "kid",
        "sec",
        "PAQTG",
        "",
        "",
        Decimal("0.04"),
        None,
        10,
        Decimal("0.08"),
        tmp_path / "avi.sqlite",
    )


class Script(httpx.BaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if not self.responses:
            raise AssertionError(request.url)
        return self.responses.pop(0)


def test_cboe_quote_is_used_and_cached_for_five_minutes(tmp_path):
    store = Store(tmp_path / "avi.sqlite")
    script = Script(
        [
            httpx.Response(302, headers={"location": CBOE_URL + "?ok=1"}),
            httpx.Response(200, json={"data": {"current_price": 16.4}}),
        ]
    )
    price, source = read_vix(NOW, store, transport=script, fred_key="")
    assert price == Decimal("16.4") and source == "cboe"
    assert any("cdn.cboe.com" in url for url in script.urls)
    again = Script([])
    cached, cached_source = read_vix(NOW + timedelta(minutes=4), store, transport=again, fred_key="")
    assert cached == Decimal("16.4") and cached_source == "cboe"
    assert again.urls == []
    store.close()


def test_fred_is_used_when_cboe_fails(tmp_path):
    store = Store(tmp_path / "avi.sqlite")
    script = Script(
        [
            httpx.Response(404, json={"error": "missing"}),
            httpx.Response(200, json={"observations": [{"date": "2026-11-07", "value": "."}, {"date": "2026-11-06", "value": "19.5"}]}),
        ]
    )
    price, source = read_vix(NOW, store, transport=script, fred_key=SECRET)
    assert price == Decimal("19.5") and source == "fred"
    assert "series_id=VIXCLS" in script.urls[1]
    assert "api.stlouisfed.org" in script.urls[1]
    store.close()


def test_sqlite_cache_covers_today_or_the_previous_session(tmp_path):
    store = Store(tmp_path / "avi.sqlite")
    store.set_meta("vix_value", "21.25")
    store.set_meta("vix_source", "cboe")
    store.set_meta("vix_cached_at", (NOW - timedelta(hours=6)).isoformat())
    store.set_meta("vix_session", "2026-11-06")
    script = Script([httpx.Response(404), httpx.Response(500)])
    price, source = read_vix(NOW, store, transport=script, fred_key="k")
    assert price == Decimal("21.25") and source == "cache"
    store.set_meta("vix_session", "2026-10-01")
    store.set_meta("vix_cached_at", (NOW - timedelta(hours=6)).isoformat())
    stale = Script([httpx.Response(404), httpx.Response(500)])
    missing, missing_source = read_vix(NOW, store, transport=stale, fred_key="k")
    assert missing is None and missing_source == ""
    store.close()


def test_no_fred_key_skips_fred_after_cboe(tmp_path, monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    store = Store(tmp_path / "avi.sqlite")
    script = Script([httpx.Response(404)])
    price, source = read_vix(NOW, store, transport=script, fred_key="")
    assert price is None and source == ""
    assert len(script.urls) == 1
    store.close()


def test_option_snapshots_use_indicative_and_retry_429(tmp_path):
    sleeps: list[float] = []
    script = Script(
        [
            httpx.Response(429, json={"message": "too many requests"}),
            httpx.Response(429, json={"message": "too many requests"}),
            httpx.Response(200, json={"snapshots": {"QQQ260111C00600000": {"latestQuote": {"bp": "1.10", "ap": "1.20"}}}}),
        ]
    )
    broker = AviBroker(_cfg(tmp_path), transport=script, sleep=sleeps.append)
    try:
        quotes = broker.quotes(["QQQ260111C00600000", "QQQ260111C00605000"])
    finally:
        broker.close()
    assert quotes["QQQ260111C00600000"]["bid"] == "1.10"
    assert len(script.urls) == 3
    assert all("feed=indicative" in url for url in script.urls)
    assert sleeps == [2.0, 4.0]
    assert len({url.split("symbols=")[1].split("&")[0] for url in script.urls}) == 1
