from __future__ import annotations

from decimal import Decimal

from app.config.settings import TradingConfig
from app.schemas.domain import UnderlyingSnapshot


def at_day_high(snapshot: UnderlyingSnapshot) -> bool:
    return snapshot.high > 0 and snapshot.price >= snapshot.high * Decimal("0.998")


def at_day_low(snapshot: UnderlyingSnapshot) -> bool:
    return snapshot.low > 0 and snapshot.price <= snapshot.low * Decimal("1.002")


def continuation(snapshot: UnderlyingSnapshot) -> bool:
    """The day's move is still in force: up and at the high, or down and at the low."""
    if snapshot.change_percent > 0:
        return at_day_high(snapshot)
    if snapshot.change_percent < 0:
        return at_day_low(snapshot)
    return False


def detect_events(snapshot: UnderlyingSnapshot, config: TradingConfig) -> set[str]:
    events: set[str] = set()
    move_floor = Decimal(str(config.min_underlying_move_percent))
    if snapshot.prior_close > 0:
        gap = (snapshot.open - snapshot.prior_close) / snapshot.prior_close
        if abs(gap) >= Decimal(str(config.gap_percent)):
            events.add("GAP")
    if snapshot.relative_volume >= Decimal(str(config.relative_volume_priority)):
        events.add("UNUSUAL_VOLUME")
    move = snapshot.change_percent
    if move >= move_floor:
        events.add("MOMENTUM")
        if at_day_high(snapshot):
            events.add("BREAKOUT")
    if move <= -move_floor:
        events.add("MOMENTUM")
        if at_day_low(snapshot):
            events.add("BREAKDOWN")
    if snapshot.bars:
        closes = [bar.close for bar in snapshot.bars]
        if len(closes) >= 4:
            first = closes[len(closes) // 2] - closes[0]
            second = closes[-1] - closes[len(closes) // 2]
            if first * second < 0 and abs(second) > abs(first) * Decimal("0.5"):
                events.add("REVERSAL")
    return events
