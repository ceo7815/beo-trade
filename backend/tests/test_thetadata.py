from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app
from app.integrations.probes import probe_thetadata
from app.providers.base import ProviderNotConfigured, ProviderUnavailable
from app.providers.registry import ProviderSet, build_providers
from app.providers.thetadata import ThetaDataError, ThetaFeed, ThetaMarketProvider, ThetaOptionsProvider, feed_status, reset_theta_state
from app.providers.unconfigured import UnconfiguredProvider
from app.quant.filters import reject_underlying
from app.recommendations.live_scan import execute_scan
from tests.test_engine import AS_OF, StubAI, ledger, loose_config

EXCHANGE = ZoneInfo("America/New_York")
FRESH = "2026-10-01T11:00:00.000"
STALE = "2026-10-01T10:00:00.000"
FUTURE = "2026-10-01T11:05:00.000"


@pytest.fixture(autouse=True)
def _clean():
    reset_theta_state()
    yield
    reset_theta_state()


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "local",
        "database_url": "sqlite://",
        "auto_create_tables": True,
        "auth_required": False,
        "thetadata_api_key": "test-terminal",
        "openai_price_input_per_million": Decimal("0"),
        "openai_price_output_per_million": Decimal("0"),
    }
    values.update(overrides)
    return Settings(**values)


def _stock(symbol: str, stamp: str) -> tuple[dict, dict, dict, dict]:
    ohlc = {
        "symbol": symbol,
        "timestamp": stamp,
        "open": 100,
        "high": 103,
        "low": 99,
        "close": 102,
        "volume": 2_000_000,
        "count": 1000,
    }
    quote = {"symbol": symbol, "timestamp": stamp, "bid": 101.9, "ask": 102.1, "bid_size": 10, "ask_size": 12}
    trade = {"symbol": symbol, "timestamp": stamp, "price": 102, "size": 100}
    eod = {"symbol": symbol, "created": "2026-09-30T17:15:00.000", "last_trade": "2026-09-30T16:00:00.000", "close": 100, "volume": 1_000_000}
    return ohlc, quote, trade, eod


def _chain(stamp: str) -> dict[str, dict]:
    base = {"symbol": "NVDA", "expiration": "2026-10-03", "strike": 100, "right": "CALL"}
    return {
        "quote": {**base, "timestamp": stamp, "bid": 1.0, "ask": 1.1, "bid_size": 20, "ask_size": 25},
        "ohlc": {**base, "timestamp": stamp, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.05, "volume": 800, "count": 40},
        "interest": {**base, "timestamp": stamp, "open_interest": 500},
        "greeks": {**base, "timestamp": stamp, "implied_vol": 0.35, "delta": 0.4, "gamma": 0.02, "theta": -0.05, "vega": 0.1},
    }


def _handler(stamp: str = FRESH, nvda_stamp: str | None = None):
    seen = []
    stock_stamp = nvda_stamp or stamp
    books = {symbol: _stock(symbol, stock_stamp if symbol == "NVDA" else stamp) for symbol in ("NVDA", "SPY", "QQQ", "IWM")}
    chain = _chain(stock_stamp)

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        symbol = request.url.params.get("symbol", "")
        seen.append((path, symbol))
        assert request.url.params["format"] == "json"
        if path == "/v3/stock/snapshot/ohlc":
            return httpx.Response(200, json=[books[name][0] for name in books if name in symbol])
        if path == "/v3/stock/snapshot/quote":
            return httpx.Response(200, json=[books[name][1] for name in books if name in symbol])
        if path == "/v3/stock/snapshot/trade":
            return httpx.Response(200, json=[books[name][2] for name in books if name in symbol])
        if path == "/v3/stock/history/eod":
            row = books.get(symbol)
            return httpx.Response(200, json=[] if row is None else [row[3]])
        if path == "/v3/stock/history/ohlc":
            if symbol != "NVDA":
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {"symbol": "NVDA", "timestamp": "2026-09-30T09:30:00.000", "volume": 100},
                    {"symbol": "NVDA", "timestamp": "2026-09-30T10:59:00.000", "volume": 50},
                    {"symbol": "NVDA", "timestamp": "2026-09-30T11:00:00.000", "volume": 9000},
                    {"symbol": "NVDA", "timestamp": "2026-09-30T15:30:00.000", "volume": 50000},
                    {"symbol": "NVDA", "timestamp": "2026-10-01T09:30:00.000", "volume": 200},
                    {"symbol": "NVDA", "timestamp": "2026-10-01T10:59:00.000", "volume": 100},
                    {"symbol": "NVDA", "timestamp": "2026-10-01T11:00:00.000", "volume": 8000},
                    {"symbol": "NVDA", "timestamp": "2026-10-02T10:00:00.000", "volume": 1},
                ],
            )
        if path == "/v3/index/snapshot/price":
            return httpx.Response(200, json=[{"symbol": "VIX", "timestamp": stamp, "price": 18.4}])
        if symbol != "NVDA":
            return httpx.Response(200, json=[])
        if path == "/v3/option/snapshot/quote":
            assert request.url.params["expiration"] == "*"
            assert request.url.params["max_dte"] == "21"
            assert request.url.params["strike_range"] == "15"
            return httpx.Response(200, json=[chain["quote"]])
        if path == "/v3/option/snapshot/ohlc":
            return httpx.Response(200, json=[chain["ohlc"]])
        if path == "/v3/option/snapshot/open_interest":
            return httpx.Response(200, json=[chain["interest"]])
        if path == "/v3/option/snapshot/greeks/all":
            return httpx.Response(200, json=[chain["greeks"]])
        return httpx.Response(200, json=[])

    return handle, seen


