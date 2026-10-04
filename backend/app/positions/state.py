"""Position memory created from an entry fill and updated by later quotes."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import TradingConfig
from app.models.tables import PositionState
from app.schemas.domain import PaperFill


def _num(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def open_state(
    session: Session,
    *,
    trade_id: str,
    symbol: str,
    underlying: str,
    entry_price: Decimal,
    quantity: int,
    entry_time: datetime,
    config: TradingConfig,
    entry_underlying: Decimal | None = None,
    entry_delta: Decimal | None = None,
    entry_iv: Decimal | None = None,
    expiration: str = "",
    dte_at_entry: int = 0,
    direction: str = "",
    thesis: str = "",
    invalidation: str = "",
) -> PositionState:
    """Create the entry record once. A second call for the same trade keeps the original entry."""
    existing = session.get(PositionState, trade_id)
    if existing is not None:
        if quantity > existing.entry_fill_quantity:
            existing.entry_fill_quantity = quantity
            _lock_risk(existing, config)
        return existing
    stop = Decimal(str(config.initial_stop_decline_pct))
    one_r_price = entry_price * stop
    row = PositionState(
        trade_id=trade_id,
        symbol=symbol.replace(" ", ""),
        underlying=underlying,
        status="open",
        entry_fill_price=_num(entry_price),
        entry_fill_quantity=quantity,
        entry_time=entry_time,
        entry_underlying_price=_num(entry_underlying),
        entry_delta=_num(entry_delta),
        entry_iv=_num(entry_iv),
        entry_option_price=_num(entry_price),
        peak_option_price=_num(entry_price),
        peak_time=entry_time,
        planned_risk=0,
        one_r_amount=0,
        initial_stop_price=_num(entry_price * (Decimal("1") - stop)),
        protected_stop_price=_num(entry_price - (one_r_price * Decimal(str(config.protective_stop_r)))),
        target_1=_num(entry_price + (one_r_price * Decimal(str(config.trail_activate_r)))),
        target_2=_num(entry_price + (one_r_price * Decimal(str(config.winner_run_r)))),
        trailing_active=False,
        trailing_peak=_num(entry_price),
        trailing_trigger=_num(entry_price * (Decimal("1") - Decimal(str(config.trailing_pct)))),
        expiration=expiration,
        dte_at_entry=dte_at_entry,
        underlying_direction=direction,
        thesis=thesis,
        invalidation=invalidation,
        risk_policy_version=config.risk_policy_version,
        exit_policy_version=config.exit_policy_version,
        protected_mode=False,
        winner_run_mode=False,
    )
    _lock_risk(row, config)
    session.add(row)
    return row


def _lock_risk(row: PositionState, config: TradingConfig) -> None:
    entry = Decimal(str(row.entry_fill_price))
    stop = Decimal(str(config.initial_stop_decline_pct))
    one_r = entry * stop * Decimal(row.entry_fill_quantity) * Decimal(config.contract_multiplier)
    row.planned_risk = float(one_r)
    row.one_r_amount = float(one_r)


def load_open_state(session: Session, symbol: str) -> PositionState | None:
    key = symbol.replace(" ", "")
    return session.scalar(
        select(PositionState).where(PositionState.symbol == key, PositionState.status == "open").limit(1)
    )


def apply_quote(
    row: PositionState,
    *,
    bid: Decimal,
    ask: Decimal,
    underlying_price: Decimal,
    iv: Decimal | None,
    now: datetime,
    config: TradingConfig,
) -> None:
    """Refresh the live mark. Entry time, 1R, and the peak are not replaced."""
    if bid > Decimal(str(row.peak_option_price)):
        row.peak_option_price = float(bid)
        row.peak_time = now
    row.trailing_peak = row.peak_option_price
    row.trailing_trigger = float(Decimal(str(row.peak_option_price)) * (Decimal("1") - Decimal(str(config.trailing_pct))))
    entry = Decimal(str(row.entry_fill_price))
    qty = Decimal(row.entry_fill_quantity)
    unrealized = (bid - entry) * qty * Decimal(config.contract_multiplier)
    one_r = Decimal(str(row.one_r_amount))
    if one_r > 0 and unrealized >= one_r:
        row.protected_mode = True
    if one_r > 0 and unrealized >= one_r * Decimal(str(config.trail_activate_r)):
        row.trailing_active = True
    if one_r > 0 and unrealized >= one_r * Decimal(str(config.winner_run_r)):
        row.winner_run_mode = True
    mid = (bid + ask) / Decimal("2")
    row.current_option_bid = float(bid)
    row.current_option_ask = float(ask)
    row.current_underlying_price = float(underlying_price)
    row.current_iv = _num(iv)
    row.current_spread = float((ask - bid) / mid) if mid > 0 else None
    row.current_unrealized_pnl = float(unrealized)
    row.current_holding_minutes = (now - _aware(row.entry_time)).total_seconds() / 60


def fill_from_state(row: PositionState) -> PaperFill:
    entry = Decimal(str(row.entry_fill_price))
    peak = Decimal(str(row.peak_option_price))
    underlying = _dec(row.entry_underlying_price)
    delta = _dec(row.entry_delta)
    return PaperFill(
        trade_id=row.trade_id,
        recommendation_id="",
        option_symbol=row.symbol,
        quantity=row.entry_fill_quantity,
        entry_price=entry,
        entry_at=_aware(row.entry_time),
        max_favorable=max(peak - entry, Decimal("0")),
        delta_at_entry=delta if delta is not None else Decimal("0"),
        underlying_at_entry=underlying if underlying is not None else Decimal("0"),
        iv_at_entry=_dec(row.entry_iv) or Decimal("0"),
    )


def locked_levels(row: PositionState) -> dict:
    entry = Decimal(str(row.entry_fill_price))
    one_r_price = Decimal("0")
    if row.entry_fill_quantity > 0:
        one_r_price = Decimal(str(row.one_r_amount)) / (Decimal(row.entry_fill_quantity) * Decimal("100"))
    return {
        "initial_stop_price": Decimal(str(row.initial_stop_price)),
        "protected_stop_price": Decimal(str(row.protected_stop_price)),
        "one_r_price": one_r_price if one_r_price > 0 else entry * Decimal("0"),
        "trailing_trigger": Decimal(str(row.trailing_trigger)),
        "trailing_active": bool(row.trailing_active),
        "protected_mode": bool(row.protected_mode),
    }
