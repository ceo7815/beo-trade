from datetime import date
from decimal import Decimal

from app.analytics.finance import trading_day_pnl
from app.config.settings import TradingConfig
from app.options.risk_limits import exposure_reason
from app.readiness.prepayment import trading_check


def test_same_day_funding_is_not_trading_pnl():
    pnl = trading_day_pnl(
        "110000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "10000", "date": "2026-10-02"}],
        date(2026, 10, 2),
    )
    assert pnl == Decimal("0")


def test_yesterday_funding_does_not_change_today():
    pnl = trading_day_pnl(
        "101000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "5000", "date": "2026-10-01"}],
        date(2026, 10, 2),
    )
    assert pnl == Decimal("1000")


def test_missing_funding_amount_blocks_the_baseline():
    assert trading_day_pnl(
        "110000",
        "100000",
        [{"activity_type": "JNLC", "date": "2026-10-02"}],
        date(2026, 10, 2),
    ) is None


def test_exposure_is_calculated_before_a_buy():
    config = TradingConfig()
    equity = Decimal("100000")
    cap = equity * Decimal(str(config.max_total_open_exposure_pct))
    assert exposure_reason(equity, cap, Decimal("1"), config) == "EXPOSURE_LIMIT"
    assert exposure_reason(equity, cap - Decimal("10"), Decimal("10"), config) is None


def test_prepayment_funding_check_is_zero_after_a_deposit():
    assert trading_check() == Decimal("0")
