from __future__ import annotations

from app.config.settings import Settings, TradingConfig

PAPER_HOST = "https://paper-api.alpaca.markets"

# Shared exit / floor / pacing fields that both capital profiles must keep.
_SHARED_POLICY = {
    "paper_policy_name": "AGGRESSIVE",
    "risk_policy_version": "aggressive-1",
    "exit_policy_version": "aggressive-exit-3",
    "max_open_positions": 10,
    "max_concurrent_positions": 10,
    "max_positions_per_underlying": 1,
    "initial_stop_decline_pct": 0.40,
    "protect_at_r": 1.0,
    "protective_stop_r": 0.25,
    "trail_activate_r": 0.75,
    "winner_run_r": 2.0,
    "trailing_enabled": True,
    "trailing_pct": 0.15,
    "holding_minutes_0dte": 90,
    "holding_minutes_1dte": 180,
    "session_exit_minutes": 15,
    "exit_before_expiration_minutes": 20,
    "outcome_target_percent": 0.0,
    "stop_loss_percent": 0.40,
    "opening_entry_cap": 2,
    "opening_entry_until_minutes": 600,
    "entry_rules_version": "v10.29-pacing",
    # The main books keep the post-26.10 behavior. Options 2 overrides these in its own policy.
    "legacy_direction_v2610": False,
    "marketable_stop": True,
    "apply_bug_lot_exclusions": True,
    "day_floor_arm": 4000,
    "day_floor_lock": 2000,
    "day_floor_steps": (
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
    ),
}

OFFICIAL_POLICY_100K = {
    **_SHARED_POLICY,
    "capital_profile": "100k",
    "risk_per_trade_pct": 0.02,
    "normal_position_capital_pct": 0.07,
    "hard_position_capital_pct": 0.10,
    "max_total_open_exposure_pct": 0.35,
    "max_aggregate_planned_risk_pct": 0.15,
    "max_sector_exposure_pct": 0.10,
    "working_capital_ils": 0,
    "day_trail_enabled": False,
    "daily_kill_loss_usd": 3500,
    "daily_warning_pct": 0.025,
    "daily_entry_stop_pct": 0.04,
    "daily_hard_stop_pct": 0.05,
    "max_daily_loss_pct": 0.04,
}

OFFICIAL_POLICY_10K = {
    **_SHARED_POLICY,
    "capital_profile": "10k_ils",
    "risk_per_trade_pct": 0.06,
    "normal_position_capital_pct": 0.15,
    "hard_position_capital_pct": 0.20,
    "max_total_open_exposure_pct": 1.0,
    "max_aggregate_planned_risk_pct": 1.0,
    "max_sector_exposure_pct": 0.40,
    "working_capital_ils": 10000,
    "ils_per_usd": 3.14,
    "daily_kill_ils": 800,
    "daily_kill_pct": 0.08,
    "day_trail_enabled": True,
    "day_trail_arm_pct": 0.06,
    "day_trail_protect_pct": 0.045,
    "day_trail_initial_floor_frac": 0.75,
    "day_trail_pct": 0.30,
    "capital_tier1_max_ils": 25000,
    "capital_tier2_max_ils": 100000,
    "capital_tier1_per_trade_pct": 0.15,
    "capital_tier1_kill_pct": 0.08,
    "capital_tier1_arm_pct": 0.06,
    "capital_tier1_protect_pct": 0.045,
    "capital_tier1_exposure_pct": 1.0,
    "capital_tier2_per_trade_pct": 0.10,
    "capital_tier2_kill_pct": 0.06,
    "capital_tier2_arm_pct": 0.045,
    "capital_tier2_protect_pct": 0.03375,
    "capital_tier2_exposure_pct": 1.0,
    "max_capital_per_trade_pct": 0.15,
    "daily_kill_loss_usd": 3500,
    "daily_warning_pct": 0.10,
    "daily_entry_stop_pct": 0.12,
    "daily_hard_stop_pct": 0.15,
    "max_daily_loss_pct": 0.12,
}

OFFICIAL_POLICY_20K = {
    **_SHARED_POLICY,
    "capital_profile": "20k_ils",
    "risk_per_trade_pct": 0.06,
    "normal_position_capital_pct": 0.15,
    "hard_position_capital_pct": 0.15,
    "max_total_open_exposure_pct": 1.0,
    "max_aggregate_planned_risk_pct": 1.0,
    "max_sector_exposure_pct": 0.40,
    "working_capital_ils": 20000,
    "ils_per_usd": 3.14,
    "daily_kill_ils": 2333,
    "daily_kill_pct": 0.11666666666666667,
    "daily_kill_ratio_loss_usd": 3500,
    "daily_kill_ratio_deploy_usd": 30000,
    "day_floor_deploy_reference_usd": 30000,
    "day_trail_enabled": True,
    "day_trail_arm_pct": 0,
    "day_trail_protect_pct": 0,
    "day_trail_initial_floor_frac": 0.75,
    "day_trail_pct": 0.40,
    "day_trail_after_first_step": True,
    "allow_one_contract_over_cap": False,
    "max_capital_per_trade_pct": 0.15,
    "daily_kill_loss_usd": 3500,
    "daily_warning_pct": 0.10,
    "daily_entry_stop_pct": 0.12,
    "daily_hard_stop_pct": 0.15,
    "max_daily_loss_pct": 0.12,
}

# Active desk after this release (trading.toml). 10k_ils stays in trading_10k.toml. 100k recovers the old desk.
OFFICIAL_POLICY = OFFICIAL_POLICY_20K

