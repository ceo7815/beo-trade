from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import TradingConfig
from app.quant.filters import rank_underlyings, reject_underlying
from app.recommendations.live_scan import _filter_observation
from app.schemas.domain import UnderlyingSnapshot

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)


def _snap(symbol: str, **overrides) -> UnderlyingSnapshot:
    payload = {
        "symbol": symbol,
        "price": Decimal("100"),
        "open": Decimal("99"),
        "high": Decimal("101"),
        "low": Decimal("98"),
        "close": Decimal("100"),
        "volume": 2_000_000,
        "relative_volume": Decimal("2.4"),
        "change_percent": Decimal("1.2"),
        "change_dollars": Decimal("1.2"),
        "prior_close": Decimal("98.8"),
        "observed_at": NOW,
        "session_ok": True,
    }
    payload.update(overrides)
    return UnderlyingSnapshot(**payload)


def test_filter_observation_counts_the_first_failing_rule_and_keeps_the_raw_values():
    config = TradingConfig()
    rows = [
        _snap("PASS"),
        _snap("STALE", observed_at=NOW - timedelta(seconds=45)),
        _snap("CHEAP", price=Decimal("4")),
        _snap("QUIET", volume=100_000),
        _snap("FLATVOL", relative_volume=Decimal("0.4")),
        _snap("FLAT", change_percent=Decimal("0.1")),
        _snap("DEAD", price=Decimal("0"), volume=0),
        _snap("EMPTY", volume=0),
    ]
    profile = _filter_observation(
        ("PASS", "STALE", "CHEAP", "QUIET", "FLATVOL", "FLAT", "DEAD", "EMPTY", "MISSING"),
        rows,
        config,
        NOW,
    )
    counts = profile["counts"]
    assert counts["symbols_requested"] == 9
    assert counts["quotes_received"] == 8
    assert counts["quotes_fresh"] == 7
    assert counts["rejected_no_quote"] == 1
    assert counts["rejected_stale"] == 1
    assert counts["rejected_price"] == 2
    assert counts["rejected_volume"] == 1
    assert counts["rejected_relative_volume"] == 1
    assert counts["rejected_move"] == 1
    assert counts["passed_underlying"] == 2
    stale = next(item for item in profile["samples"] if item["symbol"] == "STALE")
    assert stale["quote_age_seconds"] == 45
    assert stale["relative_volume"] == 2.4
    quiet = next(item for item in profile["samples"] if item["symbol"] == "QUIET")
    assert quiet["reason"] == "passed"
    assert quiet["volume"] == 100_000
    assert quiet["would_fail_old_volume_floor"] is True
    passed = next(item for item in profile["samples"] if item["symbol"] == "PASS")
    assert passed["would_fail_old_volume_floor"] is False


def test_sub_million_volume_is_kept_for_the_option_chain():
    from app.recommendations.live_scan import _liquidity_stage

    class _Feed:
        _symbols = ("FWONA",)

    class _Market:
        feed = _Feed()

    class _Providers:
        market = _Market()

    kept, rejections = _liquidity_stage([_snap("FWONA", volume=93_329)], _Providers(), TradingConfig(), NOW)
    assert [item.symbol for item in kept] == ["FWONA"]
    assert rejections == []


def test_volume_below_one_million_stays_eligible_and_ranks_behind_stronger_names():
    config = TradingConfig()
    thin = _snap("FWONA", volume=93_329, relative_volume=Decimal("2.56"), change_percent=Decimal("4.5"))
    stronger = _snap("LIQ", volume=2_000_000, relative_volume=Decimal("3.1"), change_percent=Decimal("1.1"))
    ordinary = _snap("NAKA", volume=121_115, relative_volume=Decimal("1.13"), change_percent=Decimal("7.9"))
    opened = _snap("FGRU", relative_volume=Decimal("0.55"), change_percent=Decimal("1.03"))
    quiet = _snap("QUIETRV", relative_volume=Decimal("0.49"), change_percent=Decimal("2"))
    assert reject_underlying(thin, config, NOW) is None
    assert reject_underlying(ordinary, config, NOW) is None
    assert reject_underlying(opened, config, NOW) is None
    assert reject_underlying(quiet, config, NOW) == "relative_volume"
    assert reject_underlying(_snap("EMPTY", volume=0), config, NOW) == "underlying_liquidity"
    ranked = rank_underlyings([ordinary, thin, stronger], NOW, config.relative_volume_priority)
    assert [item.symbol for item in ranked] == ["LIQ", "FWONA", "NAKA"]
