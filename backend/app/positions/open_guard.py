"""Opening-session entry guards. New buys only. Exits, size, stops, and the day floor do not read this.

QQQ gap = session open / prior regular-session close − 1.

OPEN_NO_FADE: from 09:30:00 through 10:09:59 America/New_York, block a new buy against that gap.
A down gap blocks CALL. An up gap blocks PUT. A buy with the gap is left alone.

OPEN_GAP_RETRACE: from 09:45:00 through 10:09:59, if QQQ has given back at least half the gap,
block every new buy until 10:10. The retrace price is the 15-minute close, the same bar
analyze_first40.py uses (last regular-session minute at or before 09:45). A live trade is
used only inside the 09:45 minute when that bar is not in yet. Once the retrace has fired
it stays fired for that New York date, including after a worker restart.

A missing open or prior close does not block. That miss is logged as GAP_DATA_MISSING.
Both desks (the 20K book and options2) run this code. There is no switch.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import structlog

GAP_SYMBOL = "QQQ"
OPEN_NO_FADE = "OPEN_NO_FADE"
OPEN_GAP_RETRACE = "OPEN_GAP_RETRACE"
GAP_DATA_MISSING = "GAP_DATA_MISSING"

# 09:30:00 inclusive through 10:10:00 exclusive. 10:10:00 itself is a normal entry.
WINDOW_START = time(9, 30, 0)
WINDOW_END = time(10, 10, 0)
# The 15-minute reading. Rule B starts here and stays through the same window end.
RETRACE_FROM = time(9, 45, 0)
# Last moment a live trade may stand in for the 09:45 bar (the 09:45 minute).
RETRACE_TRADE_UNTIL = time(9, 46, 0)
# Stop asking the feed a few minutes after the window, once the bar has had time to print.
REFRESH_UNTIL = time(10, 15, 0)
RETRACE_PCT = Decimal("0.50")
# A gap of exactly zero is not against either side. Any other gap counts.
MIN_GAP = Decimal("0")

NY = ZoneInfo("America/New_York")
_LOCK = threading.Lock()
_memory: dict | None = None
_logged_missing: set[str] = set()

log = structlog.get_logger()


@dataclass(frozen=True)
class MinuteBar:
    start: time
    open: Decimal
    close: Decimal


@dataclass(frozen=True)
class GapReading:
    open: Decimal | None = None
    prior_close: Decimal | None = None
    bars: tuple[MinuteBar, ...] = ()
    live_price: Decimal | None = None
    live_time: time | None = None


def reset_open_guard() -> None:
    """Drop the in-process copy. Tests use this so one case cannot leak into the next."""
    global _memory
    with _LOCK:
        _memory = None
        _logged_missing.clear()


def store_path():
    from app.ops.paths import DATA_DIR

    return DATA_DIR / "open_guard.json"


def in_fade_window(moment: datetime) -> bool:
    clock = _ny(moment).timetz().replace(tzinfo=None)
    return WINDOW_START <= clock < WINDOW_END


def in_retrace_window(moment: datetime) -> bool:
    clock = _ny(moment).timetz().replace(tzinfo=None)
    return RETRACE_FROM <= clock < WINDOW_END


def gap_return(open_price: Decimal | None, prior_close: Decimal | None) -> Decimal | None:
    """Session open / prior close − 1. None when either price is missing."""
    if open_price is None or prior_close is None or prior_close <= 0 or open_price <= 0:
        return None
    return open_price / prior_close - 1


def retracement(price: Decimal | None, open_price: Decimal | None, prior_close: Decimal | None) -> Decimal | None:
    """(price − open) / (prior close − open). Positive means the gap is being given back."""
    if price is None or open_price is None or prior_close is None:
        return None
    span = prior_close - open_price
    if span == 0:
        return None
    return (price - open_price) / span


def entry_block(side: str, symbol: str, moment: datetime, state: dict | None = None) -> str | None:
    """Skip text for this new buy, or None when the buy may continue.

    Missing gap data does not block. A latched retrace still blocks inside the window
    even if a later read cannot repeat the prices.
    """
    guard = state if state is not None else guard_for(moment)
    if not in_fade_window(moment):
        return None
    opened = _decimal(guard.get("open"))
    prior = _decimal(guard.get("prior_close"))
    price = _decimal(guard.get("price_0945"))
    gap = gap_return(opened, prior)
    retrace = retracement(price, opened, prior)
    right = str(side or "").upper() or "?"
    name = str(symbol or "").strip().upper() or GAP_SYMBOL
    latched = bool(guard.get("latched")) and in_retrace_window(moment)
    fired = latched or (in_retrace_window(moment) and retrace is not None and retrace >= RETRACE_PCT)
    if fired:
        reason = _reason(OPEN_GAP_RETRACE, gap, retrace, name, right)
        _log_block(reason, gap, retrace, name, right)
        return reason
    if gap is None:
        _log_missing_once(moment, guard)
        return None
    if _against(right, gap):
        reason = _reason(OPEN_NO_FADE, gap, retrace, name, right)
        _log_block(reason, gap, retrace, name, right)
        return reason
    return None


def guard_for(moment: datetime) -> dict:
    """Today's stored reading. An empty reading means the guard cannot see the gap yet."""
    day = _ny(moment).date().isoformat()
    with _LOCK:
        if _memory is not None and _memory.get("session_date") == day:
            return dict(_memory)
        stored = _read()
        if stored is not None and stored.get("session_date") == day:
            _memory_set(stored)
            return dict(stored)
    return _blank(day)


