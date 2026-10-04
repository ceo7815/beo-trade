from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.news.confirm import fresh_news, news_confirmed
from app.providers.base import ProviderNotConfigured
from app.providers.benzinga import BenzingaError, BenzingaNewsProvider, reset_feed_state
from app.providers.registry import ProviderSet, build_providers
from app.recommendations.live_scan import MarketDataMissing, execute_scan
from app.schemas.domain import DecisionKind, NewsItem
from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market

NVDA_AS_OF = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)

NVDA_ARTICLE = {
    "id": 62118585,
    "author": "Benzinga Newsdesk",
    "created": "Thu, 01 Oct 2026 14:20:24 -0400",
    "updated": "Thu, 01 Oct 2026 14:20:24 -0400",
    "title": "Nvidia discussion reported by The Information",
    "url": "https://www.benzinga.com/news/nvda-example",
    "stocks": [{"name": "NVDA"}, {"name": "SFTBY"}, {"name": "MSFT"}],
    "channels": [{"name": "News"}, {"name": "General"}],
    "teaser": "A published report discussed Nvidia.",
    "importance_rank": 4,
}

TEST_ARTICLE = {
    "id": 70000001,
    "author": "Benzinga Newsdesk",
    "created": "Thu, 01 Oct 2026 10:00:00 -0400",
    "updated": "Thu, 01 Oct 2026 10:20:00 -0400",
    "title": "TEST updates its outlook",
    "url": "https://www.benzinga.com/news/test-outlook",
    "stocks": [{"name": "TEST"}],
    "channels": [{"name": "Guidance"}],
    "teaser": "The company updated guidance.",
    "importance_rank": 3,
}


@pytest.fixture(autouse=True)
def _clean_feed():
    reset_feed_state()
    yield
    reset_feed_state()


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "local",
        "database_url": "sqlite://",
        "auto_create_tables": True,
        "auth_required": False,
        "benzinga_api_key": "test-key",
        "openai_price_input_per_million": Decimal("0"),
        "openai_price_output_per_million": Decimal("0"),
    }
    values.update(overrides)
    return Settings(**values)


def _provider(handler, **overrides) -> BenzingaNewsProvider:
    return BenzingaNewsProvider(_settings(**overrides), transport=httpx.MockTransport(handler))


def test_authentication_rejects_anonymous_and_accepts_token_query():
    denied = []

    def reject(request: httpx.Request) -> httpx.Response:
        denied.append(request)
        return httpx.Response(401, json=["Access denied for user 0"])

    provider = _provider(reject)
    with pytest.raises(BenzingaError, match="האימות נכשל"):
        provider.load_news(AS_OF)
    status = provider.status_detail()
    assert status["authenticated"] is False
    assert status["article_count"] == 0
    assert status["last_fetch"]
    assert status["last_success"] is None
    assert status["last_error"]
    assert "test-key" not in status["last_error"]
    assert denied[0].url.params["token"] == "test-key"
    assert "authorization" not in {name.lower() for name in denied[0].headers}

    def accept(request: httpx.Request) -> httpx.Response:
        assert request.url.params["token"] == "test-key"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(200, json=[NVDA_ARTICLE])

    reset_feed_state()
    ok = _provider(accept)
    items = ok.load_news(NVDA_AS_OF)
    assert len(items) == 1
    assert ok.status_detail()["authenticated"] is True
    assert ok.status_detail()["last_error"] is None
    assert ok.status_detail()["article_count"] == 1


def test_real_article_fields_map_into_news_item():
    provider = _provider(lambda request: httpx.Response(200, json=[NVDA_ARTICLE]))
    item = provider.load_news(NVDA_AS_OF)[0]
    assert item.article_id == "62118585"
    assert item.source == "benzinga"
    assert item.headline == NVDA_ARTICLE["title"]
    assert item.symbols == ("NVDA", "SFTBY", "MSFT")
    assert item.author == "Benzinga Newsdesk"
    assert item.source_host == "www.benzinga.com"
    assert item.channels == ("News", "General")
    assert item.url == NVDA_ARTICLE["url"]
    assert item.published_at == parsedate_to_datetime(NVDA_ARTICLE["created"])
    assert item.observed_at == parsedate_to_datetime(NVDA_ARTICLE["updated"])
    assert item.published_at.tzinfo is not None
    assert item.observed_at.tzinfo is not None
    assert item.source_credibility == Decimal("0.75")
    assert float(item.source_credibility) < loose_config().official_source_credibility


def test_primary_tickers_filter_maps_nvda_symbol():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=[NVDA_ARTICLE])

    item = _provider(handler).load_news(NVDA_AS_OF, ("nvda",))[0]
    assert seen["params"]["primaryTickers"] == "NVDA"
    assert "tickers" not in seen["params"]
    assert "NVDA" in item.symbols
    assert item.article_id == "62118585"


def test_updated_since_is_sent_on_the_next_poll():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("updatedSince"))
        return httpx.Response(200, json=[NVDA_ARTICLE])

    provider = _provider(handler)
    provider.load_news(NVDA_AS_OF)
    provider.load_news(NVDA_AS_OF)
    expected = str(int(parsedate_to_datetime(NVDA_ARTICLE["updated"]).timestamp()))
    assert calls == [None, expected]


def test_duplicate_article_ids_collapse_to_one_item():
    provider = _provider(lambda request: httpx.Response(200, json=[NVDA_ARTICLE, dict(NVDA_ARTICLE)]))
    items = provider.load_news(NVDA_AS_OF)
    assert len(items) == 1
    assert provider.status_detail()["article_count"] == 1
    again = provider.load_news(NVDA_AS_OF)
    assert [item.article_id for item in again] == ["62118585"]
    assert provider.status_detail()["article_count"] == 1


