"""Macro-event skip.

TODO: skip FOMC and CPI days when a calendar exists in this repo.
No calendar is shipped, so macro_reason() does not skip any session.
"""

from __future__ import annotations

from datetime import date


def macro_reason(day: date, blocked: set[date] | None = None) -> str | None:
    """Return 'macro' when `day` is on a caller-supplied calendar. Otherwise None."""
    if blocked and day in blocked:
        return "macro"
    return None
