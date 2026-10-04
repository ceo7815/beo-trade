from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import SessionCalendar

CLOSED = "CLOSED"
PRE_MARKET = "PRE_MARKET"
OPEN = "OPEN"
INTRADAY_SCAN = "INTRADAY_SCAN"
PRE_CLOSE = "PRE_CLOSE"
POST_MARKET = "POST_MARKET"
DAILY_REPORT = "DAILY_REPORT"

SCAN_PHASES = frozenset({OPEN, INTRADAY_SCAN, PRE_CLOSE})


def exchange_zone(calendar: SessionCalendar) -> ZoneInfo:
    return ZoneInfo(calendar.timezone)


def session_bounds(moment: datetime, calendar: SessionCalendar) -> tuple[datetime, datetime] | None:
    local = moment.astimezone(exchange_zone(calendar))
    if local.weekday() >= 5 or local.date() in calendar.holidays:
        return None
    close_clock = calendar.early_closes.get(local.date(), calendar.close_time)
    start = local.replace(
        hour=calendar.open_time.hour,
        minute=calendar.open_time.minute,
        second=0,
        microsecond=0,
    )
    end = local.replace(hour=close_clock.hour, minute=close_clock.minute, second=0, microsecond=0)
    return start, end


def phase_at(moment: datetime, calendar: SessionCalendar) -> str:
    local = moment.astimezone(exchange_zone(calendar))
    bounds = session_bounds(moment, calendar)
    if bounds is None:
        return CLOSED
    start, end = bounds
    pre_start = start - timedelta(minutes=calendar.pre_market_minutes)
    if local < pre_start or local >= end + timedelta(minutes=calendar.post_market_minutes):
        return CLOSED
    if local < start:
        return PRE_MARKET
    report_at = end + timedelta(minutes=calendar.daily_report_offset_minutes)
    report_end = report_at + timedelta(minutes=calendar.daily_report_window_minutes)
    if report_at <= local < report_end:
        return DAILY_REPORT
    if local >= end:
        return POST_MARKET
    if local >= end - timedelta(minutes=calendar.pre_close_minutes):
        return PRE_CLOSE
    if local < start + timedelta(minutes=calendar.open_drive_minutes):
        return OPEN
    return INTRADAY_SCAN


def format_clock(moment: datetime, timezone_name: str) -> str:
    return moment.astimezone(ZoneInfo(timezone_name)).strftime("%H:%M:%S")
