"""Friday put-credit weekend mode. Pure rules. Defined risk only.

Sell the put whose delta is closest to -0.20 and buy the put $5 below it.
Both legs are one limit order. A naked short is not representable here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

UNDERLYINGS = ("SPY", "QQQ")
WIDTH = Decimal("5")
TARGET_DELTA = Decimal("-0.20")
ENTRY_MINUTE = 15 * 60 + 45
CANCEL_MINUTE = 15 * 60 + 55
GUARD_MINUTE = 10 * 60
FLAT_MINUTE = 15 * 60 + 45
REPRICE_SECONDS = 60


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def session_facts(day: date, rows: list[dict] | None) -> tuple[date | None, bool, bool]:
    """Return next session, whether `day` is a half day, and whether `day` is listed."""
    nxt: date | None = None
    listed = False
    half = False
    for row in rows or []:
        try:
            stamp = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        if stamp == day:
            listed = True
            close = str(row.get("close") or "16:00")
            half = close < "16:00"
        elif stamp > day and (nxt is None or stamp < nxt):
            nxt = stamp
    return nxt, half, listed


def weekend_entry_reason(day: date, next_session: date | None, half_day: bool, listed: bool) -> str | None:
    """None means this is the last session of the week and the gap is short enough to enter.

    A normal Friday into Monday is three calendar days and is allowed.
    A gap longer than three days (Monday holiday, for example) is skipped.
    A half day is skipped. A day that still has a later session this week is skipped.
    """
    if not listed:
        return "closed"
    if half_day:
        return "half_day"
    if next_session is None:
        return "no_calendar"
    gap = (next_session - day).days
    if gap <= 0:
        return "no_next"
    if next_session.isocalendar()[:2] == day.isocalendar()[:2]:
        return "not_last_day"
    if gap > 3:
        return "gap"
    return None


def pick_short_put(candidates: list[tuple[Decimal, Decimal]], target: Decimal = TARGET_DELTA) -> Decimal | None:
    """Strike whose delta is closest to -0.20. Ties take the lower strike."""
    usable = [( _d(strike), _d(delta)) for strike, delta in candidates if delta is not None]
    if not usable:
        return None
    strike, _delta = min(usable, key=lambda item: (abs(item[1] - target), item[0]))
    return strike


def long_put_strike(short: Decimal, strikes: list[Decimal], width: Decimal = WIDTH) -> Decimal | None:
    wing = _d(short) - _d(width)
    pool = {_d(item) for item in strikes}
    if wing not in pool:
        return None
    return wing


def strike_window(spot: Decimal, strikes: list[Decimal]) -> list[Decimal]:
    """A handful of listed puts around the short strike. Not the whole chain."""
    spot = _d(spot)
    lo = spot * Decimal("0.94")
    hi = spot * Decimal("1.005")
    return sorted(strike for strike in {_d(item) for item in strikes} if lo <= strike <= hi)


def credit_prices(short_quote: dict | None, long_quote: dict | None) -> tuple[Decimal, Decimal] | None:
    """Mid credit, then the natural credit (short bid minus long ask)."""
    short = _leg(short_quote)
    long = _leg(long_quote)
    if short is None or long is None:
        return None
    mid = short[2] - long[2]
    natural = short[0] - long[1]
    if mid <= 0 or natural <= 0:
        return None
    if natural > mid:
        natural = mid
    return _cent(mid), _cent(natural)


def weekend_limit(mid: Decimal, natural: Decimal, elapsed_seconds: float) -> Decimal | None:
    """One reprice to the natural credit after 60s. None means cancel (15:55, ten minutes)."""
    if elapsed_seconds >= 600:
        return None
    if elapsed_seconds >= REPRICE_SECONDS:
        return _cent(_d(natural))
    return _cent(_d(mid))


def contract_loss(width: Decimal, credit: Decimal) -> Decimal:
    return (_d(width) - _d(credit)) * Decimal(100)


def weekend_contracts(equity: Decimal, credit: Decimal, width: Decimal = WIDTH, max_loss_pct: Decimal = Decimal("0.16"), slots: int = 2) -> int:
    """Whole contracts whose max loss fits in this underlying's half of the book.

    One contract that costs more than its half is skipped (returns 0).
    """
    equity = _d(equity)
    credit = _d(credit)
    width = _d(width)
    max_loss_pct = _d(max_loss_pct)
    if equity <= 0 or credit <= 0 or credit >= width or max_loss_pct <= 0 or slots < 1:
        return 0
    budget = equity * max_loss_pct / Decimal(slots)
    loss = contract_loss(width, credit)
    if loss <= 0 or loss > budget:
        return 0
    return int((budget / loss).to_integral_value(rounding=ROUND_DOWN))


def hold_action(
    day: date,
    expiry: date,
    minute: int,
    spot: Decimal | None,
    short_strike: Decimal,
    credit: Decimal,
) -> str | None:
    """On expiry day: close at 10:00 when the short put is ITM by more than the credit.

    Otherwise close at 15:45. Before 10:00, do nothing. Never hold through the close.
    """
    if day != expiry:
        return None
    if minute >= FLAT_MINUTE:
        return "FLAT"
    if minute >= GUARD_MINUTE and spot is not None and (_d(short_strike) - _d(spot)) > _d(credit):
        return "ASSIGNMENT"
    return None


def close_cost(long_quote: dict | None, short_quote: dict | None) -> tuple[Decimal, Decimal] | None:
    """Debit to close a put credit spread: buy back the short put, sell the long put."""
    owned = _leg(long_quote)
    sold = _leg(short_quote)
    if owned is None or sold is None:
        return None
    mid = sold[2] - owned[2]
    natural = sold[1] - owned[0]
    if mid <= 0:
        mid = Decimal("0.01")
    if natural < mid or natural <= 0:
        natural = mid
    return _cent(mid), _cent(natural)


def credit_put_order(short_symbol: str, long_symbol: str, qty: int, credit: Decimal, client_order_id: str) -> dict:
    return {
        "order_class": "mleg",
        "qty": str(int(qty)),
        "type": "limit",
        "limit_price": f"-{_cent(_d(credit))}",
        "time_in_force": "day",
        "client_order_id": client_order_id,
        "legs": [
            {"symbol": short_symbol, "ratio_qty": "1", "side": "sell", "position_intent": "sell_to_open"},
            {"symbol": long_symbol, "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_open"},
        ],
    }


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