# Options 2 only. Dollar brakes are the b7f62cf 100K ladder × 0.40 ($40,000 / $100,000).
# Percent rules match that commit. Later entry filters stay off.
_V2610_LADDER = (
    (1600, 800),
    (2800, 1200),
    (3200, 1600),
    (3600, 2000),
    (4000, 2400),
    (4400, 2800),
    (4800, 3200),
    (5200, 3600),
    (5600, 4000),
    (6000, 4400),
)

OFFICIAL_POLICY_V2610_40K = {
    "capital_profile": "v2610_40k",
    "options2_scale": 0.40,
    "options2_capital_usd": 40000,
    "options2_source_commit": "b7f62cf",
    "paper_policy_name": "AGGRESSIVE",
    "risk_policy_version": "aggressive-1",
    "exit_policy_version": "aggressive-exit-3",
    "risk_per_trade_pct": 0.02,
    "normal_position_capital_pct": 0.07,
    "hard_position_capital_pct": 0.10,
    "max_capital_per_trade_pct": 0.07,
    "max_total_open_exposure_pct": 0.35,
    "max_aggregate_planned_risk_pct": 0.15,
    "max_sector_exposure_pct": 0.10,
    "max_open_positions": 10,
    "max_concurrent_positions": 10,
    "max_positions_per_underlying": 1,
    "max_contracts": 100,
    "min_option_price": 0.30,
    "max_option_price": 20.0,
    "min_delta": 0.35,
    "max_delta": 0.75,
    "target_delta": 0.40,
    "preferred_dte": 2,
    "preferred_max_dte": 3,
    "min_expiration_days": 0,
    "max_expiration_days": 21,
    "initial_stop_decline_pct": 0.40,
    "stop_loss_percent": 0.40,
    "protect_at_r": 1.0,
    "protective_stop_r": 0.25,
    "trail_activate_r": 0.75,
    "winner_run_r": 2.0,
    "trailing_enabled": True,
    "trailing_pct": 0.15,
    "invalidation_underlying_percent": 0.01,
    "holding_minutes_0dte": 90,
    "holding_minutes_1dte": 180,
    "session_exit_minutes": 15,
    "exit_before_expiration_minutes": 20,
    "entry_cutoff_minutes": 80,
    "entry_buffer_percent": 0.04,
    "exit_reprice_seconds": 15,
    "stop_exit_reprice_seconds": 15,
    "opening_entry_cap": 2,
    "opening_entry_until_minutes": 600,
    "opening_call_block_until_minutes": 0,
    "call_extended_at_high_enabled": False,
    "entry_rules_version": "v10.29-pacing",
    "entry_pace_enabled": True,
    "entry_pace_window_minutes": 5,
    "entry_pace_max_entries": 2,
    "entry_pace_opposite_block_minutes": 30,
    "legacy_direction_v2610": True,
    "marketable_stop": False,
    "apply_bug_lot_exclusions": False,
    "day_trail_enabled": False,
    "day_trail_after_first_step": False,
    "daily_kill_loss_usd": 1400,
    "daily_warning_pct": 0.025,
    "daily_entry_stop_pct": 0.04,
    "daily_hard_stop_pct": 0.05,
    "max_daily_loss_pct": 0.04,
    "day_floor_arm": 1600,
    "day_floor_lock": 800,
    "day_floor_steps": _V2610_LADDER,
    "working_capital_ils": 0,
    "daily_kill_ratio_loss_usd": 0,
    "daily_kill_ratio_deploy_usd": 0,
}


def _policy_for(trading: TradingConfig) -> dict:
    profile = str(getattr(trading, "capital_profile", "100k") or "100k")
    if profile == "v2610_40k":
        return OFFICIAL_POLICY_V2610_40K
    if profile == "20k_ils":
        return OFFICIAL_POLICY_20K
    if profile == "10k_ils":
        return OFFICIAL_POLICY_10K
    return OFFICIAL_POLICY_100K


def policy_errors(trading: TradingConfig) -> list[str]:
    found: list[str] = []
    for name, expected in _policy_for(trading).items():
        actual = getattr(trading, name, None)
        if isinstance(expected, float):
            if actual is None or abs(float(actual) - expected) > 1e-9:
                found.append(name)
        elif actual != expected:
            found.append(name)
    return found


def paper_errors(settings: Settings) -> list[str]:
    found: list[str] = []
    if settings.trading_mode.upper() != "PAPER":
        found.append("TRADING_MODE")
    if not settings.paper_only:
        found.append("PAPER_ONLY")
    base = settings.alpaca_base_url.strip().rstrip("/")
    if base and base != PAPER_HOST:
        found.append("ALPACA_BASE_URL")
    if settings.trading_config_path.strip():
        found.append("TRADING_CONFIG_PATH")
    if settings.desk_id.strip() == "options2":
        from app.options2.desk import PROFILE_ID, config_path_blocked

        if config_path_blocked():
            found.append("TRADING_CONFIG_PATH")
        if settings.options2_profile.strip() != PROFILE_ID:
            found.append("OPTIONS2_PROFILE")
    return found


def assert_boot_safe(settings: Settings) -> None:
    """Refuse to start when paper mode or the official risk/exit policy is not intact."""
    found = paper_errors(settings)
    # A wrong Options 2 profile id must fail before the loader opens any other file.
    if "OPTIONS2_PROFILE" not in found:
        found.extend(policy_errors(settings.trading()))
    if found:
        raise SystemExit("PAPER SAFETY FAILED: " + ", ".join(found))