def test_provider_failure_does_not_invent_articles():
    provider = _provider(lambda request: httpx.Response(500, json={"error": "down"}))
    with pytest.raises(BenzingaError, match="500"):
        provider.load_news(AS_OF)
    status = provider.status_detail()
    assert status["article_count"] == 0
    assert status["last_success"] is None
    assert status["last_error"]
    assert status["authenticated"] is not True
    assert "test-key" not in status["last_error"]


def test_fresh_news_and_gate_see_one_benzinga_source_without_confirming_it():
    provider = _provider(lambda request: httpx.Response(200, json=[TEST_ARTICLE]))
    items = provider.load_news(AS_OF, ("TEST",))
    kept = fresh_news(items, "TEST", AS_OF, loose_config().news_max_age_seconds)
    assert [item.headline for item in kept] == ["TEST updates its outlook"]
    assert news_confirmed(kept, loose_config()) is False
    other = NewsItem(
        source="sec",
        url="https://example.test/filing",
        headline="SEC filing",
        published_at=AS_OF - timedelta(minutes=10),
        symbols=("TEST",),
        category="filing",
        event_type="filing",
        importance=3,
        content="",
        source_credibility=Decimal("0.95"),
        observed_at=AS_OF - timedelta(minutes=10),
    )
    assert news_confirmed(kept + [other], loose_config()) is True
    assert loose_config().allow_buy_without_news is False
    assert loose_config().min_confirming_sources == 2


class _Capture(StubAI):
    packet = None

    def analyze(self, packet, as_of):
        _Capture.packet = packet
        return super().analyze(packet, as_of)


class _ReadyMarket:
    name = "stub"

    def load_underlyings(self, as_of):
        return [sample_market()[0]]

    def load_options(self, as_of):
        return [sample_market()[1]]


class _MissingMarket:
    name = "unconfigured"
    variable = "MARKET_DATA_PROVIDER"

    def load_underlyings(self, as_of):
        raise ProviderNotConfigured(self.variable)

    def load_options(self, as_of):
        raise ProviderNotConfigured(self.variable)


def test_scan_reaches_decision_packet_and_news_gate_rejects_single_source():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["primary"] = request.url.params.get("primaryTickers")
        return httpx.Response(200, json=[TEST_ARTICLE])

    _Capture.packet = None
    providers = ProviderSet(market=_ReadyMarket(), options=_ReadyMarket(), news=_provider(handler))
    result = execute_scan(
        providers,
        loose_config(),
        _Capture(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("1000"),
        set(),
    )
    assert seen["primary"] == "TEST"
    assert _Capture.packet is not None
    article = _Capture.packet["news"][0]
    assert article["source"] == "benzinga"
    assert article["headline"] == "TEST updates its outlook"
    assert article["symbols"] == ["TEST"]
    assert article["id"] == "70000001"
    assert result.news_count == 1
    assert result.recommendations
    assert result.recommendations[0].gate_results["NEWS_OK"] is False
    assert result.recommendations[0].decision is DecisionKind.SUPPRESS


def test_scan_fetches_benzinga_then_stops_when_market_data_is_missing():
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["count"] += 1
        return httpx.Response(200, json=[TEST_ARTICLE])

    providers = ProviderSet(market=_MissingMarket(), options=_MissingMarket(), news=_provider(handler))
    with pytest.raises(MarketDataMissing) as caught:
        execute_scan(providers, loose_config(), _Capture(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())
    assert called["count"] == 1
    assert caught.value.variable == "MARKET_DATA_PROVIDER"
    assert caught.value.news_count == 1


def test_http_scan_continues_past_benzinga_and_exposes_provider_status(tmp_path, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.host, request.url.path))
        assert request.url.params["token"] == "test-key"
        return httpx.Response(200, json=[NVDA_ARTICLE])

    real_client = httpx.Client

    def factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout", 20))

    monkeypatch.setattr("app.providers.benzinga.httpx.Client", factory)
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    database = tmp_path / "beo.db"
    settings = _settings(database_url="sqlite:///" + database.as_posix())
    with TestClient(create_app(settings)) as client:
        built = build_providers(settings)
        assert built.news.name == "benzinga"
        assert built.market.name == "unconfigured"
        scan = client.post("/api/v1/scan")
        assert scan.status_code == 409
        assert "test-key" not in scan.text
        assert calls == [("api.benzinga.com", "/api/v2/news")]
        status = client.get("/api/v1/system")
        body = status.json()
        assert body["providers"]["news"] == "benzinga"
        assert body["providers"]["market"] == "unconfigured"
        assert body["news_status"]["authenticated"] is True
        assert body["news_status"]["article_count"] == 1
        assert body["news_status"]["last_success"]
        assert body["news_status"]["last_fetch"]
        assert body["news_status"]["last_error"] is None
        assert "test-key" not in status.text


def test_http_scan_reports_benzinga_failure_without_articles(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="no")

    real_client = httpx.Client

    def factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout", 20))

    monkeypatch.setattr("app.providers.benzinga.httpx.Client", factory)
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    database = tmp_path / "beo.db"
    settings = _settings(database_url="sqlite:///" + database.as_posix())
    with TestClient(create_app(settings)) as client:
        scan = client.post("/api/v1/scan")
        assert scan.status_code == 502
        assert "test-key" not in scan.text
        body = client.get("/api/v1/system").json()
        assert body["news_status"]["article_count"] == 0
        assert body["news_status"]["last_error"]
        assert body["news_status"]["authenticated"] is not True
