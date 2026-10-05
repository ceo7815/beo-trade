from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import TradingConfig
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
    ]
    profile = _filter_observation(("PASS", "STALE", "CHEAP", "QUIET", "FLATVOL", "FLAT", "DEAD", "MISSING"), rows, config, NOW)
    counts = profile["counts"]
    assert counts["symbols_requested"] == 8
    assert counts["quotes_received"] == 7
    assert counts["quotes_fresh"] == 6
    assert counts["rejected_no_quote"] == 1
    assert counts["rejected_stale"] == 1
    assert counts["rejected_price"] == 2
    assert counts["rejected_volume"] == 1
    assert counts["rejected_relative_volume"] == 1
    assert counts["rejected_move"] == 1
    assert counts["passed_underlying"] == 1
    stale = next(item for item in profile["samples"] if item["symbol"] == "STALE")
    assert stale["quote_age_seconds"] == 45
    assert stale["relative_volume"] == 2.4
