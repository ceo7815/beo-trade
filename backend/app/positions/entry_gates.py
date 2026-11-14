"""New-entry gates. Exits, size, the stop, the trail, the kill, and the day floor do not read this.

ADD_BRAKE stops new buys when today's day P&L is at or below 25% of the locked
daily kill. The day P&L is the same figure /risk uses: equity minus session-open
equity, after same-day funding and excluded lots. Open positions keep being
managed. The block is not a halt: the next check above the line may buy again.

CHASE_ATR skips one candidate when the underlying has already moved more than
0.65 times its 14-day average true range in the trade direction since today's
regular-session open. ATR uses completed daily bars only. A missing open, price,
or ATR does not block.

Both desks (the 20K book and options2) run this code. There is no switch.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import structlog

ADD_BRAKE = "ADD_BRAKE"
CHASE_ATR = "CHASE_ATR"
# Share of the locked daily kill. At or below this loss, new entries stop.
ADD_BRAKE_FRACTION = Decimal("0.25")
# Directional move since the regular-session open, in units of ATR14.
CHASE_ATR_LIMIT = Decimal("0.65")
ATR_PERIOD = 14

NY = ZoneInfo("America/New_York")
log = structlog.get_logger()


def brake_threshold(kill_usd: object) -> Decimal | None:
    """Negative dollars. New entries stop at or below this line. None when the kill is unknown."""
    kill = _positive(kill_usd)
    if kill is None:
        return None
    return -(kill * ADD_BRAKE_FRACTION)


def add_brake_reason(day_pnl: object, kill_usd: object) -> str | None:
    """ADD_BRAKE with the day P&L and the threshold, or None when entries may continue.

    Missing P&L or a missing kill does not block. Equal to the threshold blocks.
    A later call with a recovered P&L returns None: nothing is latched here.
    """
    line = brake_threshold(kill_usd)
    pnl = _signed(day_pnl)
    if line is None or pnl is None:
        return None
    if pnl > line:
        return None
    return f"{ADD_BRAKE} day_pnl={_plain(pnl)} threshold={_plain(line)}"


def atr14(bars, session_day: date | None = None) -> Decimal | None:
    """Simple 14-day average true range from completed daily bars.

    A bar on ``session_day`` (or later) is still forming and is left out.
    Fewer than 15 completed bars, a non-positive range, or a broken bar
    returns None. Callers must not block on None.
    """
    rows: list[tuple[datetime, Decimal, Decimal, Decimal]] = []
    for bar in bars or ():
        stamp = getattr(bar, "observed_at", None)
        high = _positive(getattr(bar, "high", None))
        low = _positive(getattr(bar, "low", None))
        close = _positive(getattr(bar, "close", None))
        if not isinstance(stamp, datetime) or high is None or low is None or close is None or low > high:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if session_day is not None and stamp.astimezone(NY).date() >= session_day:
            continue
        rows.append((stamp, high, low, close))
    rows.sort(key=lambda item: item[0])
    if len(rows) < ATR_PERIOD + 1:
        return None
    ranges: list[Decimal] = []
    for index in range(1, len(rows)):
        _stamp, high, low, close = rows[index]
        prev = rows[index - 1][3]
        ranges.append(max(high - low, abs(high - prev), abs(low - prev)))
    if len(ranges) < ATR_PERIOD:
        return None
    value = sum(ranges[-ATR_PERIOD:], Decimal("0")) / Decimal(ATR_PERIOD)
    if value <= 0:
        return None
    return value


def directional_move(side: object, price: object, open_price: object, atr_value: object) -> Decimal | None:
    """CALL is (price − open) / ATR. PUT is (open − price) / ATR. None when any input is missing."""
    right = str(side or "").upper()
    if right not in {"CALL", "PUT"}:
        return None
    last = _positive(price)
    opened = _positive(open_price)
    width = _positive(atr_value)
    if last is None or opened is None or width is None:
        return None
    if right == "CALL":
        return (last - opened) / width
    return (opened - last) / width


def chase_reading(
    side: object,
    price: object,
    open_price: object,
    atr_value: object,
    *,
    symbol: str = "",
    log_missing: bool = True,
) -> dict:
    """One candidate's move in ATR units, and the skip text when it is past the limit.

    Missing data is logged and ``blocked`` stays false. The move is still returned
    when it can be computed, including when it is under the limit.
    """
    right = str(side or "").upper()
    name = str(symbol or "").strip().upper()
    last = _positive(price)
    opened = _positive(open_price)
    width = _positive(atr_value)
    move = directional_move(right, last, opened, width)
    missing = move is None
    blocked = move is not None and move > CHASE_ATR_LIMIT
    reason = None
    if blocked and move is not None and opened is not None and last is not None and width is not None:
        reason = (
            f"{CHASE_ATR} move_atr={_plain(move)} open={_plain(opened)} "
            f"price={_plain(last)} atr14={_plain(width)}"
        )
    if missing and log_missing:
        log.info(
            "entry.chase_data_missing",
            service="worker",
            symbol=name,
            side=right or None,
            open=None if opened is None else str(opened),
            price=None if last is None else str(last),
            atr14=None if width is None else str(width),
        )
    return {
        "symbol": name,
        "side": right,
        "move_atr": None if move is None else _plain(move),
        "open": None if opened is None else _plain(opened),
        "price": None if last is None else _plain(last),
        "atr14": None if width is None else _plain(width),
        "limit": _plain(CHASE_ATR_LIMIT),
        "chase_blocked": blocked,
        "reason": reason,
        "missing": missing,
    }


def chase_reason(
    side: object,
    price: object,
    open_price: object,
    atr_value: object,
    *,
    symbol: str = "",
    log_missing: bool = True,
) -> str | None:
    """Skip text, or None when this candidate may continue."""
    return chase_reading(
        side,
        price,
        open_price,
        atr_value,
        symbol=symbol,
        log_missing=log_missing,
    ).get("reason")


def public_add_brake(day_pnl: object, kill_usd: object) -> dict:
    """What /risk and /system show. Does not log and does not block a buy by itself."""
    line = brake_threshold(kill_usd)
    pnl = _signed(day_pnl)
    reason = add_brake_reason(pnl, kill_usd)
    return {
        "rule": ADD_BRAKE,
        "fraction": _plain(ADD_BRAKE_FRACTION),
        "blocking": reason is not None,
        "day_pnl": None if pnl is None else _plain(pnl),
        "kill_usd": None if _positive(kill_usd) is None else _plain(_positive(kill_usd)),
        "threshold": None if line is None else _plain(line),
        "reason": reason,
        "lifts_when_recovered": True,
        "data_missing": line is None or pnl is None,
    }


def public_chase() -> dict:
    """The chase rule as a monitor sees it. Per-candidate move_atr lives on the scan stages."""
    return {
        "rule": CHASE_ATR,
        "limit": _plain(CHASE_ATR_LIMIT),
        "period": ATR_PERIOD,
        "blocks_on_missing_data": False,
    }


def session_day_of(moment: datetime) -> date:
    stamp = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return stamp.astimezone(NY).date()


def brake_inputs_from_account(config, account: dict | None, positions, activities, session_date) -> tuple[Decimal | None, Decimal | None]:
    """(day P&L, locked kill) from an account payload the caller already read.

    Day P&L is ``compute_adjusted_day_pnl`` (equity minus session-open equity,
    after funding and excluded lots). The kill is ``effective_kill_usd`` locked
    to that session open. A missing activity list leaves the P&L unknown.
    """
    from app.capital import effective_kill_usd
    from app.capital.session_lock import open_equity_from_account

    opened = None if not isinstance(account, dict) else open_equity_from_account(account, persist=False)
    kill = effective_kill_usd(config, open_equity=opened)
    kill_out = kill if kill > 0 else None
    if not isinstance(account, dict) or not isinstance(activities, list):
        return None, kill_out
    from app.trades.exclusion import compute_adjusted_day_pnl

    pnl = compute_adjusted_day_pnl(
        account.get("equity"),
        account.get("last_equity"),
        positions if isinstance(positions, list) else [],
        session_date,
        activities=activities,
    )
    return pnl, kill_out


def live_brake_inputs(settings) -> tuple[Decimal | None, Decimal | None]:
    """One account read for the scan. (None, None) when the broker cannot be read."""
    from datetime import datetime as clock

    from app.analytics.finance import read_session_funding
    from app.broker.runtime import open_paper_broker
    from app.core.sessions import exchange_zone
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError

    try:
        adapter = open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError):
        return None, None
    session_date = clock.now(timezone.utc).astimezone(exchange_zone(settings.calendar())).date()
    try:
        account = adapter.get_account()
        activities = read_session_funding(adapter, session_date)
        positions = adapter.get_positions()
    except Exception as exc:
        from app.ops.failure import log_failure

        log_failure("worker.scan_caught", exc, service="worker", where="add_brake")
        return None, None
    finally:
        adapter.close()
    if not isinstance(account, dict):
        return None, None
    return brake_inputs_from_account(
        settings.trading(),
        account,
        positions if isinstance(positions, list) else [],
        activities if isinstance(activities, list) else None,
        session_date,
    )


def _positive(value: object) -> Decimal | None:
    number = _decimal(value)
    if number is None or not number.is_finite() or number <= 0:
        return None
    return number


def _signed(value: object) -> Decimal | None:
    """A day P&L. Zero and losses are real. A blank or a non-number is missing."""
    number = _decimal(value)
    if number is None or not number.is_finite():
        return None
    return number


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
