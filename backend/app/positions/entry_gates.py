"""New-entry gates. Exits, size, the stop, the trail, the kill, and the day floor do not read this.

LOSS_STREAK stops new buys after two closed trades in a row lost money on the
current New York session day. Closed trades come from the same fill book the
history screen uses (stored broker fills reconciled with Alpaca paper FILL
activities), ordered by close time. The last two closes both below zero block.
A close above zero releases the block at once and resets the count to zero; two
new losing closes in a row are then needed. A close at exactly zero is neutral:
it is not a loss and it does not reset. Unrealized P&L is ignored, open positions
keep being managed, and the count starts over on each new session day. Nothing is
latched here: every check reads the closes again. Missing or unreadable closes do
not block.

CHASE_ATR skips one candidate when the underlying has already moved more than
0.65 times its 14-day average true range in the trade direction since today's
regular-session open. ATR uses completed daily bars only. A missing open, price,
or ATR does not block.

Both desks (the 20K book and options2) run this code. LOSS_STREAK reads
``loss_streak_enabled`` from the trading profile: the 20K book keeps it on,
Options 2 (v2610_40k) turns it off since 17.11.

OPENING_WAIT blocks every new buy in the first ``opening_wait_minutes`` after the
09:30 New York open (DST follows America/New_York). Options 2 uses 20 minutes, so
09:30:00-09:49:59 is blocked as OPENING_WAIT_20M and the first buy can go at 09:50.
0 turns it off (the 20K book).

QQQ_CHASE (index chase) skips a new buy when QQQ has already moved at least
``index_chase_pct`` from today's 09:30 New York open in the trade direction: a
CALL is skipped when QQQ >= open x (1 + pct), a PUT when QQQ <= open x (1 - pct).
Options 2 (v2610_40k) uses 0.0075 since 23.11 and logs QQQ_CHASE_075. 0 turns it
off (every other profile). A missing QQQ open or price does not block; the miss is
logged as entry.index_chase_data_missing. Exits never read it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import structlog

LOSS_STREAK = "LOSS_STREAK"
CHASE_ATR = "CHASE_ATR"
OPENING_WAIT = "OPENING_WAIT"
QQQ_CHASE = "QQQ_CHASE"
INDEX_CHASE_SYMBOL = "QQQ"
# 09:30 New York, minutes from midnight.
REGULAR_OPEN_MINUTES = 9 * 60 + 30
# Consecutive losing closes (realized P&L below zero) that stop new entries.
LOSS_STREAK_LENGTH = 2
# Recent closes echoed on /risk.
LOSS_STREAK_RECENT = 5
# Directional move since the regular-session open, in units of ATR14.
CHASE_ATR_LIMIT = Decimal("0.65")
ATR_PERIOD = 14

NY = ZoneInfo("America/New_York")
log = structlog.get_logger()


def _close_rows(closes: object) -> list[tuple[datetime, Decimal, str]] | None:
    """(closed_at, realized P&L, symbol) sorted by close time. None when closes were not read.

    A row without a usable time or P&L is skipped. Equal times keep the order given.
    """
    if closes is None or isinstance(closes, (str, bytes)):
        return None
    try:
        rows = list(closes)  # type: ignore[call-overload]
    except TypeError:
        return None
    found: list[tuple[datetime, Decimal, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = _moment(row.get("closed_at"))
        pnl = _signed(row.get("pnl"))
        if stamp is None or pnl is None:
            continue
        found.append((stamp, pnl, str(row.get("symbol") or "")))
    found.sort(key=lambda item: item[0])
    return found


def loss_streak_state(closes: object, moment: datetime | None = None) -> dict:
    """The streak as the gate reads it. Only closes on the New York day of ``moment`` count.

    ``moment`` defaults to now. ``data_missing`` is true when the closes were not read.
    """
    rows = _close_rows(closes)
    day = session_day_of(moment or datetime.now(timezone.utc))
    if rows is None:
        return {"streak": 0, "blocking": False, "data_missing": True, "closes_today": 0, "recent": [], "session_day": day.isoformat()}
    today = [item for item in rows if session_day_of(item[0]) == day]
    streak = 0
    for _stamp, pnl, _symbol in today:
        if pnl < 0:
            streak += 1
        elif pnl > 0:
            streak = 0
    recent = [
        {"closed_at": stamp.isoformat(), "pnl": _plain(pnl), "symbol": symbol, "result": "loss" if pnl < 0 else "win" if pnl > 0 else "flat"}
        for stamp, pnl, symbol in today[-LOSS_STREAK_RECENT:]
    ]
    return {
        "streak": streak,
        "blocking": streak >= LOSS_STREAK_LENGTH,
        "data_missing": False,
        "closes_today": len(today),
        "recent": recent,
        "session_day": day.isoformat(),
    }


def loss_streak_reason(closes: object, moment: datetime | None = None) -> str | None:
    """LOSS_STREAK with the count, or None when entries may continue.

    Unread closes do not block. A later call after a winning close returns None.
    """
    state = loss_streak_state(closes, moment)
    if not state["blocking"]:
        return None
    last = ",".join(item["pnl"] for item in state["recent"][-LOSS_STREAK_LENGTH:])
    return f"{LOSS_STREAK} consecutive_losses={state['streak']} threshold={LOSS_STREAK_LENGTH} last_closes={last}"


def loss_streak_enabled(trading: object) -> bool:
    """False only when the profile turns LOSS_STREAK off (Options 2). A missing field keeps it on."""
    return bool(getattr(trading, "loss_streak_enabled", True))


def loss_streak_gate(trading: object, closes: object, moment: datetime | None = None) -> str | None:
    """loss_streak_reason when the profile has the brake on, otherwise None."""
    if not loss_streak_enabled(trading):
        return None
    return loss_streak_reason(closes, moment)


def opening_wait_minutes(trading: object) -> int:
    try:
        return max(0, int(getattr(trading, "opening_wait_minutes", 0) or 0))
    except (TypeError, ValueError):
        return 0


def opening_wait_reason(moment: datetime, minutes: object) -> str | None:
    """OPENING_WAIT_<N>M from 09:30:00 to just before 09:30 + N on the New York clock. 0 is off.

    Seconds count: with N=20, 09:49:59 is blocked and 09:50:00 is not. Before the
    open this gate says nothing; the market clock already stops buys there.
    """
    try:
        wait = int(minutes or 0)
    except (TypeError, ValueError):
        return None
    if wait <= 0:
        return None
    stamp = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(NY)
    since_open = (local.hour * 60 + local.minute - REGULAR_OPEN_MINUTES) * 60 + local.second
    if 0 <= since_open < wait * 60:
        return f"{OPENING_WAIT}_{wait}M"
    return None


def opening_wait_gate(trading: object, moment: datetime) -> str | None:
    return opening_wait_reason(moment, opening_wait_minutes(trading))


def public_opening_wait(trading: object, moment: datetime | None = None) -> dict:
    """The opening wait as /risk and /system show it."""
    wait = opening_wait_minutes(trading)
    at = moment or datetime.now(timezone.utc)
    reason = opening_wait_reason(at, wait)
    until = REGULAR_OPEN_MINUTES + wait
    return {
        "rule": f"{OPENING_WAIT}_{wait}M" if wait else OPENING_WAIT,
        "enabled": wait > 0,
        "minutes": wait,
        "first_entry_ny": f"{until // 60:02d}:{until % 60:02d}" if wait else None,
        "blocking": reason is not None,
        "reason": reason,
    }


def index_chase_pct(trading: object) -> Decimal | None:
    """The profile's QQQ_CHASE threshold as a fraction (0.0075 = 0.75%). None when off or unreadable."""
    raw = getattr(trading, "index_chase_pct", 0) if trading is not None else 0
    limit = _positive(raw)
    if limit is None or limit >= 1:
        return None
    return limit