def public_open_guard(moment: datetime | None = None) -> dict:
    """What /risk and /system show. No feed call."""
    when = moment or datetime.now(timezone.utc)
    return view(guard_for(when), when)


def refresh_open_guard(moment: datetime, providers) -> dict:
    """Read QQQ from the desk's market feed when the opening window still needs it."""
    current = guard_for(moment)
    if not _needs_refresh(current, moment):
        return current
    reading = _reading_from_providers(providers, moment)
    return commit(moment, reading)


def commit(moment: datetime, reading: GapReading) -> dict:
    """Fold one reading into today's state and keep it on disk."""
    day = _ny(moment).date().isoformat()
    with _LOCK:
        prior = _memory if _memory is not None and _memory.get("session_date") == day else _read()
        if prior is None or prior.get("session_date") != day:
            prior = None
        state = fold(prior, moment, reading)
        _memory_set(state)
        _write(state)
    if state.get("data_missing") and in_fade_window(moment):
        log.info(
            "entry.gap_data_missing",
            service="worker",
            code=GAP_DATA_MISSING,
            symbol=GAP_SYMBOL,
            open=state.get("open"),
            prior_close=state.get("prior_close"),
        )
    return state


def fold(prior: dict | None, moment: datetime, reading: GapReading) -> dict:
    """Pure update. A new New York date starts clear. A latched retrace is not cleared."""
    day = _ny(moment).date().isoformat()
    state = dict(prior) if prior and prior.get("session_date") == day else _blank(day)
    if reading.open is not None and reading.open > 0 and _clock(moment) >= WINDOW_START:
        # The first regular-session open is the gap. A later print does not move it.
        if not state.get("open"):
            state["open"] = _text(reading.open)
    if reading.prior_close is not None and reading.prior_close > 0 and not state.get("prior_close"):
        state["prior_close"] = _text(reading.prior_close)
    # The 15-minute price is not known before 09:45. A bar from 09:30 must not latch the day.
    if _clock(moment) >= RETRACE_FROM:
        price, source = _retrace_price(reading)
        if source == "bar" and price is not None and not state.get("latched"):
            state["price_0945"] = _text(price)
            state["price_source"] = "bar"
        elif source == "trade" and price is not None and not state.get("latched") and state.get("price_source") != "bar" and not state.get("price_0945"):
            state["price_0945"] = _text(price)
            state["price_source"] = "trade"
    opened = _decimal(state.get("open"))
    prior_close = _decimal(state.get("prior_close"))
    marked = _decimal(state.get("price_0945"))
    gap = gap_return(opened, prior_close)
    retrace = retracement(marked, opened, prior_close)
    if in_retrace_window(moment) and retrace is not None and retrace >= RETRACE_PCT:
        state["latched"] = True
    state["gap_pct"] = None if gap is None else _places(gap, 4)
    state["retracement_pct"] = None if retrace is None else _places(retrace, 2)
    state["data_missing"] = opened is None or prior_close is None
    state["updated_at"] = _ny(moment).isoformat()
    return state


