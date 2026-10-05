from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.config.settings import TradingConfig
from app.providers.base import ProviderUnavailable
from app.providers.registry import ProviderSet
from app.providers.thetadata import ThetaFeed, ThetaMarketProvider, ThetaOptionsProvider, reset_theta_state
from app.providers.unconfigured import UnconfiguredProvider
from app.recommendations.live_scan import execute_scan
from app.universe.builder import (
    FIXED_WATCHLIST,
    UniverseUnavailable,
    filter_optionable_equities,
    load_universe,
    next_scan_symbols,
    reset_universe_state,
    rank_symbols,
    take_batch,
)
from tests.test_engine import AS_OF, StubAI, ledger, loose_config
from tests.test_thetadata import _handler, _settings

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)


def _row(symbol: str, **overrides) -> dict:
    payload = {
        "symbol": symbol,
        "status": "active",
        "class": "us_equity",
        "tradable": True,
        "attributes": ["has_options"],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr("app.universe.builder.fetch_activity_scores", lambda _settings: {})
    reset_universe_state()
    yield
    reset_universe_state()


def test_filter_keeps_only_active_tradable_optionable_us_equities():
    rows = [
        _row("AAA"),
        _row("BBB", status="inactive"),
        _row("CCC", tradable=False),
        _row("DDD", attributes=[]),
        _row("EEE", **{"class": "crypto"}),
        _row("BRK.B"),
        _row("TOO-LONG-NAME"),
        {"symbol": "FFF"},
    ]
    symbols, discovered = filter_optionable_equities(rows, ("AAA",))
    assert discovered == 8
    assert symbols == ("BRK.B",)


def test_batch_rotates_and_does_not_force_a_count():
    assert take_batch(("A", "B", "C", "D"), 2, 0) == (("A", "B"), 2)
    assert take_batch(("A", "B", "C", "D"), 2, 2) == (("C", "D"), 0)
    assert take_batch((), 40, 0) == ((), 0)
    assert take_batch(("A",), 40, 3) == (("A",), 0)


def test_cache_skips_refresh_and_failure_is_degraded_without_the_old_list(tmp_path, monkeypatch):
    path = tmp_path / "universe.json"
    calls = {"n": 0}

    def fetcher(_settings):
        calls["n"] += 1
        return [_row("AAA"), _row("BBB"), _row("CCC", status="inactive")]

    monkeypatch.setattr("app.universe.builder.fetch_assets", fetcher)
    config = TradingConfig(universe_refresh_seconds=3600, universe_exclusions=())
    from app.config.settings import Settings

    settings = Settings(app_env="local", auth_required=False, trading_mode="PAPER")
    first = load_universe(settings, config, NOW, path)
    assert first.status == "READY"
    assert first.source == "alpaca-assets"
    assert first.symbols == ("AAA", "BBB")
    assert first.discovered == 3
    assert calls["n"] == 1
    second = load_universe(settings, config, NOW + timedelta(minutes=5), path)
    assert calls["n"] == 1
    assert second.symbols == ("AAA", "BBB")

    def down(_settings):
        raise UniverseUnavailable("down")

    monkeypatch.setattr("app.universe.builder.fetch_assets", down)
    stale = load_universe(settings, TradingConfig(universe_refresh_seconds=1), NOW + timedelta(hours=2), path)
    assert stale.status == "DEGRADED"
    assert "DEGRADED" in stale.detail
    assert stale.symbols == ("AAA", "BBB")
    assert stale.symbols != FIXED_WATCHLIST


def test_provider_failure_without_cache_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr("app.universe.builder.fetch_assets", lambda _settings: (_ for _ in ()).throw(UniverseUnavailable("down")))
    from app.config.settings import Settings

    settings = Settings(app_env="local", auth_required=False, trading_mode="PAPER")
    with pytest.raises(UniverseUnavailable, match="אין רשימת גיבוי"):
        load_universe(settings, TradingConfig(universe_refresh_seconds=10), NOW, tmp_path / "missing.json")


def test_scan_uses_the_dynamic_universe_and_allows_zero_candidates(tmp_path, monkeypatch):
    path = tmp_path / "universe.json"
    monkeypatch.setattr("app.universe.builder.CACHE_PATH", path)
    monkeypatch.setattr(
        "app.universe.builder.fetch_assets",
        lambda _settings: [_row("NVDA"), _row("ZZZ"), _row("TSLA", status="inactive"), _row("OLD", attributes=[])],
    )
    handler, seen = _handler()
    feed = ThetaFeed(_settings(), transport=httpx.MockTransport(handler))
    providers = ProviderSet(
        market=ThetaMarketProvider(feed),
        options=ThetaOptionsProvider(feed),
        news=UnconfiguredProvider("NEWS_PROVIDER"),
    )
    result = execute_scan(providers, loose_config(), StubAI(), ledger(), AS_OF, AS_OF.date(), Decimal("1000"), set())
    requested = " ".join(symbol for _path, symbol in seen)
    assert "NVDA" in requested
    assert "ZZZ" in requested
    assert "TSLA" not in requested
    assert "AMD" not in requested
    assert result.recommendations
    assert result.ai_calls == 1
    assert result.option_symbols == 1
    assert result.universe["source"] == "alpaca-assets"
    assert result.universe["discovered"] == 4
    assert result.universe["filtered"] == 2
    assert any(symbol == "ZZZ" and reason == "no_quote" for symbol, reason, _stage in result.rejections)

    quiet, quiet_seen = _handler()
    blocked = ThetaFeed(_settings(), transport=httpx.MockTransport(quiet))
    blocked.bind_symbols(("NVDA",))
    blocked_providers = ProviderSet(
        market=ThetaMarketProvider(blocked),
        options=ThetaOptionsProvider(blocked),
        news=UnconfiguredProvider("NEWS_PROVIDER"),
    )
    empty = execute_scan(
        blocked_providers,
        TradingConfig(min_relative_volume=100, min_data_freshness_seconds=120, universe_min_price=1),
        StubAI(),
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal("1000"),
        set(),
    )
    assert empty.recommendations == []
    assert empty.ai_calls == 0
    assert empty.option_symbols == 0
    assert all(path != "/v3/option/snapshot/quote" for path, _symbol in quiet_seen)
    assert empty.rejections


def test_liquid_names_are_scanned_before_the_alphabetical_remainder():
    ranked = rank_symbols(("AAA", "BBB", "CCC"), {"CCC": 2_000_000, "AAA": 10})
    assert ranked == ("CCC", "BBB", "AAA")
    assert take_batch(ranked, 2, 0)[0] == ("CCC", "BBB")


def test_next_batch_comes_from_the_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("app.universe.builder.fetch_assets", lambda _settings: [_row(symbol) for symbol in ("AAA", "BBB", "CCC")])
    monkeypatch.setattr("app.universe.builder.fetch_activity_scores", lambda _settings: {})
    from app.config.settings import Settings

    settings = Settings(app_env="local", auth_required=False, trading_mode="PAPER")
    book, batch = next_scan_symbols(settings, TradingConfig(universe_max_symbols_per_scan=2), NOW, tmp_path / "universe.json")
    assert book.filtered == 3
    assert batch == ("AAA", "BBB")
    _book, second = next_scan_symbols(settings, TradingConfig(universe_max_symbols_per_scan=2, universe_refresh_seconds=3600), NOW, tmp_path / "universe.json")
    assert second == ("CCC", "AAA")
    reset_theta_state()
    with pytest.raises(ProviderUnavailable, match="יקום"):
        feed = ThetaFeed(_settings(), transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[])))
        ThetaMarketProvider(feed).load_underlyings(AS_OF)
