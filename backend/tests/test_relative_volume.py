from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.quant.relative_volume import time_of_day_relative_volume

ET = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 1)


def _bar(stamp: str, volume: int) -> tuple[datetime, int]:
    return datetime.fromisoformat(stamp).replace(tzinfo=ET), volume


def test_time_of_day_volume_ignores_later_bars_and_the_current_session():
    audit = time_of_day_relative_volume(
        [
            _bar("2026-09-29T09:30:00", 10),
            _bar("2026-09-29T10:59:00", 10),
            _bar("2026-09-29T15:30:00", 10_000),
            _bar("2026-09-30T09:30:00", 30),
            _bar("2026-09-30T11:00:00", 9_000),
            _bar("2026-10-01T09:30:00", 40),
            _bar("2026-10-01T10:59:00", 20),
            _bar("2026-10-01T11:00:00", 8_000),
            _bar("2026-10-02T10:00:00", 1),
        ],
        AS_OF,
        DAY,
    )
    assert audit is not None
    assert audit["numerator"] == 60
    assert audit["prior_totals"] == (20, 30)
    assert audit["denominator"] == Decimal("25")
    assert audit["prior_days"] == ("2026-09-29", "2026-09-30")
    assert audit["day_count"] == 2
    assert audit["numerator"] / audit["denominator"] == Decimal("2.4")
    assert "2026-10-01" not in audit["prior_days"]
    assert audit["cutoff"] == datetime(2026, 10, 1, 10, 59, tzinfo=ET)


def test_holiday_and_unfinished_open_are_not_used():
    holiday = time_of_day_relative_volume(
        [
            _bar("2026-09-30T10:00:00", 100),
            _bar("2026-10-01T10:00:00", 50),
        ],
        AS_OF,
        DAY,
        holidays={date(2026, 9, 30)},
    )
    assert holiday is None
    too_early = time_of_day_relative_volume(
        [_bar("2026-10-01T09:30:00", 50)],
        datetime(2026, 10, 1, 13, 30, 30, tzinfo=timezone.utc),
        DAY,
    )
    assert too_early is None
