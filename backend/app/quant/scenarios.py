from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.config.settings import TradingConfig
from app.quant.black_scholes import greeks, implied_volatility, price, to_decimal
from app.schemas.domain import OptionRight, OptionSnapshot, ScenarioResult, UnderlyingSnapshot


def year_fraction(expiration_date, as_of: datetime) -> float:
    expiry = datetime(
        expiration_date.year,
        expiration_date.month,
        expiration_date.day,
        21,
        0,
        tzinfo=as_of.tzinfo,
    )
    seconds = (expiry - as_of).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24 * 3600)


def enrich_option(
    option: OptionSnapshot,
    underlying: UnderlyingSnapshot,
    config: TradingConfig,
    as_of: datetime,
) -> OptionSnapshot:
    years = year_fraction(option.expiration, as_of)
    spot = float(underlying.price)
    strike = float(option.strike)
    mid = float(option.mid)
    sigma = float(option.implied_volatility) if option.implied_volatility else None
    if sigma is None or sigma <= 0:
        sigma = implied_volatility(
            option.right, mid, spot, strike, years, config.risk_free_rate, config.dividend_yield,
        )
    computed = None
    if sigma and sigma > 0:
        computed = greeks(
            option.right, spot, strike, years, config.risk_free_rate, config.dividend_yield, sigma,
        )
    return OptionSnapshot(
        underlying=option.underlying,
        option_symbol=option.option_symbol,
        right=option.right,
        strike=option.strike,
        expiration=option.expiration,
        bid=option.bid,
        ask=option.ask,
        last=option.last,
        volume=option.volume,
        open_interest=option.open_interest,
        observed_at=option.observed_at,
        implied_volatility=to_decimal(sigma) if sigma else option.implied_volatility,
        delta=option.delta if option.delta is not None else (to_decimal(computed["delta"]) if computed else None),
        gamma=option.gamma if option.gamma is not None else (to_decimal(computed["gamma"]) if computed else None),
        theta=option.theta if option.theta is not None else (to_decimal(computed["theta"]) if computed else None),
        vega=option.vega if option.vega is not None else (to_decimal(computed["vega"]) if computed else None),
    )


def scenarios_for(
    option: OptionSnapshot,
    underlying: UnderlyingSnapshot,
    config: TradingConfig,
    as_of: datetime,
) -> list[ScenarioResult]:
    if option.implied_volatility is None:
        return []
    years = year_fraction(option.expiration, as_of)
    base = float(option.mid)
    sigma = float(option.implied_volatility)
    results: list[ScenarioResult] = []
    for move in config.scenario_moves_percent:
        spot = float(underlying.price) * (1 + move / 100.0)
        theoretical = price(
            option.right,
            spot,
            float(option.strike),
            years,
            config.risk_free_rate,
            config.dividend_yield,
            sigma,
        )
        change = theoretical - base
        change_percent = (change / base * 100.0) if base else 0.0
        results.append(
            ScenarioResult(
                move_percent=move,
                underlying_price=to_decimal(spot, "0.01"),
                option_price=to_decimal(max(theoretical, 0.0), "0.0001"),
                change_dollars=to_decimal(change, "0.0001"),
                change_percent=to_decimal(change_percent, "0.01"),
            )
        )
    return results


def call_put_sign(right: OptionRight) -> int:
    return 1 if right is OptionRight.CALL else -1