def _feed(handler) -> ThetaFeed:
    feed = ThetaFeed(_settings(), transport=httpx.MockTransport(handler))
    feed.bind_symbols(("NVDA",))
    return feed


def test_underlying_quote_uses_trade_price_and_exchange_timestamp():
    handler, _seen = _handler()
    market = ThetaMarketProvider(_feed(handler))
    rows = {item.symbol: item for item in market.load_underlyings(AS_OF)}
    nvda = rows["NVDA"]
    assert nvda.price == Decimal("102")
    assert nvda.prior_close == Decimal("100")
    assert nvda.change_dollars == Decimal("2")
    assert nvda.change_percent == Decimal("2")
    assert nvda.volume == 2_000_000
    assert nvda.relative_volume == Decimal("2")
    assert nvda.rv_method == "time_of_day_cumulative"
    assert nvda.rv_numerator == 300
    assert nvda.rv_denominator == Decimal("150")
    assert nvda.rv_day_count == 1
    assert nvda.rv_prior_days == ("2026-09-30",)
    assert nvda.rv_prior_totals == (150,)
    assert nvda.rv_cutoff == datetime(2026, 10, 1, 10, 59, tzinfo=EXCHANGE)
    assert nvda.prior_day_volume == 1_000_000
    assert nvda.observed_at == datetime(2026, 10, 1, 11, 0, tzinfo=EXCHANGE)
    assert nvda.observed_at <= AS_OF
    assert nvda.session_ok is True
    context = market.context_quotes()
    assert context["SPY"]["price"] == Decimal("102")
    assert context["QQQ"]["price"] == Decimal("102")
    assert context["IWM"]["price"] == Decimal("102")
    assert context["VIX"]["price"] == Decimal("18.4")
    assert context["VIX"]["change_percent"] is None


def test_nested_v3_option_response_builds_contracts():
    def nested(fields: dict) -> dict:
        return {
            "response": [
                {
                    "contract": {"symbol": "NVDA", "expiration": "2026-10-03", "strike": 100.000, "right": "CALL"},
                    "data": [{"timestamp": FRESH, **fields}],
                }
            ]
        }

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/option/snapshot/quote":
            return httpx.Response(200, json=nested({"bid": 1.0, "ask": 1.1, "bid_size": 20, "ask_size": 25}))
        if path == "/v3/option/snapshot/ohlc":
            return httpx.Response(200, json=nested({"open": 1.0, "high": 1.2, "low": 0.9, "close": 1.05, "volume": 800, "count": 40}))
        if path == "/v3/option/snapshot/open_interest":
            return httpx.Response(200, json=nested({"open_interest": 500}))
        if path == "/v3/option/snapshot/greeks/all":
            return httpx.Response(200, json=nested({"implied_vol": 0.35, "delta": 0.4, "gamma": 0.02, "theta": -0.05, "vega": 0.1}))
        return httpx.Response(200, json=[])

    feed = _feed(handle)
    feed.bind_option_symbols(("NVDA",))
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    contract = chain[0]
    assert contract.bid == Decimal("1.0")
    assert contract.ask == Decimal("1.1")
    assert contract.volume == 800
    assert contract.open_interest == 500
    assert contract.delta == Decimal("0.4")
    assert contract.expiration.isoformat() == "2026-10-03"