def view(state: dict, moment: datetime) -> dict:
    """Gap, retrace, and whether new buys are blocked at this clock."""
    opened = _decimal(state.get("open"))
    prior = _decimal(state.get("prior_close"))
    price = _decimal(state.get("price_0945"))
    gap = gap_return(opened, prior)
    retrace = retracement(price, opened, prior)
    fade = in_fade_window(moment)
    retrace_on = in_retrace_window(moment)
    latched = bool(state.get("latched"))
    blocking_retrace = bool(latched and retrace_on) or bool(retrace_on and retrace is not None and retrace >= RETRACE_PCT)
    no_fade_side = None
    if fade and gap is not None and not blocking_retrace:
        if _against("CALL", gap):
            no_fade_side = "CALL"
        elif _against("PUT", gap):
            no_fade_side = "PUT"
    if blocking_retrace:
        blocked = ["CALL", "PUT"]
        status = OPEN_GAP_RETRACE
    elif no_fade_side:
        blocked = [no_fade_side]
        status = OPEN_NO_FADE
    elif state.get("data_missing") and fade:
        blocked = []
        status = GAP_DATA_MISSING
    elif fade:
        blocked = []
        status = "CLEAR"
    else:
        blocked = []
        status = "IDLE"
    return {
        "symbol": GAP_SYMBOL,
        "gap_pct": None if gap is None else _places(gap, 4),
        "retracement_pct": None if retrace is None else _places(retrace, 2),
        "open": state.get("open"),
        "prior_close": state.get("prior_close"),
        "price_0945": state.get("price_0945"),
        "price_source": state.get("price_source") or "",
        "data_missing": bool(state.get("data_missing")),
        "in_window": fade,
        "retrace_window": retrace_on,
        "latched": latched,
        "retrace_blocking": blocking_retrace,
        "no_fade_side": no_fade_side,
        "blocked_sides": blocked,
        "status": status,
        "window_start": WINDOW_START.strftime("%H:%M"),
        "window_end": WINDOW_END.strftime("%H:%M"),
        "retrace_from": RETRACE_FROM.strftime("%H:%M"),
        "retrace_pct": _places(RETRACE_PCT, 2),
        "min_gap": _places(MIN_GAP, 2),
    }


def _needs_refresh(state: dict, moment: datetime) -> bool:
    clock = _clock(moment)
    if clock < WINDOW_START or clock >= REFRESH_UNTIL:
        return False
    if state.get("session_date") != _ny(moment).date().isoformat():
        return True
    if state.get("data_missing") or not state.get("open") or not state.get("prior_close"):
        return True
    if clock >= RETRACE_FROM and state.get("price_source") != "bar" and not state.get("latched"):
        return True
    return False


def _reading_from_providers(providers, moment: datetime) -> GapReading:
    feed = getattr(getattr(providers, "market", None), "feed", None)
    if feed is None or not hasattr(feed, "session_gap"):
        return GapReading()
    try:
        raw = feed.session_gap(GAP_SYMBOL, moment)
    except Exception:
        return GapReading()
    return reading_from_raw(raw if isinstance(raw, dict) else {})


def reading_from_raw(raw: dict) -> GapReading:
    bars: list[MinuteBar] = []
    for row in raw.get("bars") or []:
        if not isinstance(row, dict):
            continue
        start = _clock_text(row.get("start"))
        opened = _decimal(row.get("open"))
        close = _decimal(row.get("close"))
        if start is None or opened is None or close is None or opened <= 0 or close <= 0:
            continue
        bars.append(MinuteBar(start, opened, close))
    bars.sort(key=lambda bar: bar.start)
    opened = _decimal(raw.get("open"))
    if opened is None or opened <= 0:
        for bar in bars:
            if bar.start == WINDOW_START:
                opened = bar.open
                break
    prior = _decimal(raw.get("prior_close"))
    live = _decimal(raw.get("live_price"))
    live_at = _clock_text(raw.get("live_time"))
    return GapReading(
        None if opened is None or opened <= 0 else opened,
        None if prior is None or prior <= 0 else prior,
        tuple(bars),
        None if live is None or live <= 0 else live,
        live_at,
    )


