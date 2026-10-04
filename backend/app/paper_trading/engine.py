from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from app.config.settings import TradingConfig
from app.schemas.domain import ExitSignal, OptionSnapshot, PaperFill, Recommendation, UnderlyingSnapshot


def contract_cost(price: Decimal, quantity: int, multiplier: int) -> Decimal:
    return price * Decimal(quantity) * Decimal(multiplier)


def pnl(entry: Decimal, exit_price: Decimal, quantity: int, multiplier: int) -> tuple[Decimal, Decimal]:
    dollars = (exit_price - entry) * Decimal(quantity) * Decimal(multiplier)
    percent = (exit_price - entry) / entry if entry else Decimal("0")
    return dollars.quantize(Decimal("0.01")), percent.quantize(Decimal("0.0001"))


def open_paper(recommendation: Recommendation, config: TradingConfig, now: datetime) -> PaperFill | None:
    if recommendation.decision.value != "BUY":
        return None
    slipped = (recommendation.ask * (Decimal("1") + Decimal(str(config.slippage_percent)))).quantize(Decimal("0.01"))
    if slipped > recommendation.max_entry_price or recommendation.quantity < 1:
        return None
    return PaperFill(
        trade_id=str(uuid4()),
        recommendation_id=recommendation.recommendation_id,
        option_symbol=recommendation.option_symbol,
        quantity=recommendation.quantity,
        entry_price=slipped,
        entry_at=now,
        bid_at_entry=recommendation.bid,
        ask_at_entry=recommendation.ask,
        iv_at_entry=recommendation.iv,
        delta_at_entry=recommendation.delta,
        gamma_at_entry=recommendation.gamma,
        theta_at_entry=recommendation.theta,
        vega_at_entry=recommendation.vega,
        underlying_at_entry=recommendation.underlying_price,
    )


def mark_excursion(fill: PaperFill, bid: Decimal) -> None:
    move = bid - fill.entry_price
    if move > fill.max_favorable:
        fill.max_favorable = move
    adverse = -move if move < 0 else Decimal("0")
    if adverse > fill.max_adverse:
        fill.max_adverse = adverse


def _dte(option: OptionSnapshot, now: datetime) -> int:
    return (option.expiration - now.date()).days


def _holding_limit(dte: int, config: TradingConfig) -> int | None:
    if dte <= 0:
        return config.holding_minutes_0dte
    if dte == 1:
        return config.holding_minutes_1dte
    return None


def _expiration_due(option: OptionSnapshot, now: datetime, config: TradingConfig, session_close: datetime | None) -> bool:
    """0DTE and 1DTE flatten before the session close. Midnight is not a close."""
    if session_close is None:
        return False
    dte = (option.expiration - session_close.astimezone(now.tzinfo).date()).days
    if dte < 0 or dte > 1:
        return False
    until_close = (session_close - now).total_seconds() / 60
    return 0 <= until_close <= config.exit_before_expiration_minutes


def exit_signal(
    fill: PaperFill,
    option: OptionSnapshot,
    underlying: UnderlyingSnapshot,
    now: datetime,
    config: TradingConfig,
    session_open: bool,
    session_close: datetime | None = None,
    locked: dict | None = None,
) -> ExitSignal | None:
    """Precedence: kill, session, expiration, invalidation, liquidity, stop, trail, time.

    A bid at or below zero is not an executable quote, so no order is invented.
    Reaching +2R does not close the position by itself.
    """
    if option.observed_at > now or underlying.observed_at > now:
        return None
    if option.bid <= 0:
        return None
    mark_excursion(fill, option.bid)
    if config.kill_switch:
        return ExitSignal("KILL_SWITCH", option.bid, option.observed_at)
    if not session_open:
        return ExitSignal("SESSION_CLOSE", option.bid, option.observed_at)
    if session_close is not None:
        until_close = (session_close - now).total_seconds() / 60
        if 0 <= until_close <= config.session_exit_minutes:
            return ExitSignal("SESSION_CLOSE", option.bid, option.observed_at)
    if _expiration_due(option, now, config, session_close):
        return ExitSignal("EXPIRATION", option.bid, option.observed_at)
    if fill.underlying_at_entry > 0:
        against = Decimal(str(config.invalidation_underlying_percent))
        if fill.delta_at_entry >= 0 and underlying.price <= fill.underlying_at_entry * (Decimal("1") - against):
            return ExitSignal("INVALIDATION", option.bid, option.observed_at)
        if fill.delta_at_entry < 0 and underlying.price >= fill.underlying_at_entry * (Decimal("1") + against):
            return ExitSignal("INVALIDATION", option.bid, option.observed_at)
    spread = (option.ask - option.bid) / option.mid if option.mid > 0 else None
    if spread is not None and spread > Decimal(str(config.liquidity_exit_spread)):
        return ExitSignal("LIQUIDITY", option.bid, option.observed_at)
    if option.volume < config.min_option_volume or option.open_interest < config.min_open_interest:
        return ExitSignal("LIQUIDITY", option.bid, option.observed_at)
    one_r = fill.entry_price * Decimal(str(config.initial_stop_decline_pct))
    if locked and locked.get("one_r_price"):
        one_r = Decimal(str(locked["one_r_price"]))
    protected_mode = bool(locked and locked.get("protected_mode"))
    if one_r > 0 and (protected_mode or fill.max_favorable >= one_r * Decimal(str(config.protect_at_r))):
        protected = Decimal(str(locked["protected_stop_price"])) if locked and locked.get("protected_stop_price") else fill.entry_price - one_r * Decimal(str(config.protective_stop_r))
        if option.bid <= protected:
            return ExitSignal("PROTECTED_STOP", option.bid, option.observed_at)
    else:
        stop = Decimal(str(locked["initial_stop_price"])) if locked and locked.get("initial_stop_price") else fill.entry_price * (Decimal("1") - Decimal(str(config.initial_stop_decline_pct)))
        if option.bid <= stop:
            return ExitSignal("STOP", option.bid, option.observed_at)
    trailing_on = config.trailing_enabled and (
        bool(locked and locked.get("trailing_active")) or (one_r > 0 and fill.max_favorable >= one_r * Decimal(str(config.trail_activate_r)))
    )
    if trailing_on:
        trail = Decimal(str(locked["trailing_trigger"])) if locked and locked.get("trailing_trigger") else (fill.entry_price + fill.max_favorable) * (Decimal("1") - Decimal(str(config.trailing_pct)))
        if option.bid <= trail:
            return ExitSignal("TRAILING", option.bid, option.observed_at)
    held_minutes = (now - fill.entry_at).total_seconds() / 60
    limit = _holding_limit(_dte(option, now), config)
    if limit is not None and held_minutes >= limit:
        return ExitSignal("TIME_STOP", option.bid, option.observed_at)
    return None


def close_paper(fill: PaperFill, signal: ExitSignal) -> None:
    fill.exit_price = signal.exit_price
    fill.exit_at = signal.observed_at
    fill.exit_reason = signal.reason
