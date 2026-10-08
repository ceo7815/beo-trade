from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.analytics.levels import position_levels
from app.config.settings import TradingConfig

NOW = datetime(2026, 10, 8, 18, 10, tzinfo=timezone.utc)
TODAY = date(2026, 10, 8)


def _trade(current: str) -> dict:
    return {
        "symbol": "QQQ261009P00744000",
        "expiration": "2026-10-09",
        "qty": "17",
        "entry_price": "2.99",
        "current_price": current,
        "opened_at": "2026-10-08T17:51:20+00:00",
    }


def test_a_losing_position_is_at_risk_with_the_initial_stop():
    levels = position_levels(_trade("2.41"), None, TradingConfig(), NOW, TODAY)
    assert levels["stage"] == "AT_RISK"
    assert levels["stop"] == "1.79"
    assert levels["active_exit"] == "1.79"
    assert levels["pnl_at_active_exit"] == "-2033.20"
    assert levels["dte"] == 1
    assert levels["time_limit_minutes"] == 180
    assert levels["time_exit_at"] == "2026-10-08T20:51:20+00:00"
    assert levels["time_exit_passed"] is False


def test_a_stored_peak_turns_on_the_trailing_stop_and_locks_profit():
    state = SimpleNamespace(peak_option_price=5.00, protected_mode=True, trailing_active=True, entry_time=datetime(2026, 10, 8, 17, 51, 20, tzinfo=timezone.utc))
    levels = position_levels(_trade("4.60"), state, TradingConfig(), NOW, TODAY)
    assert levels["stage"] == "TRAILING"
    assert levels["peak"] == "5.00"
    assert levels["trail_trigger"] == "4.25"
    assert levels["active_exit"] == "4.25"
    assert levels["pnl_at_active_exit"] == "2142.00"
    assert levels["from_record"] is True


def test_a_gain_past_one_r_protects_the_position_without_a_record():
    levels = position_levels(_trade("4.30"), None, TradingConfig(), NOW, TODAY)
    assert levels["stage"] == "PROTECTED"
    assert levels["active_exit"] == "2.69"


def test_no_entry_price_means_no_levels():
    assert position_levels({"symbol": "X"}, None, TradingConfig(), NOW, TODAY) == {"available": False}
