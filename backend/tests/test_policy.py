from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import TradingConfig
from app.options.risk_limits import daily_loss_reason, daily_loss_state
from app.paper_trading.engine import exit_signal
from app.policy.sizing import size_position
from app.schemas.domain import OptionRight, OptionSnapshot, PaperFill, UnderlyingSnapshot

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
EQUITY = Decimal("100000")


def _option(ask: str = "5.00", bid: str = "4.90") -> OptionSnapshot:
    return OptionSnapshot(
        "NVDA",
        "NVDA  261002C00100000",
        OptionRight.CALL,
        Decimal("100"),
        NOW.date(),
        Decimal(bid),
        Decimal(ask),
        Decimal(ask),
        800,
        500,
        NOW,
        Decimal("0.4"),
        Decimal("0.4"),
        Decimal("0.02"),
        Decimal("-0.02"),
        Decimal("0.1"),
    )


def _fill(entry: str = "5.00", favorable: str = "0") -> PaperFill:
    return PaperFill("t", "r", "NVDA  261002C00100000", 1, Decimal(entry), NOW, max_favorable=Decimal(favorable), delta_at_entry=Decimal("0.4"), underlying_at_entry=Decimal("100"))


def _underlying(price: str = "100") -> UnderlyingSnapshot:
    return UnderlyingSnapshot("NVDA", Decimal(price), Decimal(price), Decimal(price), Decimal(price), Decimal(price), 2_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal(price), NOW, True)


def test_official_example_sizes_ten_contracts_from_risk():
    plan = size_position(_option(), EQUITY, TradingConfig())
    assert plan.risk_budget == Decimal("2000")
    assert plan.contract_cost == Decimal("500")
    assert plan.risk_per_contract == Decimal("200")
    assert plan.by_risk == 10
    assert plan.by_capital == 14
    assert plan.quantity == 10
    assert plan.limiter == "risk"
    assert plan.quantity * plan.risk_per_contract <= EQUITY * Decimal("0.02")
    assert plan.quantity * plan.contract_cost <= EQUITY * Decimal("0.07")
    assert plan.quantity * plan.contract_cost <= EQUITY * Decimal("0.10")


def test_caps_cannot_be_exceeded():
    config = TradingConfig()
    exposed = size_position(_option(), EQUITY, config, open_premium=Decimal("35000"))
    assert exposed.quantity == 0
    sector = size_position(_option(), EQUITY, config, sector_premium=Decimal("10000"))
    assert sector.quantity == 0
    aggregate = size_position(_option(), EQUITY, config, open_planned_risk=Decimal("15000"))
    assert aggregate.quantity == 0
    full = size_position(_option(), EQUITY, config, open_positions=10)
    assert full.quantity == 0
    same = size_position(_option(), EQUITY, config, same_underlying_positions=1)
    assert same.quantity == 0
    power = size_position(_option(), EQUITY, config, buying_power=Decimal("400"))
    assert power.quantity == 0


def test_property_caps_hold_across_premiums_and_equity():
    config = TradingConfig()
    for equity in (Decimal("10000"), Decimal("25000"), Decimal("100000"), Decimal("250000")):
        for ask in (Decimal("0.50"), Decimal("1"), Decimal("2.50"), Decimal("5"), Decimal("8"), Decimal("12"), Decimal("20")):
            bid = ask - Decimal("0.04")
            plan = size_position(_option(format(ask, "f"), format(bid, "f")), equity, config)
            assert plan.quantity * plan.risk_per_contract <= equity * Decimal("0.02")
            assert plan.quantity * plan.contract_cost <= equity * Decimal("0.07")
            assert plan.quantity * plan.contract_cost <= equity * Decimal("0.10")
            assert plan.quantity * plan.contract_cost + Decimal("0") <= equity * Decimal("0.35")


def test_daily_levels():
    config = TradingConfig()
    assert daily_loss_state(EQUITY, Decimal("-2500"), config) == "warning"
    assert daily_loss_reason(EQUITY, Decimal("-2500"), config) is None
    assert daily_loss_reason(EQUITY, Decimal("-4000"), config) == "DAILY_LOSS_LIMIT_REACHED"
    assert daily_loss_reason(EQUITY, Decimal("-5000"), config) == "DAILY_HARD_STOP"
    assert daily_loss_state(EQUITY, Decimal("0"), config) == "ok"


def test_exit_modes():
    config = TradingConfig()
    underlying = _underlying()
    assert exit_signal(_fill(), _option("3.10", "3.00"), underlying, NOW, config, True).reason == "STOP"
    protected = exit_signal(_fill(favorable="2.00"), _option("4.50", "4.40"), underlying, NOW, config, True)
    assert protected.reason == "PROTECTED_STOP"
    trail = exit_signal(_fill(favorable="4.00"), _option("7.10", "7.00"), underlying, NOW, config, True)
    assert trail.reason == "TRAILING"
    winner = exit_signal(_fill(favorable="4.00"), _option("8.60", "8.50"), underlying, NOW, config, True)
    assert winner is None
    held = _fill()
    assert exit_signal(held, _option("5.00", "5.10"), underlying, NOW + timedelta(minutes=90), config, True).reason == "TIME_STOP"
    one_dte = replace(_option("5.00", "5.10"), expiration=(NOW + timedelta(days=1)).date())
    assert exit_signal(_fill(), one_dte, underlying, NOW + timedelta(minutes=180), config, True).reason == "TIME_STOP"
    close = NOW + timedelta(hours=1)
    assert exit_signal(_fill(), _option(), underlying, close - timedelta(minutes=10), config, True, close).reason == "SESSION_CLOSE"
    invalid = exit_signal(_fill(), _option(), _underlying("98"), NOW, config, True)
    assert invalid.reason == "INVALIDATION"
