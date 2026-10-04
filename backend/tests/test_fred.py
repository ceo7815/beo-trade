from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.integrations.probes import probe_fred
from app.providers.fred import FredFeed, fred_status, reset_fred_state
from app.providers.registry import ProviderSet, build_providers
from app.providers.thetadata import ThetaMarketProvider, ThetaOptionsProvider
from app.providers.unconfigured import UnconfiguredProvider
from app.recommendations.live_scan import execute_scan
from tests.test_engine import AS_OF, StubAI, ledger, loose_config
from tests.test_thetadata import _feed, _handler, _settings


@pytest.fixture(autouse=True)
def _clean():
    reset_fred_state()
    yield
    reset_fred_state()


def _fred_response(series_id: str, value: str, observed: str, title: str) -> dict:
    return {
        "seriess": [{"id": series_id, "title": title, "units_short": "Percent"}],
        "observations": [{"date": observed, "value": value}],
    }


def _transport(payloads: dict[str, dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["series_id"]
        assert request.url.params["file_type"] == "json"
        assert request.url.params["api_key"] == "test-fred-key"
        body = payloads.get(series_id)
        if body is None:
            return httpx.Response(200, json={"observations": []})
        if request.url.path.endswith("/series/observations"):
            assert request.url.params["realtime_start"] == request.url.params["realtime_end"] == "2026-10-01"
            assert request.url.params["observation_end"] == "2026-10-01"
            return httpx.Response(200, json={"observations": body["observations"]})
        return httpx.Response(200, json={"seriess": body["seriess"]})

    return httpx.MockTransport(handler)


def test_empty_provider_is_unconfigured(monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    providers = build_providers(_settings(thetadata_api_key="", fred_api_key=""))
    assert providers.macro is None
    assert providers.macro_status()["provider"] == "unconfigured"
    assert providers.macro_status()["authenticated"] is None


def test_terminal_style_failure_is_not_connected():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    feed = FredFeed(_settings(fred_api_key="test-fred-key"), transport=httpx.MockTransport(down))
    assert feed.load(AS_OF) is None
    status = fred_status()
    assert status["authenticated"] is False
    assert status["series_count"] == 0
    assert status["last_success"] is None
    assert "test-fred-key" not in (status["last_error"] or "")


def test_missing_observation_is_not_connected():
    payloads = {"FEDFUNDS": {"seriess": [{"id": "FEDFUNDS", "title": "Federal Funds Effective Rate", "units_short": "Percent"}], "observations": [{"date": "2026-09-01", "value": "."}]}}
    feed = FredFeed(_settings(fred_api_key="test-fred-key"), transport=_transport(payloads))
    assert feed.load(AS_OF) is None
    assert fred_status()["authenticated"] is False
    assert fred_status()["series_count"] == 0


def test_valid_observation_and_future_value_is_excluded():
    payloads = {
        "FEDFUNDS": _fred_response("FEDFUNDS", "4.22", "2026-09-01", "Federal Funds Effective Rate"),
        "DGS2": _fred_response("DGS2", "3.55", "2026-10-02", "2-Year Treasury"),
        "DGS10": _fred_response("DGS10", "4.10", "2026-09-30", "10-Year Treasury"),
        "T10Y2Y": _fred_response("T10Y2Y", "0.55", "2026-09-30", "10-Year Minus 2-Year"),
    }
    feed = FredFeed(_settings(fred_api_key="test-fred-key"), transport=_transport(payloads))
    snapshot = feed.load(AS_OF)
    assert snapshot is not None
    by_id = {item["id"]: item for item in snapshot["series"]}
    assert by_id["FEDFUNDS"]["value"] == 4.22
    assert by_id["FEDFUNDS"]["units"] == "Percent"
    assert by_id["FEDFUNDS"]["date"] == "2026-09-01"
    assert "DGS2" not in by_id
    assert fred_status()["authenticated"] is True
    assert fred_status()["series_count"] == 3


def test_probe_requires_a_numeric_observation(monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    settings = _settings(fred_api_key="test-fred-key")

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"observations": [{"date": "2026-09-01", "value": "."}]})

    status, _latency, _detail = probe_fred(settings, transport=httpx.MockTransport(empty))
    assert status == "no_data"

    def ready(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fred/series/observations"
        assert request.url.params["series_id"] == "FEDFUNDS"
        return httpx.Response(200, json={"observations": [{"date": "2026-09-01", "value": "4.22"}]})

    status, _latency, detail = probe_fred(settings, transport=httpx.MockTransport(ready))
    assert status == "connected"
    assert "test-fred-key" not in detail


class _Capture(StubAI):
    packet = None

    def analyze(self, packet, as_of):
        _Capture.packet = packet
        return super().analyze(packet, as_of)


def test_scan_packet_receives_macro_context():
    handler, _seen = _handler()
    market = _feed(handler)
    payloads = {
        "FEDFUNDS": _fred_response("FEDFUNDS", "4.22", "2026-09-01", "Federal Funds Effective Rate"),
        "DGS10": _fred_response("DGS10", "4.10", "2026-09-30", "10-Year Treasury"),
    }
    _Capture.packet = None
    providers = ProviderSet(
        market=ThetaMarketProvider(market),
        options=ThetaOptionsProvider(market),
        news=UnconfiguredProvider("NEWS_PROVIDER"),
        macro=FredFeed(_settings(fred_api_key="test-fred-key"), transport=_transport(payloads)),
    )
    execute_scan(providers, loose_config(), _Capture(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())
    assert _Capture.packet is not None
    assert _Capture.packet["macro"]["source"] == "fred"
    rates = {item["id"]: item["value"] for item in _Capture.packet["macro"]["series"]}
    assert rates["FEDFUNDS"] == 4.22
    assert rates["DGS10"] == 4.10
    assert "test-fred-key" not in str(_Capture.packet)
