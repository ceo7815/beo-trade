"""Options 2 is the 26.10 book (b7f62cf) scaled to $40,000, and it does not move the main desk."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.settings import Settings, get_settings, load_trading_config, set_settings
from app.main import create_app
from app.ops.safety import policy_errors, paper_errors
from app.options.day_floor import marketable_sell_limit
from app.options2.desk import PAPER_HOST, prepare_process
from app.options2.guard import order_refusal, trading_allowed
from app.recommendations.decision import direction_matches
from app.schemas.domain import OptionRight
from app.trades.exclusion import SEEDS, runtime_seeds

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "app" / "config" / "trading_v2610_40k.toml"
SCALE = Decimal("0.40")
# b7f62cf day-floor ladder on the $100,000 book. Options 2 multiplies both numbers by 0.40.
SOURCE_LADDER = (
    (4000, 2000),
    (7000, 3000),
    (8000, 4000),
    (9000, 5000),
    (10000, 6000),
    (11000, 7000),
    (12000, 8000),
    (13000, 9000),
    (14000, 10000),
    (15000, 11000),
)
SOURCE_KILL = Decimal("3500")

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


def _options2(**overrides) -> Settings:
    values = dict(
        app_env="local",
        database_url="sqlite:///:memory:",
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
        paper_only=True,
        alpaca_base_url=PAPER_HOST,
        desk_id="options2",
        options2_profile="v2610_40k",
        options2_enabled=False,
        api_admin_key=ADMIN,
        api_read_key=READ,
    )
    values.update(overrides)
    return Settings(**values)


def test_profile_matches_b7f62cf_with_documented_dollar_scale():
    cfg = load_trading_config(PROFILE)
    assert policy_errors(cfg) == []
    assert cfg.capital_profile == "v2610_40k"
    assert cfg.options2_source_commit == "b7f62cf"
    assert Decimal(str(cfg.options2_scale)) == SCALE
    assert cfg.options2_capital_usd == 40000
    assert Decimal(str(cfg.daily_kill_loss_usd)) == SOURCE_KILL * SCALE
    assert cfg.day_floor_steps == tuple((int(arm * SCALE), int(lock * SCALE)) for arm, lock in SOURCE_LADDER)
    assert cfg.day_floor_arm == 1600
    assert cfg.day_floor_lock == 800
    # Percent rules are the source commit, not the later 20K book.
    assert cfg.risk_per_trade_pct == 0.02
    assert cfg.normal_position_capital_pct == 0.07
    assert cfg.hard_position_capital_pct == 0.10
    assert cfg.max_total_open_exposure_pct == 0.35
    assert cfg.max_aggregate_planned_risk_pct == 0.15
    assert cfg.max_sector_exposure_pct == 0.10
    assert cfg.min_option_price == 0.30
    assert cfg.max_option_price == 20.0
    assert cfg.min_delta == 0.35
    assert cfg.max_delta == 0.75
    assert cfg.target_delta == 0.40
    assert cfg.preferred_dte == 2
    assert cfg.min_expiration_days == 0
    assert cfg.max_expiration_days == 21
    assert cfg.initial_stop_decline_pct == 0.40
    assert cfg.trail_activate_r == 0.75
    assert cfg.trailing_pct == 0.15
    assert cfg.invalidation_underlying_percent == 0.01
    assert cfg.entry_buffer_percent == 0.04
    assert cfg.entry_cutoff_minutes == 80
    assert cfg.opening_entry_cap == 2
    assert cfg.opening_call_block_until_minutes == 0
    assert cfg.call_extended_at_high_enabled is False
    assert cfg.entry_pace_enabled is True
    assert cfg.entry_pace_max_entries == 2
    assert cfg.entry_pace_window_minutes == 5
    assert cfg.entry_pace_opposite_block_minutes == 30
    assert cfg.exit_reprice_seconds == 15
    assert cfg.stop_exit_reprice_seconds == 15
    assert cfg.marketable_stop is False
    assert cfg.legacy_direction_v2610 is True
    assert cfg.apply_bug_lot_exclusions is False
    assert cfg.day_trail_enabled is False
    assert cfg.max_chains_per_scan == 12
    assert cfg.universe_max_symbols_per_scan == 40
    assert "VIX" in cfg.context_symbols
    assert cfg.allow_quant_entry is True


def test_main_book_is_unchanged_and_ignores_the_options2_profile_id():
    main = load_trading_config()
    assert policy_errors(main) == []
    assert main.capital_profile == "20k_ils"
    assert main.opening_call_block_until_minutes == 600
    assert main.call_extended_at_high_enabled is True
    assert main.min_option_price == 0.50
    assert main.max_option_price == 8.0
    assert main.marketable_stop is True
    assert main.legacy_direction_v2610 is False
    assert main.apply_bug_lot_exclusions is True
    assert main.daily_kill_loss_usd == 3500
    ignored = Settings(
        app_env="local",
        trading_mode="PAPER",
        paper_only=True,
        options2_profile="v2610_40k",
        options2_enabled=True,
    )
    assert ignored.desk_id == ""
    assert ignored.trading().capital_profile == "20k_ils"
    assert policy_errors(load_trading_config(ROOT / "app" / "config" / "trading_100k.toml")) == []
    assert policy_errors(load_trading_config(ROOT / "app" / "config" / "trading_10k.toml")) == []


def test_trading_config_path_stays_blocked_and_only_the_allowlisted_profile_loads():
    blocked = Settings(app_env="local", trading_mode="PAPER", paper_only=True, trading_config_path="/tmp/other.toml")
    assert "TRADING_CONFIG_PATH" in paper_errors(blocked)
    wrong = _options2(options2_profile="other")
    assert "OPTIONS2_PROFILE" in paper_errors(wrong)
    good = _options2()
    assert paper_errors(good) == []
    assert policy_errors(good.trading()) == []


def test_prepare_leaves_the_main_keys_alone(monkeypatch):
    monkeypatch.delenv("DESK_ID", raising=False)
    monkeypatch.setenv("ALPACA_API_KEY", "main-key")
    monkeypatch.setenv("OPTIONS2_ALPACA_API_KEY", "other-key")
    prepare_process()
    assert os.environ["ALPACA_API_KEY"] == "main-key"


def test_prepare_swaps_options2_credentials_and_refuses_a_live_host(monkeypatch):
    monkeypatch.setenv("DESK_ID", "options2")
    monkeypatch.setenv("ALPACA_API_KEY", "main-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "main-secret")
    monkeypatch.setenv("OPTIONS2_ALPACA_API_KEY", "o2-key")
    monkeypatch.setenv("OPTIONS2_ALPACA_API_SECRET", "o2-secret")
    monkeypatch.setenv("OPTIONS2_ALPACA_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/main.db")
    monkeypatch.setenv("TRADING_CONFIG_PATH", "/tmp/evil.toml")
    monkeypatch.setenv("OPTIONS2_HOST_REFUSED", "")
    monkeypatch.setenv("OPTIONS2_CONFIG_PATH_BLOCKED", "")
    prepare_process()
    assert os.environ["ALPACA_API_KEY"] == "o2-key"
    assert os.environ["ALPACA_API_SECRET"] == "o2-secret"
    assert os.environ["ALPACA_BASE_URL"] == PAPER_HOST
    assert os.environ["DATABASE_URL"] == ""
    assert os.environ["TRADING_CONFIG_PATH"] == ""
    assert os.environ["OPTIONS2_HOST_REFUSED"] == "1"
    assert os.environ["OPTIONS2_CONFIG_PATH_BLOCKED"] == "1"
    assert "main-key" not in os.environ["ALPACA_API_KEY"]


def test_paper_guard_refuses_until_the_paper_account_matches(monkeypatch):
    monkeypatch.delenv("OPTIONS2_HOST_REFUSED", raising=False)
    disabled = _options2(options2_enabled=False, alpaca_api_key="k", alpaca_api_secret="s", options2_account_last4="32PA")
    assert trading_allowed(disabled) == (False, "OPTIONS2_DISABLED")
    assert order_refusal(disabled, {"account_number": "PA32PA"}) == "OPTIONS2_DISABLED"
    missing = _options2(options2_enabled=True, alpaca_api_key="k", alpaca_api_secret="s", options2_account_last4="")
    assert order_refusal(missing, {"account_number": "PA32PA"}) == "OPTIONS2_NOT_CONFIGURED"
    monkeypatch.setenv("OPTIONS2_HOST_REFUSED", "1")
    refused = _options2(options2_enabled=True, alpaca_api_key="k", alpaca_api_secret="s", options2_account_last4="32PA")
    assert order_refusal(refused, {"account_number": "PA32PA"}) == "OPTIONS2_NOT_PAPER"
    monkeypatch.delenv("OPTIONS2_HOST_REFUSED", raising=False)
    mismatch = _options2(options2_enabled=True, alpaca_api_key="k", alpaca_api_secret="s", options2_account_last4="32PA")
    assert order_refusal(mismatch, {"account_number": "PA3N1EPU6"}) == "OPTIONS2_ACCOUNT_MISMATCH"
    match = order_refusal(mismatch, {"account_number": "PA32PA"})
    assert match is None
    assert order_refusal(Settings(app_env="local", trading_mode="PAPER", paper_only=True), {"account_number": "X"}) is None


def _underlying(change: str):
    from app.schemas.domain import UnderlyingSnapshot

    now = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    return UnderlyingSnapshot(
        symbol="QQQ",
        price=Decimal("100"),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=2_000_000,
        relative_volume=Decimal("2"),
        change_percent=Decimal(change),
        change_dollars=Decimal(change),
        prior_close=Decimal("100"),
        observed_at=now,
        session_ok=True,
    )


def _contract(right: OptionRight):
    from app.schemas.domain import OptionSnapshot

    return OptionSnapshot(
        underlying="QQQ",
        option_symbol="QQQ261029C00100000" if right is OptionRight.CALL else "QQQ261029P00100000",
        right=right,
        strike=Decimal("100"),
        expiration=date(2026, 10, 29),
        bid=Decimal("1.00"),
        ask=Decimal("1.05"),
        last=Decimal("1.02"),
        volume=100,
        open_interest=200,
        observed_at=datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc),
        delta=Decimal("0.40") if right is OptionRight.CALL else Decimal("-0.40"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.04"),
        implied_volatility=Decimal("0.50"),
    )


def test_reversal_matches_b7f62cf_only_on_the_options2_profile():
    down = _underlying("-1.2")
    flat = _underlying("0")
    call = _contract(OptionRight.CALL)
    put = _contract(OptionRight.PUT)
    reversal = {"REVERSAL"}
    legacy = _options2().trading()
    assert direction_matches(down, call, reversal, legacy) is True
    assert direction_matches(down, put, reversal, legacy) is True
    assert direction_matches(flat, call, set(), legacy) is True
    current = load_trading_config()
    assert direction_matches(down, call, reversal, current) is False
    assert direction_matches(down, put, reversal, current) is True
    assert direction_matches(flat, call, reversal, current) is False


def test_stop_sells_at_the_bid_on_this_profile_and_under_the_bid_on_main():
    bid = Decimal("3.55")
    assert marketable_sell_limit(bid, "STOP", marketable=False) == bid
    assert marketable_sell_limit(bid, "PROTECTED_STOP", marketable=False) == bid
    assert marketable_sell_limit(bid, "STOP", marketable=True) == Decimal("3.50")
    assert _options2().trading().marketable_stop is False
    assert load_trading_config().marketable_stop is True


def test_bug_lots_are_not_applied_on_options2():
    assert len(SEEDS) == 3
    assert runtime_seeds(_options2()) == []
    assert len(runtime_seeds(Settings(app_env="local", trading_mode="PAPER", paper_only=True))) == 3


def test_read_key_can_read_options2_status_trades_and_risk(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    settings = _options2(database_url="sqlite:///" + (tmp_path / "o2.db").as_posix())
    set_settings(None)
    previous = get_settings()
    set_settings(settings)
    try:
        with TestClient(create_app(settings)) as client:
            headers = {"X-Beo-Key": READ}
            assert client.get("/api/v1/system", headers=headers).status_code == 200
            body = client.get("/api/v1/system", headers=headers).json()
            assert body["desk"] == "options2"
            assert body["options2_block"] == "OPTIONS2_DISABLED"
            assert body["daily_kill_loss_usd"] == "1400.00"
            assert client.get("/api/v1/report/trades", headers=headers).status_code == 200
            assert client.get("/api/v1/risk", headers=headers).status_code == 200
            rules = client.get("/api/v1/trading-rules", headers=headers)
            assert rules.status_code == 200
            assert rules.json()["active"]["profile"] == "v2610_40k"
            assert client.get("/api/v1/decisions", headers=headers).status_code == 200
            positions = client.get("/api/v1/broker/alpaca/positions", headers=headers)
            assert positions.status_code not in {401, 403}
            assert client.post("/api/v1/control/trading", headers=headers, json={"halted": True}).status_code == 403
    finally:
        set_settings(previous)
