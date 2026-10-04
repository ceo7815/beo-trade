from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal


HORIZONS = (5, 15, 30, 60, 120)
RULE_ID = "RETURN_TARGET_V1"


def evaluate_horizons(
    entry_time: datetime,
    entry_price: Decimal,
    ticks: list[tuple[datetime, Decimal]],
    target_percent: float,
) -> list[dict]:
    if entry_price <= 0:
        return []
    ordered = sorted(ticks, key=lambda item: item[0])
    results = []
    target = Decimal(str(target_percent))
    for minutes in HORIZONS:
        end = entry_time + timedelta(minutes=minutes)
        window = [(moment, price) for moment, price in ordered if entry_time <= moment <= end]
        if not window:
            continue
        peak_at, peak = max(window, key=lambda item: item[1])
        trough_at, trough = min(window, key=lambda item: item[1])
        last_price = window[-1][1]
        return_pct = (last_price - entry_price) / entry_price
        max_gain = (peak - entry_price) / entry_price
        max_loss = (trough - entry_price) / entry_price
        results.append(
            {
                "horizon_minutes": minutes,
                "return_percent": return_pct.quantize(Decimal("0.0001")),
                "max_gain_percent": max_gain.quantize(Decimal("0.0001")),
                "max_loss_percent": max_loss.quantize(Decimal("0.0001")),
                "time_to_peak_seconds": int((peak_at - entry_time).total_seconds()),
                "time_to_drawdown_seconds": int((trough_at - entry_time).total_seconds()),
                "success": max_gain >= target,
                "rule": RULE_ID,
                "observed_at": window[-1][0],
            }
        )
    return results
