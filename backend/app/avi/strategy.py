"""QQQ debit-spread rules. Pure functions. No broker and no database.

The noise boundary is the session open times one plus the 14-session average
absolute move from the open, from the SPY engine's sigma_at. This is not the
gap-adjusted band used by the Eli stock engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from app.spy.clock import NY, as_ny
from app.spy.strategy import (
    Bar,
    close_at,
    prior_sessions,
    rth_sessions,
    session_open,
    sigma_at,
    vwap_through,
)

LOOKBACK = 14
VIX_MAX = Decimal("30")
MOVE_MULT = Decimal("1")
WING = Decimal("5")
NARROW_WINGS = (Decimal("3"), Decimal("2"))
WIDTHS = (WING, *NARROW_WINGS)
TAKE_PROFIT = Decimal("1.70")
STOP_VALUE = Decimal("0.50")
TIME_EXIT = 15 * 60 + 45
WEEKLY_LOSS = Decimal("0.10")
CHASE_SECONDS = 300
STEP_SECONDS = 30
STEP = Decimal("0.01")
SIGNAL_SLOTS = ((10, 30), (11, 0), (11, 30))

__all__ = [
    "Bar",
    "Size",
    "TIME_EXIT",
    "WIDTHS",
    "as_ny",
    "chase_price",
    "choose_expiry",
    "choose_size",
    "close_at",
    "close_prices",
    "entry_order",
    "entry_prices",
    "evaluate_trend",
    "exit_limit",
    "exit_order",
    "exit_reason",
    "mark_value",
    "next_monday",
    "occ_symbol",
    "prior_sessions",
    "rth_sessions",
    "session_open",
    "sigma_at",
    "signal_slot",
    "spread_pnl",
    "vwap_pairs",
    "vwap_through",
    "week_start",
    "weekly_brake",
    "wing_strikes",
]


def _cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def signal_slot(now: datetime, recorded: set[str]) -> datetime | None:
    """Latest 10:30 / 11:00 / 11:30 check that has arrived and is not recorded.

    A check stays actionable until the next one. The 11:30 check stays
    actionable until 12:00. The price used is the bar that closed at the slot.
    """
    local = as_ny(now)
    due: list[datetime] = []
    for hour, minute in SIGNAL_SLOTS:
        slot = datetime(local.year, local.month, local.day, hour, minute, tzinfo=NY)
        if slot <= local:
            due.append(slot)
    if not due:
        return None
    slot = due[-1]
    if slot.isoformat() in recorded:
        return None
    # Each check stays open until the next one. 11:30 stays open until 12:00.
    if local >= slot + timedelta(minutes=30):
        return None
    return slot


def evaluate_trend(
    *,
    price: Decimal | None,
    open_px: Decimal | None,
    sigma: Decimal | None,
    vwap: Decimal | None,
    vix: Decimal | None,
) -> tuple[str, str, Decimal | None, Decimal | None]:
    """Return state, reason, upper, lower.

    state is up, down, none, or skipped. skipped carries the reason.
    """
    if vix is None:
        return "skipped", "vix_missing", None, None
    vix = _d(vix)
    if vix >= VIX_MAX:
        return "skipped", "vix", None, None
    if (
        price is None
        or open_px is None
        or sigma is None
        or vwap is None
        or _d(open_px) <= 0
        or _d(sigma) <= 0
    ):
        return "skipped", "data", None, None
    price = _d(price)
    open_px = _d(open_px)
    sigma = _d(sigma)
    vwap = _d(vwap)
    upper = open_px * (Decimal(1) + sigma)
    lower = open_px * (Decimal(1) - sigma)
    move = price / open_px - Decimal(1)
    up = price > upper and price > vwap and move >= MOVE_MULT * sigma and vix < VIX_MAX
    down = price < lower and price < vwap and -move >= MOVE_MULT * sigma and vix < VIX_MAX
    if up:
        return "up", "trend", upper, lower
    if down:
        return "down", "trend", upper, lower
    return "none", "inside", upper, lower


def choose_expiry(today: date, expiries: list[date]) -> date | None:
    """Nearest expiry with DTE >= 1. 0DTE is never selected. 1-3 DTE is preferred."""
    future = sorted({exp for exp in expiries if (exp - today).days >= 1})
    if not future:
        return None
    preferred = [exp for exp in future if (exp - today).days <= 3]
    return (preferred or future)[0]


def nearest_strike(price: Decimal, strikes: list[Decimal]) -> Decimal | None:
    if not strikes:
        return None
    price = _d(price)
    return min(strikes, key=lambda strike: (abs(_d(strike) - price), _d(strike)))


def wing_strikes(
    price: Decimal,
    strikes: list[Decimal],
    side: str,
    width: Decimal,
) -> tuple[Decimal, Decimal] | None:
    """Long nearest ATM. Short is `width` dollars further OTM. Both must be listed."""
    pool = sorted({_d(strike) for strike in strikes})
    long = nearest_strike(price, pool)
    if long is None:
        return None
    width = _d(width)
    short = long + width if side == "up" else long - width
    if short not in pool:
        return None
    return long, short


def occ_symbol(underlying: str, expiry: date, right: str, strike: Decimal) -> str:
    thousand = int((_d(strike) * Decimal(1000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"{underlying.upper()}{expiry:%y%m%d}{right.upper()}{thousand:08d}"


@dataclass(frozen=True)
class Size:
    contracts: int
    width: Decimal | None
    reason: str


def choose_size(
    debits: dict[Decimal, Decimal | None],
    bankroll: Decimal,
    risk_pct: Decimal,
    max_contracts: int,
    max_debit_pct: Decimal,
) -> Size:
    """Size a debit spread off the virtual bankroll.

    A $5 wing is first. When its debit is inside the risk budget, contracts are
    floor(risk / (debit×100)), at least 1 and at most max_contracts.
    When the $5 debit is larger than the risk budget, one contract is allowed
    only if the cash debit is within max_debit_pct of the bankroll. Otherwise
    the wing narrows to $3, then $2. If those are still too big, skip.
    """
    bankroll = _d(bankroll)
    risk_pct = _d(risk_pct)
    max_debit_pct = _d(max_debit_pct)
    if bankroll <= 0 or risk_pct <= 0 or max_contracts < 1:
        return Size(0, None, "skip")
    risk = bankroll * risk_pct
    cap = bankroll * max_debit_pct
    for index, width in enumerate(WIDTHS):
        debit = debits.get(width)
        if debit is None:
            continue
        debit = _d(debit)
        if debit <= 0:
            continue
        cost = debit * Decimal(100)
        raw = int((risk / cost).to_integral_value(rounding=ROUND_DOWN))
        if raw >= 1:
            qty = min(int(max_contracts), raw)
            return Size(qty, width, "sized" if index == 0 else "narrowed")
        if cost <= cap:
            return Size(1, width, "one_lot" if index == 0 else "narrowed")
    return Size(0, None, "SKIP_DEBIT_CAP")


def _leg(quote: dict | None) -> tuple[Decimal, Decimal, Decimal] | None:
    if not quote:
        return None
    try:
        bid = _d(quote["bid"])
        ask = _d(quote["ask"])
    except Exception:
        return None
    if bid < 0 or ask <= 0 or ask < bid:
        return None
    return bid, ask, (bid + ask) / Decimal(2)


def entry_prices(long_quote: dict | None, short_quote: dict | None) -> tuple[Decimal, Decimal] | None:
    """Mid debit and natural debit (long ask minus short bid)."""
    long = _leg(long_quote)
    short = _leg(short_quote)
    if long is None or short is None:
        return None
    mid = long[2] - short[2]
    natural = long[1] - short[0]
    if mid <= 0 or natural <= 0:
        return None
    if natural < mid:
        natural = mid
    return _cent(mid), _cent(natural)


def mark_value(long_quote: dict | None, short_quote: dict | None) -> Decimal | None:
    long = _leg(long_quote)
    short = _leg(short_quote)
    if long is None or short is None:
        return None
    value = long[2] - short[2]
    return value if value > 0 else Decimal(0)


def close_prices(long_quote: dict | None, short_quote: dict | None) -> tuple[Decimal, Decimal] | None:
    """Credit we try to collect, then the natural credit we will step toward."""
    long = _leg(long_quote)
    short = _leg(short_quote)
    if long is None or short is None:
        return None
    mid = long[2] - short[2]
    natural = long[0] - short[1]
    if mid <= 0:
        mid = Decimal("0.01")
    if natural <= 0 or natural > mid:
        natural = Decimal("0.01") if natural <= 0 else natural
        if natural > mid:
            natural = mid
    return _cent(mid), _cent(natural)


def chase_price(start: Decimal, natural: Decimal, elapsed_seconds: float) -> Decimal | None:
    """Entry limit. None means the 5-minute window is over and the order is canceled."""
    if elapsed_seconds >= CHASE_SECONDS:
        return None
    start = _d(start)
    natural = _d(natural)
    steps = int(elapsed_seconds // STEP_SECONDS)
    price = start + STEP * Decimal(steps)
    if price > natural:
        price = natural
    if price <= 0:
        return None
    return _cent(price)


def exit_limit(start: Decimal, natural: Decimal, elapsed_seconds: float) -> Decimal:
    """Close credit. After 5 minutes the limit sits at the natural credit."""
    start = _d(start)
    natural = _d(natural)
    if natural <= 0:
        natural = Decimal("0.01")
    if elapsed_seconds >= CHASE_SECONDS:
        return _cent(natural)
    steps = int(elapsed_seconds // STEP_SECONDS)
    price = start - STEP * Decimal(steps)
    if price < natural:
        price = natural
    if price <= 0:
        price = natural
    return _cent(price)


def exit_reason(
    *,
    side: str,
    debit: Decimal,
    value: Decimal | None,
    closes: list[tuple[int, Decimal, Decimal]],
    minute: int,
) -> str | None:
    """TIME, TAKE_PROFIT, STOP_VALUE, VWAP, or None.

    `closes` is (end minute, close, session VWAP), oldest first.
    VWAP requires two consecutive 1-minute closes on the wrong side of VWAP.
    """
    if minute >= TIME_EXIT:
        return "TIME"
    debit = _d(debit)
    if value is not None and debit > 0:
        value = _d(value)
        if value >= debit * TAKE_PROFIT:
            return "TAKE_PROFIT"
        if value <= debit * STOP_VALUE:
            return "STOP_VALUE"
    if len(closes) >= 2:
        end_a, px_a, vw_a = closes[-2]
        end_b, px_b, vw_b = closes[-1]
        if int(end_b) - int(end_a) == 1:
            if side == "up" and px_a < vw_a and px_b < vw_b:
                return "VWAP"
            if side == "down" and px_a > vw_a and px_b > vw_b:
                return "VWAP"
    return None


def vwap_pairs(bars: list[Bar], minute: int) -> list[tuple[int, Decimal, Decimal]]:
    """Completed session bars at or before `minute`, with VWAP through that bar."""
    pairs: list[tuple[int, Decimal, Decimal]] = []
    eligible = [bar for bar in bars if bar.end_minute() <= minute]
    for bar in eligible:
        vwap = vwap_through(eligible, bar.end_minute())
        if vwap is None:
            continue
        pairs.append((bar.end_minute(), bar.close, vwap))
    return pairs


def spread_pnl(debit: Decimal, credit: Decimal, qty: int) -> Decimal:
    return (_d(credit) - _d(debit)) * Decimal(100) * Decimal(int(qty))


def weekly_brake(week_pnl: Decimal, bankroll: Decimal) -> bool:
    bankroll = _d(bankroll)
    if bankroll <= 0:
        return False
    return _d(week_pnl) <= -(bankroll * WEEKLY_LOSS)


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def next_monday(day: date) -> date:
    return day + timedelta(days=(7 - day.weekday()) or 7)


def entry_order(long_symbol: str, short_symbol: str, qty: int, limit: Decimal, client_order_id: str) -> dict:
    return {
        "order_class": "mleg",
        "qty": str(int(qty)),
        "type": "limit",
        "limit_price": f"{_cent(_d(limit))}",
        "time_in_force": "day",
        "client_order_id": client_order_id,
        "legs": [
            {"symbol": long_symbol, "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_open"},
            {"symbol": short_symbol, "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_open"},
        ],
    }


def exit_order(long_symbol: str, short_symbol: str, qty: int, credit: Decimal, client_order_id: str) -> dict:
    return {
        "order_class": "mleg",
        "qty": str(int(qty)),
        "type": "limit",
        "limit_price": f"-{_cent(_d(credit))}",
        "time_in_force": "day",
        "client_order_id": client_order_id,
        "legs": [
            {"symbol": long_symbol, "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_close"},
            {"symbol": short_symbol, "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_close"},
        ],
    }
