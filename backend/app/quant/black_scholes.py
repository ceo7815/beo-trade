from __future__ import annotations

import math
from decimal import Decimal

from app.schemas.domain import OptionRight


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(spot: float, strike: float, years: float, rate: float, dividend: float, sigma: float) -> float:
    return (
        math.log(spot / strike) + (rate - dividend + 0.5 * sigma * sigma) * years
    ) / (sigma * math.sqrt(years))


def price(
    right: OptionRight,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend: float,
    sigma: float,
) -> float:
    if years <= 0 or sigma <= 0:
        intrinsic = spot - strike if right is OptionRight.CALL else strike - spot
        return max(intrinsic, 0.0)
    d1 = _d1(spot, strike, years, rate, dividend, sigma)
    d2 = d1 - sigma * math.sqrt(years)
    discount_spot = math.exp(-dividend * years)
    discount_strike = math.exp(-rate * years)
    if right is OptionRight.CALL:
        return spot * discount_spot * _norm_cdf(d1) - strike * discount_strike * _norm_cdf(d2)
    return strike * discount_strike * _norm_cdf(-d2) - spot * discount_spot * _norm_cdf(-d1)


def greeks(
    right: OptionRight,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend: float,
    sigma: float,
) -> dict[str, float]:
    if years <= 0 or sigma <= 0 or spot <= 0:
        delta = 1.0 if right is OptionRight.CALL and spot > strike else 0.0
        if right is OptionRight.PUT and spot < strike:
            delta = -1.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1 = _d1(spot, strike, years, rate, dividend, sigma)
    d2 = d1 - sigma * math.sqrt(years)
    pdf = _norm_pdf(d1)
    discount_spot = math.exp(-dividend * years)
    discount_strike = math.exp(-rate * years)
    gamma = discount_spot * pdf / (spot * sigma * math.sqrt(years))
    vega = spot * discount_spot * pdf * math.sqrt(years)
    if right is OptionRight.CALL:
        delta = discount_spot * _norm_cdf(d1)
        theta = (
            -spot * discount_spot * pdf * sigma / (2 * math.sqrt(years))
            - rate * strike * discount_strike * _norm_cdf(d2)
            + dividend * spot * discount_spot * _norm_cdf(d1)
        )
    else:
        delta = discount_spot * (_norm_cdf(d1) - 1.0)
        theta = (
            -spot * discount_spot * pdf * sigma / (2 * math.sqrt(years))
            + rate * strike * discount_strike * _norm_cdf(-d2)
            - dividend * spot * discount_spot * _norm_cdf(-d1)
        )
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}


def implied_volatility(
    right: OptionRight,
    target: float,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    dividend: float,
) -> float | None:
    if years <= 0 or target <= 0 or spot <= 0 or strike <= 0:
        return None
    sigma = 0.5
    for _ in range(60):
        model = price(right, spot, strike, years, rate, dividend, sigma)
        vega = greeks(right, spot, strike, years, rate, dividend, sigma)["vega"]
        if vega < 1e-8:
            return None
        sigma -= (model - target) / vega
        if sigma < 1e-4:
            sigma = 1e-4
        if abs(model - target) < 1e-4:
            return sigma
    return sigma if sigma > 0 else None


def to_decimal(value: float, places: str = "0.000001") -> Decimal:
    return Decimal(str(round(value, 6))).quantize(Decimal(places))
