from __future__ import annotations

import math
from decimal import Decimal


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    if min(highs) <= 0 or min(closes) <= 0:
        return None
    ranges = []
    for index in range(1, len(closes)):
        ranges.append(max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
    if len(ranges) < period:
        return None
    return sum(ranges[-period:]) / period


def vwap(prices: list[float], volumes: list[int]) -> float | None:
    if not prices or len(prices) != len(volumes):
        return None
    total_volume = sum(volumes)
    if total_volume <= 0:
        return None
    return sum(price * volume for price, volume in zip(prices, volumes) if price > 0) / total_volume


def expected_move(price: float, implied_volatility: float, days: float) -> float | None:
    if price <= 0 or implied_volatility <= 0 or days <= 0:
        return None
    return price * implied_volatility * math.sqrt(days / 365.0)


def decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(value, 6)))