def index_chase_code(limit: Decimal) -> str:
    """Reason code in hundredths of a percent: QQQ_CHASE_075 for 0.0075, QQQ_CHASE_100 for 0.01."""
    hundredths = int((limit * Decimal("10000")).to_integral_value())
    return f"{QQQ_CHASE}_{hundredths:03d}"


def index_chase_reading(side: object, price: object, open_price: object, limit: object, *, log_missing: bool = True) -> dict:
    """QQQ move from today's open in the trade direction, and the skip text when it reached the limit.

    CALL move = price / open - 1. PUT move = 1 - price / open. Blocks when move >= limit.
    Missing open or price never blocks; it is logged once per call when ``log_missing``.
    """
    right = str(side or "").upper()
    threshold = _positive(limit)
    last = _positive(price)
    opened = _positive(open_price)
    move = None
    if right in {"CALL", "PUT"} and last is not None and opened is not None:
        change = last / opened - 1
        move = change if right == "CALL" else -change
    missing = threshold is not None and move is None
    blocked = threshold is not None and move is not None and move >= threshold
    reason = None
    if blocked and threshold is not None and move is not None and opened is not None and last is not None:
        reason = (
            f"{index_chase_code(threshold)} {INDEX_CHASE_SYMBOL}_move={_plain((move * 100).quantize(Decimal('0.01')))}% "
            f"open={_plain(opened)} price={_plain(last)} side={right}"
        )
    if missing and log_missing:
        log.info(
            "entry.index_chase_data_missing",
            service="worker",
            symbol=INDEX_CHASE_SYMBOL,
            side=right or None,
            open=None if opened is None else str(opened),
            price=None if last is None else str(last),
        )
    return {
        "symbol": INDEX_CHASE_SYMBOL,
        "side": right,
        "move_pct": None if move is None else _plain((move * 100).quantize(Decimal("0.01"))),
        "open": None if opened is None else _plain(opened),
        "price": None if last is None else _plain(last),
        "limit_pct": None if threshold is None else _plain(threshold * 100),
        "blocked": blocked,
        "reason": reason,
        "missing": missing,
    }


