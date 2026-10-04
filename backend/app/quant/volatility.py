from __future__ import annotations

import math
from decimal import Decimal


def historical_volatility(closes: list[float], periods_per_year: float = 252) -> float | None:
    if len(closes) < 3:
        return None
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes)) if closes[index - 1] > 0]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def iv_rank(current: float, history: list[float]) -> float | None:
    if len(history) < 2:
        return None
    low = min(history)
    high = max(history)
    if high == low:
        return 0.0
    return (current - low) / (high - low) * 100.0


def iv_percentile(current: float, history: list[float]) -> float | None:
    if not history:
        return None
    return sum(1 for item in history if item <= current) / len(history) * 100.0


def decimal_or_none(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(value, 6)))
