from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.config.settings import Settings
from app.integrations.probes import probe_sec
from app.providers.registry import ProviderSet
from app.providers.sec import SecError, SecFeed, classify_http, reset_sec_state, sec_status
from app.providers.thetadata import ThetaMarketProvider, ThetaOptionsProvider
from app.providers.unconfigured import UnconfiguredProvider
from app.recommendations.live_scan import MarketDataMissing, execute_scan
from tests.test_engine import AS_OF, StubAI, ledger, loose_config
from tests.test_thetadata import _feed, _handler, _settings


@pytest.fixture(autouse=True)
def _clean():
    reset_sec_state()
    yield
    reset_sec_state()


def test_access_denied_and_rate_limit_are_not_success():
    status, detail, delay = classify_http(403, None)
    assert status == "error"
    assert "סירב" in detail
    assert delay is None
    status, detail, delay = classify_http(429, "2")
    assert status == "error"
    assert "הגביל" in detail
    assert delay == 2


def test_http_403_and_429_do_not_authenticate():
    def denied(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].endswith("research@beosystems.com")
        return httpx.Response(403)

    feed = SecFeed(_settings(), transport=httpx.MockTransport(denied))
    with pytest.raises(SecError, match="סירב"):
        feed.health(AS_OF)
    assert sec_status()["authenticated"] is False
    assert sec_status()["submissions_status"] is None

    calls = {"count": 0}

    def limited(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}})
        return httpx.Response(429, headers={"Retry-After": "0"})

    reset_sec_state()
    feed = SecFeed(_settings(), transport=httpx.MockTransport(limited))
    with pytest.raises(SecError, match="הגביל"):
        feed.health(AS_OF)
    assert calls["count"] == 3
    assert sec_status()["authenticated"] is not True


def test_live_submissions_and_company_facts():
    report = SecFeed(Settings()).health()
    assert report["submissions_status"] == 200
    assert report["facts_status"] == 200
    assert report["submissions_endpoint"].startswith("https://data.sec.gov/submissions/CIK")
    assert report["facts_endpoint"].startswith("https://data.sec.gov/api/xbrl/companyfacts/CIK")
    assert report["cik"].isdigit() and len(report["cik"]) == 10
    assert report["entity"]
    assert report["filings"]
    assert report["filings"][0]["form"]
    assert report["filings"][0]["filed"]
    assert report["facts_available"] is True
    assert report["facts"]
    assert report["facts"][0]["value"] is not None
    assert sec_status()["authenticated"] is True
    status, _latency, detail = probe_sec(Settings())
    assert status == "connected"
    assert "200" in detail


class _Capture(StubAI):
    packet = None

    def analyze(self, packet, as_of):
        _Capture.packet = packet
        return super().analyze(packet, as_of)


def test_research_context_does_not_enter_news_or_run_without_market_data():
    class _Boom:
        name = "sec"

        def load(self, as_of, symbols):
            raise AssertionError("SEC ran without market data")

    with pytest.raises(MarketDataMissing):
        execute_scan(
            ProviderSet(
                market=UnconfiguredProvider("MARKET_DATA_PROVIDER"),
                options=UnconfiguredProvider("OPTIONS_DATA_PROVIDER"),
                news=UnconfiguredProvider("NEWS_PROVIDER"),
                research=_Boom(),
            ),
            loose_config(),
            None,
            ledger(),
            AS_OF,
            AS_OF.date(),
            Decimal("1000"),
            set(),
        )

    handler, _seen = _handler()
    market = _feed(handler)
    _Capture.packet = None
    execute_scan(
        ProviderSet(
            market=ThetaMarketProvider(market),
            options=ThetaOptionsProvider(market),
            news=UnconfiguredProvider("NEWS_PROVIDER"),
            research=SecFeed(Settings()),
        ),
        loose_config(),
        _Capture(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("1000"),
        set(),
    )
    assert _Capture.packet is not None
    assert _Capture.packet["research"]["source"] == "sec"
    assert _Capture.packet["research"]["company"]["filings"]
    assert all(item["source"] != "sec" for item in _Capture.packet["news"])
