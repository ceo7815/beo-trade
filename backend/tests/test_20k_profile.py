"""20K ₪ profile: locked kill, scaled ladder, day trail, hard per-trade cap."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.capital import (
    configured_kill_pct,
    day_giveback_enabled,
    effective_kill_usd,
    profile_is_20k,
    scaled_day_floor_steps,
    working_capital_usd,
)
from app.capital.session_lock import locked_open_equity
from app.config.settings import Settings, load_trading_config
from app.options.day_floor import observe_day_floor
from app.options.risk_limits import day_flatten_reason
from app.positions.admission import admit_buy
from app.positions.entry_pace import call_block_clocks
from app.recommendations.decision import direction_matches
from app.schemas.domain import OptionRight, OptionSnapshot, UnderlyingSnapshot

import pytest

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config"
TOML_10K = CONFIG / "trading_10k.toml"
RATIO = Decimal(3500) / Decimal(30000)
WC20 = (Decimal("20000") / Decimal("3.14")).quantize(Decimal("0.01"))


@pytest.fixture(autouse=True)
def _freeze_fx(monkeypatch):
    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)


def _cfg20():
    cfg = load_trading_config()
    assert profile_is_20k(cfg)
    return cfg


def test_20k_kill_fraction_and_working_capital():
    cfg = _cfg20()
    assert configured_kill_pct(cfg) == RATIO
    assert working_capital_usd(cfg) == WC20
    assert day_giveback_enabled(cfg) is True
    assert cfg.day_trail_pct == 0.40
    assert cfg.allow_one_contract_over_cap is False
    assert cfg.normal_position_capital_pct == cfg.hard_position_capital_pct == 0.15
    assert cfg.max_total_open_exposure_pct == 1.0
    assert effective_kill_usd(cfg, WC20) == (WC20 * RATIO).quantize(Decimal("0.01"))
    assert effective_kill_usd(load_trading_config(CONFIG / "trading_100k.toml")) == Decimal("3500")


def test_kill_is_locked_to_session_open_equity_for_20k_and_10k():
    cfg = _cfg20()
    opened = WC20
    shrunk = Decimal("5600.00")
    locked = effective_kill_usd(cfg, shrunk, open_equity=opened)
    assert locked == (opened * RATIO).quantize(Decimal("0.01"))
    assert locked == Decimal("743.10")
    shrunk_line = (shrunk * RATIO).quantize(Decimal("0.01"))
    assert shrunk_line < locked
    assert day_flatten_reason(shrunk, -locked, cfg, open_equity=opened) == "DAILY_HARD_STOP"
    assert day_flatten_reason(shrunk, -shrunk_line, cfg, open_equity=opened) is None

    cfg10 = load_trading_config(TOML_10K)
    open10 = Decimal("3184.71")
    # 8% of a smaller live equity was the bug ($234.99). The open stays $254.78.
    assert effective_kill_usd(cfg10, Decimal("2937.37"), open_equity=open10) == Decimal("254.78")
    assert day_flatten_reason(Decimal("2937.37"), Decimal("-254.78"), cfg10, open_equity=open10) == "DAILY_HARD_STOP"
    assert day_flatten_reason(Decimal("2937.37"), Decimal("-241.00"), cfg10, open_equity=open10) is None


def test_ladder_scales_the_100k_steps_by_open_equity_over_30000():
    steps = scaled_day_floor_steps(_cfg20(), WC20)
    assert steps[0] == (Decimal("849.26"), Decimal("424.63"))
    assert steps[-1] == (
        (Decimal(15000) * WC20 / Decimal(30000)).quantize(Decimal("0.01")),
        (Decimal(11000) * WC20 / Decimal(30000)).quantize(Decimal("0.01")),
    )
    assert abs(steps[0][0] - Decimal("850")) < 1
    assert abs(steps[0][1] - Decimal("425")) < 1
    assert abs(steps[-1][0] - Decimal("3190")) < 10
    assert abs(steps[-1][1] - Decimal("2340")) < 10
    # 100K steps are not scaled.
    classic = scaled_day_floor_steps(load_trading_config(CONFIG / "trading_100k.toml"), WC20)
    assert classic[0] == (Decimal("4000"), Decimal("2000"))
    assert classic[-1] == (Decimal("15000"), Decimal("11000"))


def test_day_trail_arms_only_after_the_first_ladder_step(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.capital.session_lock.store_path", lambda: tmp_path / "session_open.json")
    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)
    locked_open_equity("2026-11-04", last_equity=WC20, persist=True)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 11, 4, 15, 30, tzinfo=timezone.utc)
    quiet = observe_day_floor(settings, Decimal("800"), moment, True, open_equity=WC20)
    assert quiet["armed"] is False
    assert quiet["trail_armed"] is False
    assert quiet["mode"] == "ladder_trail"

    first_arm, first_floor = scaled_day_floor_steps(settings.trading(), WC20)[0]
    armed = observe_day_floor(settings, first_arm, moment, True, open_equity=WC20)
    trail_floor = (first_arm * Decimal("0.60")).quantize(Decimal("0.01"))
    assert armed["armed"] is True
    assert armed["trail_armed"] is True
    assert Decimal(armed["trail_floor"]) == trail_floor
    assert Decimal(armed["ladder_floor"]) == first_floor
    assert trail_floor > first_floor
    assert Decimal(armed["floor"]) == trail_floor

    held = observe_day_floor(settings, trail_floor + Decimal("1"), moment, True, open_equity=WC20)
    assert held["closed"] is False
    tripped = observe_day_floor(settings, trail_floor, moment, True, open_equity=WC20)
    assert tripped["closed"] is True
    assert tripped["reason"] == "DAY_TRAIL"


def test_ladder_floor_wins_when_it_is_higher_than_the_trail(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.capital.session_lock.store_path", lambda: tmp_path / "session_open.json")
    locked_open_equity("2026-11-04", last_equity=WC20, persist=True)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "t.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)
    last_arm, last_floor = scaled_day_floor_steps(settings.trading(), WC20)[-1]
    armed = observe_day_floor(settings, last_arm, moment, True, open_equity=WC20)
    assert Decimal(armed["ladder_floor"]) == last_floor
    assert Decimal(armed["ladder_floor"]) > Decimal(armed["trail_floor"])
    assert Decimal(armed["floor"]) == last_floor
    closed = observe_day_floor(settings, last_floor, moment, True, open_equity=WC20)
    assert closed["closed"] is True
    assert closed["reason"] == "DAY_FLOOR"
    # The floor does not fall if the next mark is still above it but under the peak.
    still = observe_day_floor(settings, last_floor + Decimal("10"), moment, True, open_equity=WC20)
    assert still["closed"] is True or Decimal(still.get("floor") or still["floor"]) >= last_floor


def test_one_contract_over_the_hard_cap_is_skipped():
    cfg = _cfg20()
    equity = WC20
    cap = (equity * Decimal("0.15")).quantize(Decimal("0.01"))

    def option(ask: str) -> OptionSnapshot:
        return OptionSnapshot(
            underlying="NVDA",
            option_symbol="NVDA261104C00100000",
            right=OptionRight.CALL,
            strike=Decimal("100"),
            expiration=date(2026, 11, 6),
            bid=Decimal(ask) - Decimal("0.05"),
            ask=Decimal(ask),
            last=Decimal(ask),
            volume=500,
            open_interest=1000,
            observed_at=datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc),
            delta=Decimal("0.45"),
            gamma=Decimal("0.01"),
            theta=Decimal("-0.10"),
            vega=Decimal("0.05"),
            implied_volatility=Decimal("0.40"),
        )

    over = admit_buy(
        option("10.00"),
        equity,
        3,
        cfg,
        positions=[],
        orders=[],
        state_risk={},
        open_premium=Decimal("0"),
        sector_premium=Decimal("0"),
        buying_power=equity,
        open_positions=0,
    )
    assert Decimal("1000") > cap
    assert over.quantity == 0
    assert over.reason == "SIZE_TOO_SMALL"

    under = admit_buy(
        option("9.00"),
        equity,
        3,
        cfg,
        positions=[],
        orders=[],
        state_risk={},
        open_premium=Decimal("0"),
        sector_premium=Decimal("0"),
        buying_power=equity,
        open_positions=0,
    )
    assert Decimal("900") <= cap
    assert under.quantity == 1


def test_call_block_dubai_clock_follows_new_york_dst():
    winter = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
    summer = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    assert call_block_clocks(600, winter) == ("10:00", "19:00")
    assert call_block_clocks(600, summer) == ("10:00", "18:00")


def _underlying(change: str) -> UnderlyingSnapshot:
    now = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
    return UnderlyingSnapshot(
        symbol="FIG",
        price=Decimal("22"),
        open=Decimal("22"),
        high=Decimal("23"),
        low=Decimal("21"),
        close=Decimal("22"),
        volume=2_000_000,
        relative_volume=Decimal("2"),
        change_percent=Decimal(change),
        change_dollars=Decimal(change) / Decimal("10"),
        prior_close=Decimal("22"),
        observed_at=now,
        session_ok=True,
    )


def _contract(right: OptionRight) -> OptionSnapshot:
    return OptionSnapshot(
        underlying="FIG",
        option_symbol="FIG261104C00022000" if right is OptionRight.CALL else "FIG261104P00022000",
        right=right,
        strike=Decimal("22"),
        expiration=date(2026, 11, 4),
        bid=Decimal("1.20"),
        ask=Decimal("1.30"),
        last=Decimal("1.25"),
        volume=400,
        open_interest=800,
        observed_at=datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc),
        delta=Decimal("0.40") if right is OptionRight.CALL else Decimal("-0.40"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.04"),
        implied_volatility=Decimal("0.50"),
    )


def test_reversal_does_not_buy_a_call_on_a_down_move():
    down = _underlying("-3.26")
    up = _underlying("3.26")
    call = _contract(OptionRight.CALL)
    put = _contract(OptionRight.PUT)
    reversal = {"REVERSAL", "MOMENTUM"}
    assert direction_matches(down, call, reversal) is False
    assert direction_matches(down, put, reversal) is True
    assert direction_matches(up, call, reversal) is True
    assert direction_matches(up, put, reversal) is False
    assert direction_matches(_underlying("0"), call, reversal) is False
    assert direction_matches(_underlying("0"), put, set()) is False
