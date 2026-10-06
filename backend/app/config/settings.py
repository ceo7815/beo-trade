from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, fields
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TradingConfig:
    min_option_volume: int = 0
    min_open_interest: int = 0
    max_bid_ask_spread: float = 0.40
    min_underlying_volume: int = 1_000_000
    max_expiration_days: int = 21
    min_expiration_days: int = 0
    min_delta: float = 0.15
    max_delta: float = 0.75
    target_delta: float = 0.40
    min_gamma: float = 0.0005
    max_theta_threshold: float = 0.80
    min_data_freshness_seconds: int = 20
    max_buys_per_scan: int = 2
    scan_interval_seconds: int = 60
    monitor_interval_seconds: int = 15
    shortlist_size: int = 4
    min_relative_volume: float = 0.50
    relative_volume_priority: float = 1.8
    min_underlying_move_percent: float = 0.6
    min_option_price: float = 0.05
    max_option_price: float = 20.0
    entry_buffer_percent: float = 0.04
    holding_window_min_minutes: int = 15
    holding_window_max_minutes: int = 180
    max_risk_percent: float = 0.02
    max_capital_per_trade: float = 0
    contract_multiplier: int = 100
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    scenario_moves_percent: tuple[float, ...] = (
        0.5, 1.0, 2.0, 3.0, -0.5, -1.0, -2.0, 0.0,
    )
    min_confirming_sources: int = 2
    official_source_credibility: float = 0.9
    stop_loss_percent: float = 0.40
    initial_stop_decline_pct: float = 0.40
    protect_at_r: float = 1.0
    protective_stop_r: float = 0.25
    trail_activate_r: float = 1.5
    winner_run_r: float = 2.0
    invalidation_underlying_percent: float = 0.01
    iv_crush_percent: float = 0.20
    outcome_target_percent: float = 0.0
    min_reason_codes: int = 2
    slippage_percent: float = 0.01
    allow_buy_without_news: bool = True
    allow_quant_entry: bool = True
    paper_auto_exit: bool = True
    min_events: int = 1
    max_input_tokens: int = 6000
    max_output_tokens: int = 800
    news_max_age_seconds: int = 21600
    gap_percent: float = 0.004
    target_pct: float = 0.30
    trailing_enabled: bool = True
    trailing_pct: float = 0.15
    exit_before_expiration_minutes: int = 20
    session_exit_minutes: int = 15
    holding_minutes_0dte: int = 90
    holding_minutes_1dte: int = 180
    max_contracts: int = 100
    max_open_positions: int = 10
    max_daily_loss_dollars: float = 0
    paper_policy_name: str = "AGGRESSIVE"
    risk_policy_version: str = "aggressive-1"
    exit_policy_version: str = "aggressive-exit-1"
    risk_per_trade_pct: float = 0.02
    normal_position_capital_pct: float = 0.07
    hard_position_capital_pct: float = 0.10
    max_aggregate_planned_risk_pct: float = 0.15
    daily_warning_pct: float = 0.025
    daily_entry_stop_pct: float = 0.04
    daily_hard_stop_pct: float = 0.05
    max_capital_per_trade_pct: float = 0.07
    max_total_open_exposure_pct: float = 0.35
    max_concurrent_positions: int = 10
    max_positions_per_underlying: int = 1
    max_daily_loss_pct: float = 0.04
    max_sector_exposure_pct: float = 0.10
    ai_max_calls_per_scan: int = 8
    ai_decision_ttl_seconds: int = 900
    kill_switch: bool = False
    recommendation_max_age_seconds: int = 180
    revalidation_max_price_drift: float = 0.03
    liquidity_exit_spread: float = 0.25
    scan_strike_range: int = 15
    min_eod_days: int = 5
    universe_max_symbols_per_scan: int = 40
    universe_refresh_seconds: int = 21600
    universe_min_price: float = 5
    universe_exclusions: tuple[str, ...] = ()
    core_symbols: tuple[str, ...] = ()
    research_probe_symbol: str = "NVDA"
    context_symbols: tuple[str, ...] = ("SPY", "QQQ", "IWM", "VIX")
    fred_series: tuple[str, ...] = ("FEDFUNDS", "DGS2", "DGS10", "T10Y2Y")
    sec_fact_tags: tuple[str, ...] = ("Revenues", "NetIncomeLoss", "EarningsPerShareDiluted")