def index_chase_gate(trading: object, side: object, price: object, open_price: object, *, log_missing: bool = True) -> str | None:
    """Skip text when the profile has QQQ_CHASE on and QQQ already ran in this direction. Otherwise None."""
    limit = index_chase_pct(trading)
    if limit is None:
        return None
    return index_chase_reading(side, price, open_price, limit, log_missing=log_missing)["reason"]


def public_index_chase(trading: object) -> dict:
    """QQQ_CHASE as /risk and /system show it. No feed call."""
    limit = index_chase_pct(trading)
    return {
        "rule": index_chase_code(limit) if limit is not None else QQQ_CHASE,
        "enabled": limit is not None,
        "symbol": INDEX_CHASE_SYMBOL,
        "limit_pct": None if limit is None else _plain(limit * 100),
        "reference": "today's 09:30 New York open",
        "blocks_on_missing_data": False,
    }


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


def public_loss_streak(closes: object, moment: datetime | None = None, enabled: bool = True) -> dict:
    """What /risk and /system show. Does not log and does not block a buy by itself.

    ``enabled`` False (Options 2) still shows the count but never blocks.
    """
    state = loss_streak_state(closes, moment)
    reason = loss_streak_reason(closes, moment) if enabled else None
    return {
        "rule": LOSS_STREAK,
        "enabled": bool(enabled),
        "threshold": LOSS_STREAK_LENGTH,
        "current_streak": state["streak"],
        "blocking": reason is not None,
        "reason": reason,
        "releases_on_winning_close": True,
        "closes_today": state["closes_today"],
        "recent_closes": state["recent"],
        "session_day": state["session_day"],
        "data_missing": state["data_missing"],
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


def session_closes_from_fills(
    fills: list[dict],
    moment: datetime,
    multiplier: int = 100,
    exclusions: list[dict] | None = None,
) -> list[dict]:
    """Closed round trips (one contract, flat to flat) that closed on the New York day of ``moment``.

    A lot on an active bug exclusion (the same rules /risk uses for the day P&L)
    is not a close here either, so an excluded bug loss cannot start a streak.
    ``exclusions`` None loads the active rules; an unreadable table means none.
    """
    from app.analytics.trades import round_trips
    from app.trades.exclusion import active_exclusions, matches_trade

    if exclusions is None:
        try:
            exclusions = active_exclusions()
        except Exception:
            exclusions = []
    day = session_day_of(moment)
    closed = [
        row
        for row in (round_trips(fills or [], multiplier=multiplier).get("closed") or [])
        if not exclusions or not matches_trade(row, exclusions)
    ]
    # round_trips lists newest first. Oldest first keeps equal close times in fill order.
    closed.reverse()
    found = []
    for row in closed:
        stamp = _moment(row.get("closed_at"))
        if stamp is not None and session_day_of(stamp) == day:
            found.append({"symbol": row.get("symbol"), "pnl": row.get("pnl"), "closed_at": stamp.isoformat()})
    return found


def _stored_day_fills(moment: datetime) -> list[dict]:
    """Fills already reconciled into broker_fills from the day before the session on. Empty when unreadable."""
    from datetime import time, timedelta

    from app.models.db import database_ready, session_scope

    if not database_ready():
        return []
    from sqlalchemy import select

    from app.models.tables import BrokerFillRecord

    since = datetime.combine(session_day_of(moment) - timedelta(days=1), time.min, tzinfo=NY).astimezone(timezone.utc)
    payloads: list[dict] = []
    with session_scope() as session:
        rows = session.scalars(
            select(BrokerFillRecord)
            .where(BrokerFillRecord.observed_at >= since)
            .order_by(BrokerFillRecord.observed_at.asc(), BrokerFillRecord.id.asc())
        )
        for row in rows:
            payload = dict(row.payload) if isinstance(row.payload, dict) else {}
            if row.execution_id and not payload.get("execution_id"):
                payload["execution_id"] = row.execution_id
            if row.symbol and not payload.get("symbol"):
                payload["symbol"] = row.symbol
            if row.broker_order_id and not payload.get("broker_order_id"):
                payload["broker_order_id"] = row.broker_order_id
            payloads.append(payload)
    return payloads


def session_closes_from_adapter(settings, adapter, moment: datetime) -> list[dict] | None:
    """Closed trades of today's New York session for this account. None when nothing could be read.

    Stored fills and Alpaca paper FILL activities are merged by execution id, the
    same union the history screen builds, so one close is never counted twice.
    The Alpaca read starts the day before the session so a buy from yesterday
    still pairs with today's sell.
    """
    from datetime import timedelta

    from app.analytics.closed_trades import merge_fills
    from app.ops.failure import log_failure

    stored: list[dict] = []
    live: list[dict] = []
    stored_ok = False
    live_ok = False
    try:
        stored = _stored_day_fills(moment)
        stored_ok = True
    except Exception as exc:
        log_failure("worker.scan_caught", exc, service="worker", where="loss_streak_stored")
    reader = getattr(adapter, "get_all_fills", None)
    if callable(reader):
        try:
            after = (session_day_of(moment) - timedelta(days=1)).isoformat()
            rows = reader(max_pages=5, after=after)
            live = list(rows) if isinstance(rows, list) else []
            live_ok = isinstance(rows, list)
        except Exception as exc:
            log_failure("worker.scan_caught", exc, service="worker", where="loss_streak_live")
    if not stored_ok and not live_ok:
        return None
    multiplier = int(getattr(settings.trading(), "contract_multiplier", 100) or 100)
    return session_closes_from_fills(merge_fills(stored, live), moment, multiplier)


def live_session_closes(settings, moment: datetime | None = None, adapter=None) -> list[dict] | None:
    """Today's closed trades for the account this process trades. None when the broker cannot be read.

    Opens its own paper adapter when none is given. A caller that passes one keeps ownership.
    """
    from app.broker.runtime import open_paper_broker
    from app.integrations.alpaca import AlpacaNotConfigured, PaperOnlyError

    at = moment or datetime.now(timezone.utc)
    owned = adapter is None
    if owned:
        try:
            adapter = open_paper_broker(settings)
        except (AlpacaNotConfigured, PaperOnlyError):
            return None
    try:
        return session_closes_from_adapter(settings, adapter, at)
    finally:
        if owned:
            adapter.close()


def loss_streak_missing_log(*, where: str) -> None:
    log.info("entry.loss_streak_data_missing", service="worker", where=where)


def _moment(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


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
