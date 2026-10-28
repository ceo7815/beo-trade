"""UTC timestamps for audit logs and alerts.

Legacy rows are naive. They are UTC wall time, not server-local and not
America/New_York.

Writers already pass ``datetime.now(timezone.utc)``. SQLite has no timezone
type, and SQLAlchemy's ``DateTime`` stored those UTC components without an
offset (``2026-10-28 06:49:00.000000``). The UI then parsed the string as
Asia/Dubai local time, so a 06:49 UTC event rendered as 06:49 instead of
10:49. Dubai is UTC+4, which is the four-hour early shift. A New York wall
clock stored by mistake would be eight or nine hours away from Dubai, not four.

New rows are stored as UTC with an explicit ``+00:00`` offset. The separator
stays a space so the text still sorts with the legacy strings.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator


def as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive value. Aware values are converted to UTC."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).isoformat()


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    return as_utc(moment)


def format_stored(value: datetime) -> str:
    moment = as_utc(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"


class UtcDateTime(TypeDecorator):
    """Persist timezone-aware UTC. Naive legacy strings are read as UTC."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = parse_utc(value)
        if not isinstance(value, datetime):
            raise TypeError("UTC timestamp required")
        return format_stored(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, datetime):
            return as_utc(value)
        return parse_utc(str(value))