@pytest.mark.parametrize(
    ("stamp", "current"),
    [(FUTURE, True), (STALE, True), ("2026-09-30T15:59:00.000", False)],
)
def test_standing_session_quote_is_current_when_the_chain_is_read(stamp, current):
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/option/snapshot/quote":
            return httpx.Response(
                200,
                json=[{"symbol": "NVDA", "expiration": "2026-10-03", "strike": 100, "right": "CALL", "timestamp": stamp, "bid": 1.0, "ask": 1.1}],
            )
        return httpx.Response(200, json=[])

    feed = _feed(handle)
    feed.bind_option_symbols(("NVDA",))
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    if current:
        assert chain[0].observed_at == AS_OF
    else:
        assert chain[0].observed_at < AS_OF


def test_column_response_and_listed_expiration_build_a_contract():
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/option/snapshot/quote" and request.url.params.get("expiration") == "*":
            return httpx.Response(472, text="NO_DATA")
        if path == "/v3/option/list/expirations":
            return httpx.Response(200, json=[{"symbol": "NVDA", "expiration": "20261003"}])
        if path == "/v3/option/snapshot/quote":
            return httpx.Response(
                200,
                json={
                    "header": {"format": ["expiration", "strike", "right", "timestamp", "bid", "ask"]},
                    "response": [[20261003, 100, "C", FRESH, 1.4, 1.5]],
                },
            )
        return httpx.Response(200, json=[])

    feed = _feed(handle)
    feed.bind_option_symbols(("NVDA",))
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    assert chain[0].bid == Decimal("1.4")
    assert chain[0].expiration.isoformat() == "2026-10-03"
    assert feed.last_option_status == "200:1"


def test_empty_strike_window_retries_the_full_chain():
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if request.url.path != "/v3/option/snapshot/quote":
            return httpx.Response(200, json=[])
        if "strike_range" in request.url.params:
            return httpx.Response(472, text="NO_DATA")
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "NVDA",
                    "expiration": "2026-10-03",
                    "strike": 100,
                    "right": "CALL",
                    "timestamp": FRESH,
                    "bid": 1.2,
                    "ask": 1.3,
                }
            ],
        )

    feed = _feed(handle)
    feed.bind_option_symbols(("NVDA",))
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    assert chain[0].bid == Decimal("1.2")
    assert any("strike_range" not in item for item in calls)


def test_quote_without_volume_still_builds_a_contract():
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v3/option/snapshot/quote":
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "NVDA",
                    "expiration": "2026-10-03",
                    "strike": 100,
                    "right": "CALL",
                    "timestamp": FRESH,
                    "bid": 1.0,
                    "ask": 1.1,
                }
            ],
        )

    feed = _feed(handle)
    feed.bind_option_symbols(("NVDA",))
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    assert chain[0].volume == 0
    assert chain[0].open_interest == 0
    assert chain[0].last == Decimal("1.05")


def test_option_chain_maps_bid_ask_greeks_and_open_interest():
    handler, _seen = _handler()
    feed = _feed(handler)
    ThetaMarketProvider(feed).load_underlyings(AS_OF)
    chain = ThetaOptionsProvider(feed).load_options(AS_OF)
    assert len(chain) == 1
    contract = chain[0]
    assert contract.underlying == "NVDA"
    assert contract.option_symbol == "NVDA  261003C00100000"
    assert contract.bid == Decimal("1.0")
    assert contract.ask == Decimal("1.1")
    assert contract.last == Decimal("1.05")
    assert contract.volume == 800
    assert contract.open_interest == 500
    assert contract.implied_volatility == Decimal("0.35")
    assert contract.delta == Decimal("0.4")
    assert contract.observed_at.tzinfo == EXCHANGE
    assert feed_status_count() == 1


def feed_status_count() -> int:
    status = feed_status()
    assert status["authenticated"] is True
    assert status["last_success"]
    assert status["last_fetch"]
    assert status["newest_quote"]
    assert status["last_error"] is None
    return status["option_count"]


def test_future_quote_is_excluded_and_stale_quote_fails_the_freshness_gate():
    future_handler, _seen = _handler(nvda_stamp=FUTURE)
    future_rows = ThetaMarketProvider(_feed(future_handler)).load_underlyings(AS_OF)
    assert "NVDA" not in {item.symbol for item in future_rows}

    reset_theta_state()
    stale_handler, _seen = _handler(nvda_stamp=STALE)
    stale = ThetaMarketProvider(_feed(stale_handler)).load_underlyings(AS_OF)
    nvda = next(item for item in stale if item.symbol == "NVDA")
    assert nvda.observed_at < AS_OF
    assert reject_underlying(nvda, loose_config(), AS_OF) == "stale_underlying"


