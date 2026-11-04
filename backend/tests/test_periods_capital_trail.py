"""Periods, equity % brakes, tiers, DAY_TRAIL, kill-first (release 2026.11.03.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

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
    day_trail_protect_usd,
    effective_kill_usd,
    resolve_tier,
    risk_equity,
    working_capital_usd,
)
from app.config.settings import Settings, TradingConfig, load_trading_config
from app.options.day_floor import advance_trail, observe_day_floor
from app.options.risk_limits import daily_loss_state, day_flatten_reason
from app.positions.admission import admit_buy
from app.schemas.domain import OptionSnapshot

import pytest


@pytest.fixture(autouse=True)
def _freeze_fx(monkeypatch):
    """Tests assert against config 3.14; do not call Frankfurter."""
    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)


CONFIG = Path(__file__).resolve().parents[1] / "app" / "config"
PERIODS = CONFIG / "periods.json"
TOML_10K = CONFIG / "trading_10k.toml"
TOML_100K = CONFIG / "trading_100k.toml"

WC = Decimal("3184.71")  # 10000 / 3.14


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
    assert [row.id for row in rows] == ["paper_100k", "paper_10k_ils", "paper_20k_ils"]
    p1, p2, p3 = rows
    assert p1.contains_day(date(2026, 11, 2))
    assert not p1.contains_day(date(2026, 11, 3))
    assert p2.contains_day(date(2026, 11, 3))
    assert not p2.contains_day(date(2026, 11, 4))
    assert p3.contains_day(date(2026, 11, 4))
    assert not p3.contains_day(date(2026, 11, 3))
    assert public_periods(PERIODS)["current_id"] == "paper_20k_ils"
    assert p2.trading is False
    assert p3.trading is True
    assert p1.read_only is True
    assert Decimal(str(p2.capital_basis_usd)) == WC
    assert Decimal(str(p3.capital_basis_usd)) == (Decimal("20000") / Decimal("3.14")).quantize(Decimal("0.01"))


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
    from dataclasses import replace

    pinned = replace(period, alpaca_account_number="PA123")
    tagged = {"closed_at": datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc), "account_number": "PA123"}
    other = {"closed_at": datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc), "account_number": "OTHER"}
    legacy = {"closed_at": datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)}
    assert trade_matches_period(tagged, pinned) is True
    assert trade_matches_period(other, pinned) is False
    assert trade_matches_period(legacy, pinned) is True


def test_working_capital_and_live_equity_no_cap():
    cfg = _cfg_10k()
    assert working_capital_usd(cfg) == WC
    # Live equity is used uncapped for % brakes (tiers handle scale).
    assert risk_equity(Decimal("100000"), cfg) == Decimal("100000")
    assert risk_equity(Decimal("1000"), cfg) == Decimal("1000")
    classic = _cfg_100k()
    assert working_capital_usd(classic) is None
    assert risk_equity(Decimal("100000"), classic) == Decimal("100000")
    assert day_trail_enabled(classic) is False
    assert effective_kill_usd(classic) == Decimal("3500")


def test_kill_arm_protect_at_10k_and_20k_equity():
    cfg = _cfg_10k()
    # 10K ₪ basis ≈ $3184.71
    assert effective_kill_usd(cfg, WC) == Decimal("254.78")  # 8%
    assert day_trail_arm_usd(cfg, WC) == Decimal("191.08")  # 6%
    assert day_trail_protect_usd(cfg, WC) == Decimal("143.31")  # 4.5%
    assert day_flatten_reason(WC, Decimal("-254.78"), cfg) == "DAILY_HARD_STOP"
    assert day_flatten_reason(WC, Decimal("-50"), cfg) is None

    # 20K ₪ ≈ $6369.43 at 3.14 — still tier 1 (≤25K)
    eq20 = (Decimal("20000") / Decimal("3.14")).quantize(Decimal("0.01"))
    assert resolve_tier(cfg, eq20).name == "small_le_25k"
    assert effective_kill_usd(cfg, eq20) == (eq20 * Decimal("0.08")).quantize(Decimal("0.01"))
    assert day_trail_arm_usd(cfg, eq20) == (eq20 * Decimal("0.06")).quantize(Decimal("0.01"))
    assert day_trail_protect_usd(cfg, eq20) == (eq20 * Decimal("0.045")).quantize(Decimal("0.01"))


def test_tier_switch_mid_and_large():
    cfg = _cfg_10k()
    # 50K ₪ → mid provisional
    eq_mid = (Decimal("50000") / Decimal("3.14")).quantize(Decimal("0.01"))
    mid = resolve_tier(cfg, eq_mid)
    assert mid.name == "mid_25k_100k"
    assert mid.provisional is True
    assert mid.per_trade_pct == Decimal("0.10")
    assert mid.kill_pct == Decimal("0.06")
    assert day_trail_enabled(cfg, eq_mid) is True

    # 150K ₪ → 100K rules, no trail
    eq_big = (Decimal("150000") / Decimal("3.14")).quantize(Decimal("0.01"))
    big = resolve_tier(cfg, eq_big)
    assert big.name == "large_100k_rules"
    assert big.day_trail is False
    assert day_trail_enabled(cfg, eq_big) is False
    assert big.per_trade_pct == Decimal("0.07")


def test_kill_fires_before_hard_stop_pct():
    cfg = _cfg_10k()
    equity = WC
    kill = effective_kill_usd(cfg, equity)
    # At kill loss, flatten via kill; hard_stop % (15%) is still far away.
    assert kill < equity * Decimal(str(cfg.daily_hard_stop_pct))
    assert kill < equity * Decimal(str(cfg.daily_entry_stop_pct))
    assert kill < equity * Decimal(str(cfg.daily_warning_pct))
    assert day_flatten_reason(equity, -kill, cfg) == "DAILY_HARD_STOP"
    # Percent hard stop alone would need 15% — daily_loss_state stays ok near kill.
    mild = -kill + Decimal("1")
    assert daily_loss_state(equity, mild, cfg) == "ok"
    assert day_flatten_reason(equity, mild, cfg) is None


def test_size_too_small_when_one_contract_exceeds_cap():
    cfg = _cfg_10k(normal_position_capital_pct=0.05, hard_position_capital_pct=0.05, risk_per_trade_pct=0.02,
                  capital_tier1_per_trade_pct=0.05)
    equity = WC
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


def test_day_trail_arm_floor_trail_math_10k():
    arm = Decimal("191.08")
    initial = Decimal("143.31")
    trail = Decimal("0.30")
    quiet = advance_trail(None, "2026-11-03", Decimal("50"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert quiet["armed"] is False
    armed = advance_trail(quiet, "2026-11-03", arm, arm=arm, initial_floor=initial, trail_pct=trail)
    assert armed["armed"] is True
    assert Decimal(armed["floor"]) == initial  # max(protect, arm*0.7=133.76) → protect
    peaked = advance_trail(armed, "2026-11-03", Decimal("600"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert Decimal(peaked["floor"]) == Decimal("420.00")  # 600 * 0.7
    closed = advance_trail(peaked, "2026-11-03", Decimal("420"), arm=arm, initial_floor=initial, trail_pct=trail)
    assert closed["closed"] is True
    assert closed["reason"] == "DAY_TRAIL"


def test_day_trail_persists_across_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    # Freeze FX so arm/protect match WC math.
    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
        trading_config_path=str(TOML_10K),
    )
    moment = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    first = observe_day_floor(settings, Decimal("600"), moment, True, equity=WC)
    assert first["armed"] is True
    assert Decimal(first["floor"]) == Decimal("420.00")
    again = observe_day_floor(settings, Decimal("500"), moment, True, equity=WC)
    assert again["closed"] is False
    tripped = observe_day_floor(settings, Decimal("420"), moment, True, equity=WC)
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


def test_trading_rules_endpoint_shape(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
        trading_config_path=str(TOML_10K),
        api_admin_key="",
    )
    app = create_app(settings, allow_open_api=True)
    client = TestClient(app)
    response = client.get("/api/v1/trading-rules")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["read_only"] is True
    assert body["active"]["ils_per_usd"] == "3.14"
    assert "account_sizing" in body["active"]["sections"]
    assert "day_brakes" in body["active"]["sections"]
    assert len(body["active"]["sections"]["tiers"]) == 3
