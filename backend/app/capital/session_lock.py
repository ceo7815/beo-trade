"""Session-open equity for the daily kill and the scaled day ladder.

Alpaca ``last_equity`` is the prior close and does not move during the session.
That is the open the kill is locked to. A current-equity reading must not replace it:
the kill used to shrink as the account lost money (8% of a smaller number).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ops.paths import DATA_DIR

_NY = ZoneInfo("America/New_York")


def store_path():
    return DATA_DIR / "session_open.json"


def session_date_of(moment: datetime | None = None) -> str:
    stamp = moment or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(_NY).date().isoformat()


def read_locked_open_equity(session_date: str | None = None) -> Decimal | None:
    """The equity locked for ``session_date`` (today in New York when omitted)."""
    day = session_date or session_date_of()
    raw = _read()
    if not raw or raw.get("session_date") != day:
        return None
    return _decimal(raw.get("open_equity"))


def open_equity_from_account(account: dict | None, moment: datetime | None = None, *, persist: bool = True) -> Decimal | None:
    """Lock (or read) the session open from an Alpaca account payload."""
    if not isinstance(account, dict):
        return read_locked_open_equity(session_date_of(moment))
    return locked_open_equity(
        session_date_of(moment),
        last_equity=_decimal(account.get("last_equity")),
        equity=_decimal(account.get("equity")),
        persist=persist,
    )


def locked_open_equity(
    session_date: str,
    *,
    last_equity: Decimal | None = None,
    equity: Decimal | None = None,
    persist: bool = True,
) -> Decimal | None:
    """Return the session-open equity, writing it the first time ``last_equity`` is known.

    ``last_equity`` always wins over a weaker snapshot that was stored from live equity.
    Live equity never overwrites a ``last_equity`` lock.
    """
    opened = _positive(last_equity)
    current = _positive(equity)
    raw = _read()
    same_day = raw if raw and raw.get("session_date") == session_date else None
    stored = _decimal(same_day.get("open_equity")) if same_day else None
    source = str((same_day or {}).get("source") or "")
    if opened is not None:
        if stored is not None and source == "last_equity" and stored == opened:
            return stored
        if persist:
            _write(session_date, opened, "last_equity")
        return opened
    if stored is not None:
        return stored
    if current is None:
        return None
    if persist:
        _write(session_date, current, "equity")
    return current


def _positive(value: Decimal | None) -> Decimal | None:
    number = _decimal(value)
    if number is None or number <= 0:
        return None
    return number.quantize(Decimal("0.01"))


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    if number <= 0:
        return None
    return number.quantize(Decimal("0.01"))


def _read() -> dict | None:
    path = store_path()
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _write(session_date: str, equity: Decimal, source: str) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_date": session_date,
        "open_equity": str(equity.quantize(Decimal("0.01"))),
        "source": source,
    }
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)