def _retrace_price(reading: GapReading) -> tuple[Decimal | None, str]:
    """15-minute close, then a trade inside the 09:45 minute if the bar is not in yet.

    The bar rule matches analyze_first40.bar_at_or_before(09:45): the last regular-session
    minute whose start is at or before 09:45, and that bar's close. A trade after 09:46
    is not the 15-minute price.
    """
    chosen: MinuteBar | None = None
    for bar in sorted(reading.bars, key=lambda item: item.start):
        if WINDOW_START <= bar.start <= RETRACE_FROM:
            chosen = bar
        elif bar.start > RETRACE_FROM:
            break
    if chosen is not None:
        return chosen.close, "bar"
    if reading.live_price is not None and reading.live_time is not None and RETRACE_FROM <= reading.live_time < RETRACE_TRADE_UNTIL:
        return reading.live_price, "trade"
    return None, ""


def _against(side: str, gap: Decimal) -> bool:
    if gap.copy_abs() <= MIN_GAP:
        return False
    if gap < 0 and side == "CALL":
        return True
    if gap > 0 and side == "PUT":
        return True
    return False


def _reason(code: str, gap: Decimal | None, retrace: Decimal | None, symbol: str, side: str) -> str:
    text = f"{code} gap={_percent(gap, 2)} retrace={_percent(retrace, 2)} {symbol} {side}"
    if len(text) <= 80:
        return text
    return f"{code} gap={_percent(gap, 2)} retrace={_percent(retrace, 2)} {side}"[:80]


def _log_missing_once(moment: datetime, state: dict) -> None:
    """One GAP_DATA_MISSING line per New York date when a buy is considered without a gap."""
    if not in_fade_window(moment) or not state.get("data_missing"):
        return
    day = str(state.get("session_date") or _ny(moment).date().isoformat())
    with _LOCK:
        if day in _logged_missing:
            return
        _logged_missing.add(day)
    log.info(
        "entry.gap_data_missing",
        service="worker",
        code=GAP_DATA_MISSING,
        symbol=GAP_SYMBOL,
        open=state.get("open"),
        prior_close=state.get("prior_close"),
    )


def _log_block(reason: str, gap: Decimal | None, retrace: Decimal | None, symbol: str, side: str) -> None:
    code = reason.split(" ", 1)[0]
    log.info(
        "entry.open_guard",
        service="worker",
        code=code,
        reason=reason,
        gap_pct=_percent(gap, 4),
        retracement_pct=_percent(retrace, 2),
        symbol=symbol,
        side=side,
        gap_symbol=GAP_SYMBOL,
    )


def _blank(day: str) -> dict:
    return {
        "session_date": day,
        "symbol": GAP_SYMBOL,
        "open": None,
        "prior_close": None,
        "price_0945": None,
        "price_source": "",
        "latched": False,
        "gap_pct": None,
        "retracement_pct": None,
        "data_missing": True,
        "updated_at": None,
    }


def _percent(value: Decimal | None, places: int) -> str:
    if value is None:
        return "n/a"
    return f"{_places(value, places)}%"


def _places(value: Decimal, places: int) -> str:
    quant = Decimal("1").scaleb(-places)
    return str((value * Decimal("100")).quantize(quant))


def _text(value: Decimal) -> str:
    return format(value, "f")


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value if value is not None else "").strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
    if not number.is_finite():
        return None
    return number


def _clock_text(value: object) -> time | None:
    if isinstance(value, time):
        return value.replace(tzinfo=None)
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return time(hour, minute, second)


def _ny(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(NY)


def _clock(moment: datetime) -> time:
    return _ny(moment).timetz().replace(tzinfo=None)


def _memory_set(state: dict) -> None:
    global _memory
    _memory = dict(state)


def _read() -> dict | None:
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write(state: dict) -> None:
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return
