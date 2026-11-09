"""America/New_York session clock. DST is ZoneInfo's job, not a fixed offset."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
DUBAI = ZoneInfo("Asia/Dubai")

# Spec v5: checks on the hour and the half hour, 10:00 through 15:30 New York.
DECISION_CLOCKS = (
    (10, 0),
    (10, 30),
    (11, 0),
    (11, 30),
    (12, 0),
    (12, 30),
    (13, 0),
    (13, 30),
    (14, 0),
    (14, 30),
    (15, 0),
    (15, 30),
)


def as_ny(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("spy clock requires an aware datetime")
    return moment.astimezone(NY)


def regular_close(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)


def flatten_deadline(session_close: datetime) -> datetime:
    """Two minutes before the close. 16:00 -> 15:58. An early 13:00 -> 12:58."""
    return as_ny(session_close) - timedelta(minutes=2)


def decision_instants(day: date, session_close: datetime) -> list[datetime]:
    deadline = flatten_deadline(session_close)
    instants: list[datetime] = []
    for hour, minute in DECISION_CLOCKS:
        stamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)
        if stamp < deadline:
            instants.append(stamp)
    return instants


def is_decision_minute(minute_of_day: int) -> bool:
    hour, minute = divmod(minute_of_day, 60)
    return (hour, minute) in DECISION_CLOCKS


def active_slot(now: datetime, session_close: datetime) -> datetime | None:
    """Latest decision time that has arrived and is still before the flatten.

    A restart at 10:20 still belongs to the 10:00 slot. The price used is the
    bar that closed at 10:00, not the 10:20 print.
    """
    local = as_ny(now)
    if local >= flatten_deadline(session_close):
        return None
    slots = [slot for slot in decision_instants(local.date(), session_close) if slot <= local]
    if not slots:
        return None
    return slots[-1]


def minute_of(moment: datetime) -> int:
    local = as_ny(moment)
    return local.hour * 60 + local.minute


def rth_start(moment: datetime) -> bool:
    """True when a bar *starts* inside the regular session [09:30, 16:00)."""
    local = as_ny(moment)
    minute = local.hour * 60 + local.minute
    return (9 * 60 + 30) <= minute < 16 * 60


def parse_broker_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def session_close_from_clock(now: datetime, clock: dict | None) -> datetime:
    local = as_ny(now)
    if clock:
        nxt = parse_broker_time(str(clock.get("next_close") or ""))
        if nxt is not None and as_ny(nxt).date() == local.date():
            return as_ny(nxt)
    return regular_close(local.date())


def entries_allowed(clock: dict | None) -> bool:
    """New entries only while Alpaca says the session is open."""
    return bool(clock) and clock.get("is_open") is True


def should_flatten(now: datetime, session_close: datetime) -> bool:
    return as_ny(now) >= flatten_deadline(session_close)
