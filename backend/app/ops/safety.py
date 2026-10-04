from __future__ import annotations

from app.config.settings import Settings, TradingConfig

PAPER_HOST = "https://paper-api.alpaca.markets"

OFFICIAL_POLICY = {
    "paper_policy_name": "AGGRESSIVE",
    "risk_policy_version": "aggressive-1",
    "exit_policy_version": "aggressive-exit-1",
    "risk_per_trade_pct": 0.02,
    "normal_position_capital_pct": 0.07,
    "hard_position_capital_pct": 0.10,
    "max_open_positions": 10,
    "max_concurrent_positions": 10,
    "max_total_open_exposure_pct": 0.35,
    "max_aggregate_planned_risk_pct": 0.15,
    "max_sector_exposure_pct": 0.10,
    "max_positions_per_underlying": 1,
    "daily_warning_pct": 0.025,
    "daily_entry_stop_pct": 0.04,
    "daily_hard_stop_pct": 0.05,
    "initial_stop_decline_pct": 0.40,
    "protect_at_r": 1.0,
    "protective_stop_r": 0.25,
    "trail_activate_r": 1.5,
    "winner_run_r": 2.0,
    "trailing_enabled": True,
    "trailing_pct": 0.15,
    "holding_minutes_0dte": 90,
    "holding_minutes_1dte": 180,
    "session_exit_minutes": 15,
    "exit_before_expiration_minutes": 20,
    "outcome_target_percent": 0.0,
    "stop_loss_percent": 0.40,
}


def policy_errors(trading: TradingConfig) -> list[str]:
    found: list[str] = []
    for name, expected in OFFICIAL_POLICY.items():
        actual = getattr(trading, name)
        if isinstance(expected, float):
            if abs(float(actual) - expected) > 1e-9:
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
    return found


def assert_boot_safe(settings: Settings) -> None:
    """Refuse to start when paper mode or the official risk/exit policy is not intact."""
    found = paper_errors(settings) + policy_errors(settings.trading())
    if found:
        raise SystemExit("PAPER SAFETY FAILED: " + ", ".join(found))