@dataclass(frozen=True)
class SessionCalendar:
    timezone: str
    open_time: time
    close_time: time
    pre_market_minutes: int
    open_drive_minutes: int
    pre_close_minutes: int
    post_market_minutes: int
    daily_report_offset_minutes: int
    daily_report_window_minutes: int
    holidays: frozenset[date]
    early_closes: dict[date, time]


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def load_trading_config(path: Path | None = None) -> TradingConfig:
    file_path = path or (CONFIG_DIR / "trading.toml")
    raw = tomllib.loads(file_path.read_text(encoding="utf-8"))
    known = {item.name for item in fields(TradingConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"Unknown trading config keys: {', '.join(unknown)}")
    if "scenario_moves_percent" in raw:
        raw["scenario_moves_percent"] = tuple(raw["scenario_moves_percent"])
    for key in ("universe_exclusions", "core_symbols", "context_symbols", "fred_series"):
        if key in raw and isinstance(raw[key], list):
            raw[key] = tuple(str(item).upper() for item in raw[key])
    if "sec_fact_tags" in raw and isinstance(raw["sec_fact_tags"], list):
        raw["sec_fact_tags"] = tuple(str(item) for item in raw["sec_fact_tags"])
    return TradingConfig(**raw)


def load_calendar(path: Path | None = None) -> SessionCalendar:
    file_path = path or (CONFIG_DIR / "market_calendar.json")
    raw = json.loads(file_path.read_text(encoding="utf-8"))
    early = {date.fromisoformat(day): _parse_hhmm(clock) for day, clock in raw["early_closes"].items()}
    holidays = frozenset(date.fromisoformat(day) for day in raw["holidays"])
    return SessionCalendar(
        timezone=raw["timezone"],
        open_time=_parse_hhmm(raw["open"]),
        close_time=_parse_hhmm(raw["close"]),
        pre_market_minutes=int(raw["pre_market_minutes"]),
        open_drive_minutes=int(raw["open_drive_minutes"]),
        pre_close_minutes=int(raw["pre_close_minutes"]),
        post_market_minutes=int(raw["post_market_minutes"]),
        daily_report_offset_minutes=int(raw["daily_report_offset_minutes"]),
        daily_report_window_minutes=int(raw["daily_report_window_minutes"]),
        holidays=holidays,
        early_closes=early,
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "Beo-Trade"
    database_url: str = ""
    auto_create_tables: bool = True
    redis_url: str = ""
    auth_required: bool = False
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    market_data_provider: str = "unconfigured"
    options_data_provider: str = "unconfigured"
    news_provider: str = "unconfigured"
    openai_api_key: str = ""
    openai_model: str = "gpt-6.1-sol"
    trading_mode: str = "PAPER"
    paper_only: bool = True
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = ""
    thetadata_api_key: str = ""
    thetadata_base_url: str = ""
    theta_terminal_host: str = ""
    theta_terminal_port: int = 25503
    benzinga_api_key: str = ""
    fred_api_key: str = ""
    sec_user_agent: str = "Beo-Trade research platform (Beo Systems) research@beosystems.com"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_price_input_per_million: Decimal = Decimal("2")
    openai_price_cached_input_per_million: Decimal = Decimal("0.10")
    openai_price_output_per_million: Decimal = Decimal("10")
    ai_soft_budget: Decimal = Decimal("150")
    ai_warning_budget: Decimal = Decimal("150")
    ai_critical_budget: Decimal = Decimal("180")
    ai_hard_limit: Decimal = Decimal("200")
    max_ai_cost_per_day: Decimal = Decimal("20")
    paper_account_balance: Decimal = Decimal("1000")
    display_timezone: str = "Asia/Dubai"
    exchange_timezone: str = "America/New_York"
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"
    dev_user_id: str = "00000000-0000-0000-0000-000000000001"
    trading_config_path: str = ""
    calendar_path: str = ""

    @model_validator(mode="after")
    def production_rules(self) -> "Settings":
        if self.trading_mode.upper() != "PAPER":
            raise ValueError("TRADING_MODE must be PAPER in this version")
        if not self.paper_only:
            raise ValueError("PAPER_ONLY must be true")
        paper_host = "https://paper-api.alpaca.markets"
        base = self.alpaca_base_url.strip().rstrip("/")
        if base and base != paper_host:
            raise ValueError("ALPACA_BASE_URL must be the Alpaca paper host")
        if not self.thetadata_base_url.strip() and self.theta_terminal_host.strip():
            self.thetadata_base_url = f"http://{self.theta_terminal_host.strip()}:{self.theta_terminal_port}"
        if self.app_env == "production":
            if not self.auth_required:
                raise ValueError("AUTH_REQUIRED must be true in production")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("production DATABASE_URL must be PostgreSQL")
            if self.auto_create_tables:
                raise ValueError("AUTO_CREATE_TABLES must be false in production")
            if not self.supabase_jwt_secret:
                raise ValueError("SUPABASE_JWT_SECRET is required in production")
        return self

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        data_dir = Path(__file__).resolve().parents[2] / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return "sqlite:///" + (data_dir / "beo_trade.db").as_posix()

    def trading(self) -> TradingConfig:
        path = Path(self.trading_config_path) if self.trading_config_path else None
        return load_trading_config(path)

    def calendar(self) -> SessionCalendar:
        path = Path(self.calendar_path) if self.calendar_path else None
        return load_calendar(path)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