def test_liquid_quote_read_just_after_the_scan_clock_is_kept():
    late = (AS_OF.astimezone(EXCHANGE) + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000")
    handler, _seen = _handler(nvda_stamp=late)
    rows = {item.symbol: item for item in ThetaMarketProvider(_feed(handler)).load_underlyings(AS_OF)}
    assert rows["NVDA"].observed_at == AS_OF
    assert reject_underlying(rows["NVDA"], loose_config(), AS_OF) != "stale_underlying"


def test_prior_history_for_a_core_name_is_read_once_per_session():
    handler, seen = _handler()
    feed = _feed(handler)
    market = ThetaMarketProvider(feed)
    first = {item.symbol: item for item in market.load_underlyings(AS_OF)}
    second = {item.symbol: item for item in market.load_underlyings(AS_OF + timedelta(seconds=60))}
    eod = [symbol for path, symbol in seen if path == "/v3/stock/history/eod" and symbol == "NVDA"]
    assert len(eod) == 1
    assert second["NVDA"].prior_close == first["NVDA"].prior_close


def test_fresh_providers_quote_the_held_and_bought_contracts(monkeypatch):
    from types import SimpleNamespace

    from app.workers import loops

    handler, _seen = _handler()

    def fresh_providers(_settings):
        feed = ThetaFeed(_settings, transport=httpx.MockTransport(handler))
        return SimpleNamespace(market=ThetaMarketProvider(feed), options=ThetaOptionsProvider(feed))

    monkeypatch.setattr(loops, "build_providers", fresh_providers)
    held = loops._held_underlyings([{"symbol": "NVDA261003C00100000", "qty": "1"}])
    assert held == ("NVDA",)
    bundles = loops._loaded_bundles(_settings(), AS_OF, held)
    assert "NVDA261003C00100000" in bundles
    assert bundles["NVDA261003C00100000"]["underlying"].symbol == "NVDA"

    providers = fresh_providers(_settings())
    providers.options.bind_option_symbols(("NVDA",))
    assert [item.option_symbol for item in providers.options.load_options(AS_OF)] == ["NVDA  261003C00100000"]


def test_no_data_on_one_symbol_keeps_the_rest_of_the_snapshot():
    base, seen = _handler()

    def handle(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params.get("symbol", "")
        if request.url.path.startswith("/v3/stock/") and "VIX" in symbol:
            seen.append((request.url.path, symbol))
            return httpx.Response(472, text="NO_DATA")
        return base(request)

    market = ThetaMarketProvider(_feed(handle))
    rows = {item.symbol: item for item in market.load_underlyings(AS_OF)}
    assert "NVDA" in rows
    assert rows["NVDA"].price == Decimal("102")
    assert "VIX" not in rows
    assert feed_status()["last_error"] is None


def test_forbidden_on_one_symbol_keeps_the_rest_of_the_snapshot():
    base, _seen = _handler()

    def handle(request: httpx.Request) -> httpx.Response:
        symbol = request.url.params.get("symbol", "")
        if request.url.path.startswith("/v3/stock/") and "VIX" in symbol:
            return httpx.Response(403, text="FORBIDDEN")
        return base(request)

    market = ThetaMarketProvider(_feed(handle))
    rows = {item.symbol: item for item in market.load_underlyings(AS_OF)}
    assert "NVDA" in rows
    assert rows["NVDA"].price == Decimal("102")
    assert feed_status()["last_error"] is None


def test_terminal_failure_returns_no_fabricated_quotes():
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    market = ThetaMarketProvider(_feed(explode))
    with pytest.raises(ThetaDataError, match="לא זמין"):
        market.load_underlyings(AS_OF)
    status = feed_status()
    assert status["quote_count"] == 0
    assert status["option_count"] == 0
    assert status["last_success"] is None
    assert status["last_error"]
    assert status["authenticated"] is False


class _Capture(StubAI):
    packet = None

    def analyze(self, packet, as_of):
        _Capture.packet = packet
        return super().analyze(packet, as_of)


def test_scan_reaches_run_scan_with_quote_chain_and_regime():
    handler, _seen = _handler()
    feed = _feed(handler)
    _Capture.packet = None
    providers = ProviderSet(
        market=ThetaMarketProvider(feed),
        options=ThetaOptionsProvider(feed),
        news=UnconfiguredProvider("NEWS_PROVIDER"),
    )
    result = execute_scan(providers, loose_config(), _Capture(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())
    assert _Capture.packet is not None
    assert _Capture.packet["underlying"]["symbol"] == "NVDA"
    assert _Capture.packet["underlying"]["price"] == 102.0
    assert _Capture.packet["shortlist"][0]["bid"] == 1.0
    assert _Capture.packet["market_regime"]["spy"]["price"] == 102.0
    assert _Capture.packet["market_regime"]["vix"]["price"] == 18.4
    assert result.recommendations
    assert result.recommendations[0].underlying == "NVDA"


def test_empty_provider_is_unconfigured(monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    providers = build_providers(_settings(thetadata_api_key=""))
    assert providers.market.name == "unconfigured"
    assert providers.options.name == "unconfigured"
    with pytest.raises(ProviderNotConfigured):
        providers.market.load_underlyings(AS_OF)
    with pytest.raises(ProviderNotConfigured):
        providers.options.load_options(AS_OF)


def test_empty_terminal_payload_is_not_a_live_feed():
    feed = ThetaFeed(_settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    feed.bind_symbols(("NVDA",))
    assert ThetaMarketProvider(feed).load_underlyings(AS_OF) == []
    status = feed_status()
    assert status["authenticated"] is False
    assert status["last_success"] is None
    assert status["quote_count"] == 0
    assert status["option_count"] == 0
    providers = ProviderSet(
        market=ThetaMarketProvider(feed),
        options=ThetaOptionsProvider(feed),
        news=UnconfiguredProvider("NEWS_PROVIDER"),
    )
    with pytest.raises(ProviderUnavailable, match="אין ציטוט"):
        execute_scan(providers, loose_config(), None, ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())


def test_probe_is_connected_only_after_quote_and_chain(monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    settings = _settings(thetadata_api_key="", thetadata_base_url="http://theta.local")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    status, _latency, _detail = probe_thetadata(settings, transport=httpx.MockTransport(down))
    assert status == "blocked_by_entitlement"

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    status, _latency, _detail = probe_thetadata(settings, transport=httpx.MockTransport(empty))
    assert status == "no_data"

    def ready(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stock/snapshot/quote"):
            return httpx.Response(200, json=[{"symbol": "NVDA", "timestamp": FRESH, "bid": 101.9, "ask": 102.1}])
        return httpx.Response(
            200,
            json=[{
                "symbol": "NVDA",
                "expiration": "2026-10-03",
                "strike": 100,
                "right": "CALL",
                "timestamp": FRESH,
                "bid": 1.0,
                "ask": 1.1,
            }],
        )

    status, _latency, detail = probe_thetadata(settings, transport=httpx.MockTransport(ready))
    assert status == "connected"
    assert "test-terminal" not in detail


def test_provider_selection_follows_the_saved_key(monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    assert build_providers(_settings(thetadata_api_key="")).market.name == "unconfigured"
    wired = build_providers(_settings())
    assert wired.market.name == "thetadata"
    assert wired.options.name == "thetadata"


def test_http_scan_enters_run_scan_from_terminal_payload(tmp_path, monkeypatch):
    handler, _seen = _handler()
    real_client = httpx.Client

    def factory(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout", 20))

    monkeypatch.setattr("app.providers.thetadata.httpx.Client", factory)
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    monkeypatch.setattr("app.universe.builder.CACHE_PATH", tmp_path / "universe.json")
    monkeypatch.setattr(
        "app.universe.builder.fetch_assets",
        lambda _settings: [{"symbol": "NVDA", "status": "active", "class": "us_equity", "tradable": True, "attributes": ["has_options"]}],
    )
    from app.universe.builder import reset_universe_state

    reset_universe_state()
    database = tmp_path / "beo.db"
    settings = _settings(database_url="sqlite:///" + database.as_posix())
    with TestClient(create_app(settings)) as client:
        scan = client.post("/api/v1/scan")
        assert scan.status_code == 200
        body = scan.json()
        assert body["started"] is True
        assert body["universe"]["source"] == "alpaca-assets"
        assert body["universe"]["filtered"] == 1
        assert "test-terminal" not in scan.text
        status = client.get("/api/v1/system").json()
        assert status["providers"]["market"] == "thetadata"
        assert status["providers"]["options"] == "thetadata"
        assert status["market_status"]["quote_count"] >= 1
        assert status["market_status"]["last_error"] is None
        assert status["paper_only"] is True
