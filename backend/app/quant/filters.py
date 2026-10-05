from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.config.settings import TradingConfig
from app.schemas.domain import OptionSnapshot, UnderlyingSnapshot


def quote_age_seconds(observed_at: datetime, as_of: datetime) -> float:
    return (as_of - observed_at).total_seconds()


def reject_underlying(snapshot: UnderlyingSnapshot, config: TradingConfig, as_of: datetime) -> str | None:
    """Stage A eligibility. Volume below the old 1,000,000 mark is not a rejection."""
    age = quote_age_seconds(snapshot.observed_at, as_of)
    if age < 0:
        return "future_quote"
    if age > config.min_data_freshness_seconds:
        return "stale_underlying"
    if not snapshot.session_ok:
        return "session_closed"
    if snapshot.price <= 0 or snapshot.volume <= 0:
        return "underlying_liquidity"
    if snapshot.relative_volume < Decimal(str(config.min_relative_volume)):
        return "relative_volume"
    if abs(snapshot.change_percent) < Decimal(str(config.min_underlying_move_percent)):
        return "underlying_move"
    return None


def would_fail_old_volume_floor(snapshot: UnderlyingSnapshot, config: TradingConfig) -> bool:
    """The retired hard floor stays visible. It does not block the option chain."""
    return int(snapshot.volume) < int(config.min_underlying_volume)


def rank_underlyings(
    underlyings: list[UnderlyingSnapshot],
    as_of: datetime,
    priority: float = 1.8,
) -> list[UnderlyingSnapshot]:
    """Stage B. RV at or above the priority mark first, then RV, move, freshness, liquidity."""

    def key(item: UnderlyingSnapshot) -> tuple:
        age = quote_age_seconds(item.observed_at, as_of)
        relative = float(item.relative_volume)
        return (
            0 if relative >= priority else 1,
            -relative,
            -abs(float(item.change_percent)),
            age,
            -int(item.volume),
            item.symbol,
        )

    return sorted(underlyings, key=key)


def reject_option(
    option: OptionSnapshot,
    underlying: UnderlyingSnapshot,
    config: TradingConfig,
    as_of: datetime,
    session_date,
) -> str | None:
    age = quote_age_seconds(option.observed_at, as_of)
    if age < 0:
        return "future_quote"
    if age > config.min_data_freshness_seconds:
        return "stale_option"
    if option.bid <= 0 or option.ask < option.bid:
        return "quote_quality"
    mid = option.mid
    if mid <= 0:
        return "quote_quality"
    spread = (option.ask - option.bid) / mid
    if spread > Decimal(str(config.max_bid_ask_spread)):
        return "spread"
    if option.volume < config.min_option_volume:
        return "option_volume"
    if option.open_interest < config.min_open_interest:
        return "open_interest"
    if mid < Decimal(str(config.min_option_price)) or mid > Decimal(str(config.max_option_price)):
        return "premium_band"
    dte = (option.expiration - session_date).days
    if dte < config.min_expiration_days or dte > config.max_expiration_days:
        return "expiration"
    if option.implied_volatility is None or option.implied_volatility <= 0:
        return "iv_missing"
    if option.delta is None or option.gamma is None or option.theta is None or option.vega is None:
        return "greeks_missing"
    absolute_delta = abs(option.delta)
    if absolute_delta < Decimal(str(config.min_delta)) or absolute_delta > Decimal(str(config.max_delta)):
        return "delta_band"
    if option.right.value == "CALL" and option.delta <= 0:
        return "delta_sign"
    if option.right.value == "PUT" and option.delta >= 0:
        return "delta_sign"
    if option.gamma < Decimal(str(config.min_gamma)):
        return "gamma"
    if mid > 0 and abs(option.theta) / mid > Decimal(str(config.max_theta_threshold)):
        return "theta"
    if underlying.symbol != option.underlying:
        return "underlying_mismatch"
    return None
