from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config.settings import TradingConfig
from app.schemas.domain import OptionSnapshot


@dataclass(frozen=True)
class SizePlan:
    quantity: int
    max_entry: Decimal
    risk_budget: Decimal
    contract_cost: Decimal
    risk_per_contract: Decimal
    by_risk: int
    by_capital: int
    by_hard_cap: int
    by_exposure: int
    by_sector: int
    by_aggregate: int
    by_buying_power: int | None
    limiter: str
    risk_policy_version: str
    exit_policy_version: str


def _floor_div(budget: Decimal, unit: Decimal) -> int:
    if unit <= 0 or budget <= 0:
        return 0
    return int(budget // unit)


def size_position(
    option: OptionSnapshot,
    equity: Decimal,
    config: TradingConfig,
    open_premium: Decimal = Decimal("0"),
    open_positions: int = 0,
    same_underlying_positions: int = 0,
    sector_premium: Decimal = Decimal("0"),
    open_planned_risk: Decimal = Decimal("0"),
    buying_power: Decimal | None = None,
) -> SizePlan:
    """One formula. Capital is premium spent. Planned risk is the loss to the stop."""
    premium = option.ask * Decimal(config.contract_multiplier)
    stop = Decimal(str(config.initial_stop_decline_pct))
    max_entry = (option.mid * (Decimal("1") + Decimal(str(config.entry_buffer_percent)))).quantize(Decimal("0.01"))
    empty = SizePlan(0, max_entry, Decimal("0"), premium, Decimal("0"), 0, 0, 0, 0, 0, 0, None, "none", config.risk_policy_version, config.exit_policy_version)
    if premium <= 0 or option.ask > max_entry or equity <= 0 or stop <= 0 or stop >= 1:
        return empty
    if open_positions >= config.max_concurrent_positions or same_underlying_positions >= config.max_positions_per_underlying:
        return SizePlan(0, max_entry, equity * Decimal(str(config.risk_per_trade_pct)), premium, premium * stop, 0, 0, 0, 0, 0, 0, None, "positions", config.risk_policy_version, config.exit_policy_version)
    risk_budget = equity * Decimal(str(config.risk_per_trade_pct))
    normal_capital = equity * Decimal(str(config.normal_position_capital_pct))
    hard_capital = equity * Decimal(str(config.hard_position_capital_pct))
    risk_per_contract = premium * stop
    by_risk = _floor_div(risk_budget, risk_per_contract)
    by_capital = _floor_div(normal_capital, premium)
    by_hard = _floor_div(hard_capital, premium)
    by_exposure = _floor_div(equity * Decimal(str(config.max_total_open_exposure_pct)) - open_premium, premium)
    by_sector = _floor_div(equity * Decimal(str(config.max_sector_exposure_pct)) - sector_premium, premium)
    by_aggregate = _floor_div(equity * Decimal(str(config.max_aggregate_planned_risk_pct)) - open_planned_risk, risk_per_contract)
    by_bp = None if buying_power is None else _floor_div(buying_power, premium)
    candidates = {
        "risk": by_risk,
        "capital": by_capital,
        "hard_cap": by_hard,
        "exposure": by_exposure,
        "sector": by_sector,
        "aggregate_risk": by_aggregate,
        "max_contracts": config.max_contracts,
    }
    if by_bp is not None:
        candidates["buying_power"] = by_bp
    limiter = min(candidates, key=candidates.get)
    quantity = max(candidates[limiter], 0)
    capital_used = premium * Decimal(quantity)
    planned = risk_per_contract * Decimal(quantity)
    if capital_used > normal_capital or capital_used > hard_capital or planned > risk_budget:
        quantity = 0
        limiter = "none"
    return SizePlan(
        quantity,
        max_entry,
        risk_budget,
        premium,
        risk_per_contract,
        by_risk,
        by_capital,
        by_hard,
        by_exposure,
        by_sector,
        by_aggregate,
        by_bp,
        limiter,
        config.risk_policy_version,
        config.exit_policy_version,
    )


def policy_view(config: TradingConfig) -> dict:
    return {
        "profile": config.paper_policy_name,
        "risk_policy_version": config.risk_policy_version,
        "exit_policy_version": config.exit_policy_version,
        "risk_per_trade_pct": config.risk_per_trade_pct,
        "normal_position_capital_pct": config.normal_position_capital_pct,
        "hard_position_capital_pct": config.hard_position_capital_pct,
        "max_open_positions": config.max_concurrent_positions,
        "max_total_premium_exposure_pct": config.max_total_open_exposure_pct,
        "max_aggregate_planned_risk_pct": config.max_aggregate_planned_risk_pct,
        "max_sector_exposure_pct": config.max_sector_exposure_pct,
        "max_positions_per_underlying": config.max_positions_per_underlying,
        "daily_warning_pct": config.daily_warning_pct,
        "daily_entry_stop_pct": config.daily_entry_stop_pct,
        "daily_hard_stop_pct": config.daily_hard_stop_pct,
        "initial_stop_decline_pct": config.initial_stop_decline_pct,
        "protective_stop_r": config.protective_stop_r,
        "protect_at_r": config.protect_at_r,
        "trail_activate_r": config.trail_activate_r,
        "winner_run_r": config.winner_run_r,
        "trailing_pct": config.trailing_pct,
        "holding_minutes_0dte": config.holding_minutes_0dte,
        "holding_minutes_1dte": config.holding_minutes_1dte,
        "session_exit_minutes": config.session_exit_minutes,
        "contract_multiplier": config.contract_multiplier,
        "example_equity": "100000",
        "exit_precedence": [
            "KILL_SWITCH",
            "SESSION_CLOSE",
            "EXPIRATION",
            "INVALIDATION",
            "LIQUIDITY",
            "STOP",
            "PROTECTED_STOP",
            "TRAILING",
            "TIME_STOP",
        ],
    }
