"""Last check before a paper buy. Quantity is the minimum that still passes every cap."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.broker.normalize import parse_option_symbol
from app.config.settings import TradingConfig
from app.policy.sizing import size_position
from app.schemas.domain import OptionSnapshot

OPEN_ORDER_STATES = {"NEW", "ACCEPTED", "PENDING_NEW", "PARTIALLY_FILLED", "SUBMITTED", "OPEN"}


@dataclass(frozen=True)
class Admission:
    quantity: int
    reason: str


def contract_underlying(symbol: str) -> str:
    parsed = parse_option_symbol(symbol)
    return "" if parsed is None else str(parsed["underlying"])


def _planned(entry: Decimal, quantity: Decimal, config: TradingConfig) -> Decimal:
    return entry * quantity * Decimal(config.contract_multiplier) * Decimal(str(config.initial_stop_decline_pct))


def book_planned_risk(positions: list[dict], orders: list[dict], states: dict[str, Decimal], config: TradingConfig) -> Decimal | None:
    """Dollar risk already committed. None when an open position has no entry price."""
    total = sum(states.values(), Decimal("0"))
    seen: set[str] = set(states)
    for row in positions:
        symbol = str(row.get("symbol") or "").replace(" ", "")
        if not symbol:
            continue
        seen.add(symbol)
        if symbol in states:
            total += states[symbol]
            continue
        entry = row.get("avg_entry_price")
        qty = row.get("qty")
        if entry in {None, ""} or qty in {None, ""}:
            return None
        total += _planned(Decimal(str(entry)), Decimal(str(qty)), config)
    for row in orders:
        if not _pending_buy(row):
            continue
        symbol = str(row.get("symbol") or "").replace(" ", "")
        if symbol in seen:
            continue
        price = row.get("limit_price")
        qty = row.get("qty")
        if price in {None, ""} or qty in {None, ""}:
            return None
        total += _planned(Decimal(str(price)), Decimal(str(qty)), config)
    return total


def underlying_taken(underlying: str, positions: list[dict], orders: list[dict]) -> bool:
    name = underlying.strip().upper()
    if not name:
        return False
    for row in positions:
        if contract_underlying(str(row.get("symbol") or "")) == name and Decimal(str(row.get("qty") or "0")) > 0:
            return True
    for row in orders:
        if _pending_buy(row) and contract_underlying(str(row.get("symbol") or "")) == name:
            return True
    return False


def _pending_buy(row: dict) -> bool:
    if str(row.get("position_intent") or "") != "buy_to_open":
        return False
    state = str(row.get("internal_state") or row.get("status") or "").upper()
    return state not in {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}


def admit_buy(
    option: OptionSnapshot,
    equity: Decimal,
    requested: int,
    config: TradingConfig,
    *,
    positions: list[dict],
    orders: list[dict],
    state_risk: dict[str, Decimal],
    open_premium: Decimal,
    sector_premium: Decimal,
    buying_power: Decimal | None,
    open_positions: int,
) -> Admission:
    underlying = option.underlying.strip().upper()
    if underlying_taken(underlying, positions, orders):
        return Admission(0, "UNDERLYING_POSITION_LIMIT")
    planned_open = book_planned_risk(positions, orders, state_risk, config)
    if planned_open is None:
        return Admission(0, "AGGREGATE_RISK_LIMIT")
    if option.volume < config.min_option_volume or option.open_interest < config.min_open_interest:
        return Admission(0, "LIQUIDITY")
    if option.mid > 0 and (option.ask - option.bid) / option.mid > Decimal(str(config.max_bid_ask_spread)):
        return Admission(0, "LIQUIDITY")
    plan = size_position(
        option,
        equity,
        config,
        open_premium=open_premium,
        open_positions=open_positions,
        same_underlying_positions=0,
        sector_premium=sector_premium,
        open_planned_risk=planned_open,
        buying_power=buying_power,
    )
    quantity = min(requested, plan.quantity)
    if quantity < 1:
        reason = {
            "aggregate_risk": "AGGREGATE_RISK_LIMIT",
            "positions": "UNDERLYING_POSITION_LIMIT",
            "exposure": "EXPOSURE_LIMIT",
            "sector": "SECTOR_EXPOSURE_LIMIT",
            "buying_power": "BUYING_POWER",
            "capital": "CAPITAL_LIMIT",
            "hard_cap": "HARD_CAP",
            "risk": "RISK_BUDGET",
        }.get(plan.limiter, "NO_TRADE")
        return Admission(0, reason)
    premium = option.ask * Decimal(config.contract_multiplier)
    stop = Decimal(str(config.initial_stop_decline_pct))
    capital = premium * Decimal(quantity)
    planned = premium * stop * Decimal(quantity)
    if planned > equity * Decimal(str(config.risk_per_trade_pct)):
        return Admission(0, "RISK_BUDGET")
    if capital > equity * Decimal(str(config.normal_position_capital_pct)):
        return Admission(0, "CAPITAL_LIMIT")
    if capital > equity * Decimal(str(config.hard_position_capital_pct)):
        return Admission(0, "HARD_CAP")
    if open_premium + capital > equity * Decimal(str(config.max_total_open_exposure_pct)):
        return Admission(0, "EXPOSURE_LIMIT")
    if planned_open + planned > equity * Decimal(str(config.max_aggregate_planned_risk_pct)):
        return Admission(0, "AGGREGATE_RISK_LIMIT")
    if sector_premium + capital > equity * Decimal(str(config.max_sector_exposure_pct)):
        return Admission(0, "SECTOR_EXPOSURE_LIMIT")
    if open_positions + 1 > config.max_concurrent_positions:
        return Admission(0, "MAX_POSITIONS")
    if buying_power is not None and capital > buying_power:
        return Admission(0, "BUYING_POWER")
    return Admission(quantity, plan.limiter)
