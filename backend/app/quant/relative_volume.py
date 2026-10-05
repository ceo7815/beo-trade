from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

EXCHANGE = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
LAST_BAR_OPEN = time(15, 59)


def time_of_day_relative_volume(
    bars: list[tuple[datetime, int]],
    as_of: datetime,
    session_day: date,
    holidays: frozenset[date] | set[date] = frozenset(),
    sessions: int = 20,
) -> dict | None:
    """Today's completed-minute volume divided by the same clock window on prior sessions.

    The current minute is excluded on every day. Bars after the cutoff, bars from
    the session day in the average, and later calendar days are ignored.
    """
    local = as_of.astimezone(EXCHANGE)
    cutoff = local.replace(second=0, microsecond=0) - timedelta(minutes=1)
    if cutoff.date() != local.date() or cutoff.time() < SESSION_OPEN:
        return None
    if cutoff.time() > LAST_BAR_OPEN:
        cutoff = cutoff.replace(hour=LAST_BAR_OPEN.hour, minute=LAST_BAR_OPEN.minute, second=0, microsecond=0)
    cutoff_clock = cutoff.time()
    by_day: dict[date, int] = {}
    for moment, volume in bars:
        if volume < 0:
            continue
        bar = moment.astimezone(EXCHANGE)
        if bar > as_of or bar.date() > session_day:
            continue
        if bar.weekday() >= 5 or bar.date() in holidays:
            continue
        if bar.time() < SESSION_OPEN or bar.time() > cutoff_clock:
            continue
        by_day[bar.date()] = by_day.get(bar.date(), 0) + int(volume)
    if session_day not in by_day:
        return None
    numerator = by_day[session_day]
    prior = [(day, total) for day, total in by_day.items() if day < session_day and total > 0]
    prior.sort()
    prior = prior[-sessions:]
    if not prior:
        return None
    denominator = sum((Decimal(total) for _, total in prior), Decimal("0")) / Decimal(len(prior))
    if denominator <= 0:
        return None
    return {
        "method": "time_of_day_cumulative",
        "numerator": numerator,
        "denominator": denominator,
        "day_count": len(prior),
        "cutoff": cutoff,
        "as_of": local,
        "prior_days": tuple(day.isoformat() for day, _ in prior),
        "prior_totals": tuple(total for _, total in prior),
    }
