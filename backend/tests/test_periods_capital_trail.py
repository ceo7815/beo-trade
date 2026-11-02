"""Periods, working capital, ILS kill, day trail, SIZE_TOO_SMALL (release 2026.11.03.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics.periods import (
    filter_trades_for_period,
    load_periods,
    public_periods,
    resolve_period,
    trade_matches_period,
)
from app.capital import (
    day_trail_arm_usd,
    day_trail_enabled,
    effective_kill_usd,
    risk_equity,
    working_capital_usd,
)
from app.config.settings import Settings, TradingConfig, load_trading_config
from app.options.day_floor import advance_trail, observe_day_floor
from app.options.risk_limits import day_flatten_reason
from app.positions.admission import admit_buy
from app.schemas.domain import OptionSnapshot

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config"
PERIODS = CONFIG / "periods.json"
TOML_10K = CONFIG / "trading.toml"
TOML_100K = CONFIG / "trading_100k.toml"


def _cfg_10k(**overrides) -> TradingConfig:
    from dataclasses import fields

    base = load_trading_config(TOML_10K)
    if not overrides:
        return base
    data = {item.name: getattr(base, item.name) for item in fields(TradingConfig)}
    data.update(overrides)
    return TradingConfig(**data)


def _cfg_100k() -> TradingConfig:
    return load_trading_config(TOML_100K)


def test_periods_split_at_2026_11_02():
    rows = load_periods(PERIODS)
    assert [row.id for row in rows] == ["paper_100k", "paper_10k_ils"]
    p1, p2 = rows
    assert p1.contains_day(date(2026, 11, 2))
    assert not p1.contains_day(date(2026, 11, 3))
    assert p2.contains_day(date(2026, 11, 3))
    assert not p2.contains_day(date(2026, 11, 2))
    assert public_periods(PERIODS)["current_id"] == "paper_10k_ils"


def test_period_filter_keeps_history_untouched_for_period_1():
    trades = [
        {"symbol": "A", "closed_at": datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc), "pnl": "100"},
        {"symbol": "B", "closed_at": datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc), "pnl": "50"},
        {"symbol": "C", "closed_at": datetime(2026, 10, 15, 20, 0, tzinfo=timezone.utc), "pnl": "10"},
    ]
    p1 = filter_trades_for_period(trades, "paper_100k", path=PERIODS)
    p2 = filter_trades_for_period(trades, "paper_10k_ils", path=PERIODS)
    all_rows = filter_trades_for_period(trades, "all", path=PERIODS)
    assert [row["symbol"] for row in p1] == ["A", "C"]
    assert [row["symbol"] for row in p2] == ["B"]
    assert [row["symbol"] for row in all_rows] == ["A", "B", "C"]


def test_period_matches_by_account_when_tagged():
    period = resolve_period("paper_10k_ils", PERIODS)
    assert period is not None
    # Temporarily treat period as pinned to an account number.
    from dataclasses import replace

    pinned = replace(period, alpaca_account_number="PA123")
    tagged = {"closed_at": datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc), "account_number": "PA123"}
    other = {"closed_at": datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc), "account_number": "OTHER"}
    legacy = {"closed_at": datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)}
    assert trade_matches_period(tagged, pinned) is True
    assert trade_matches_period(other, pinned) is False
    assert trade_matches_period(legacy, pinned) is True  # untagged legacy kept by date


def test_working_capital_caps_equity_on_10k_only():
    cfg = _cfg_10k()
    assert working_capital_usd(cfg) == Decimal("2702.70")
    assert risk_equity(Decimal("100000"), cfg) == Decimal("2702.70")
    assert risk_equity(Decimal("1000"), cfg) == Decimal("1000")
    classic = _cfg_100k()
    assert working_capital_usd(classic) is None
    assert risk_equity(Decimal("100000"), classic) == Decimal("100000")
    assert day_trail_enabled(classic) is False
    assert effective_kill_usd(classic) == Decimal("3500")


def test_ils_kill_and_trail_arm():
    cfg = _cfg_10k()
    assert effective_kill_usd(cfg) == Decimal("108.11")
    assert day_trail_arm_usd(cfg) == Decimal("108.11")
    assert day_flatten_reason(Decimal("2702.70"), Decimal("-108.11"), cfg) == "DAILY_HARD_STOP"
    assert day_flatten_reason(Decimal("2702.70"), Decimal("-50"), cfg) is None
    # 100K profile keeps the $3,500 line.
    assert day_flatten_reason(Decimal("100000"), Decimal("-3500"), _cfg_100k()) == "DAILY_HARD_STOP"


def test_size_too_small_when_one_contract_exceeds_cap():
    cfg = _cfg_10k(normal_position_capital_pct=0.05, hard_position_capital_pct=0.05, risk_per_trade_pct=0.02)
    equity = Decimal("2702.70")
    # ask 8 → premium 800 > 5% of 2702 (~135)
    from app.schemas.domain import OptionRight

    option = OptionSnapshot(
        underlying="NVDA",
        option_symbol="NVDA261103C00100000",
        right=OptionRight.CALL,
        strike=Decimal("100"),
        expiration=date(2026, 11, 3),
        bid=Decimal("7.80"),
        ask=Decimal("8.00"),
        last=Decimal("7.90"),
        volume=500,
        open_interest=1000,
        observed_at=datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc),
        delta=Decimal("0.45"),
        gamma=Decimal("0.01"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.05"),
        implied_volatility=Decimal("0.40"),
    )
    decision = admit_buy(
        option,
        equity,
        10,
        cfg,
        positions=[],
        orders=[],
        state_risk={},
        open_premium=Decimal("0"),
        sector_premium=Decimal("0"),
        buying_power=equity,
        open_positions=0,
    )
    assert decision.quantity == 0
    assert decision.reason == "SIZE_TOO_SMALL"


def test_day_trail_arm_floor_trail_trigger_and_reset():
    arm = Decimal("108.11")
    initial = (arm * Decimal("0.5")).quantize(Decimal("0.01"))
    trail = Decimal("0.30")
    quiet = advance_trail(None, "2026-11-03", Decimal("50"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert quiet["armed"] is False
    assert quiet["closed"] is False
    armed = advance_trail(quiet, "2026-11-03", Decimal("108.11"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert armed["armed"] is True
    assert Decimal(armed["floor"]) == Decimal("75.68")  # max(initial, arm*0.7)
    peaked = advance_trail(armed, "2026-11-03", Decimal("400"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert Decimal(peaked["peak"]) == Decimal("400.00")
    assert Decimal(peaked["floor"]) == Decimal("280.00")  # 400 * 0.7
    # Still above floor
    held = advance_trail(peaked, "2026-11-03", Decimal("300"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert held["closed"] is False
    closed = advance_trail(peaked, "2026-11-03", Decimal("280"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert closed["closed"] is True
    assert closed["reason"] == "DAY_TRAIL"
    bounced = advance_trail(closed, "2026-11-03", Decimal("500"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert bounced["closed"] is True
    nxt = advance_trail(closed, "2026-11-04", Decimal("10"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert nxt["armed"] is False
    assert nxt["closed"] is False


def test_day_trail_persists_across_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
        trading_config_path=str(TOML_10K),
    )
    moment = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    first = observe_day_floor(settings, Decimal("400"), moment, True)
    assert first["armed"] is True
    assert Decimal(first["floor"]) == Decimal("280.00")
    # New process view: read from disk without writing a higher peak.
    again = observe_day_floor(settings, Decimal("350"), moment, True)
    assert again["armed"] is True
    assert again["closed"] is False
    assert Decimal(again["floor"]) == Decimal("280.00")
    tripped = observe_day_floor(settings, Decimal("280"), moment, True)
    assert tripped["closed"] is True
    assert tripped["reason"] == "DAY_TRAIL"


def test_100k_ladder_still_works_when_profile_recovered(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
        trading_config_path=str(TOML_100K),
    )
    moment = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    view = observe_day_floor(settings, Decimal("4000"), moment, True)
    assert view["armed"] is True
    assert view["floor"] == "2000.00"
    assert view.get("mode") != "trail"


def test_period_1_dates_unchanged_vs_all_for_precutoff_only_book():
    """History through 2026-11-02 equals period-1 filter when no later trades exist."""
    trades = [
        {"symbol": "X", "closed_at": datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc), "pnl": "1"},
        {"symbol": "Y", "closed_at": datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc), "pnl": "2"},
    ]
    assert filter_trades_for_period(trades, "paper_100k", path=PERIODS) == filter_trades_for_period(trades, "all", path=PERIODS)
