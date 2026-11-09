"""Spec v5 rules for Zarattini, Aziz & Barbon (2024), as written in the spec.

Where the published paper checks the band more often, the spec wins: decisions
only at HH:00 and HH:30 from 10:00 to 15:30 America/New_York. Live trading is
long-only and 1x. Short math is here so tests can turn it on; the default
config leaves it off. Parameters are constants, not runtime knobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from app.spy.clock import NY, is_decision_minute, rth_start

LOOKBACK = 14
VOL_TARGET = Decimal("0.02")
DAILY_LOSS = Decimal("0.02")
DRAWDOWN = Decimal("0.10")
MAX_ENTRIES = 4
# US Pattern Day Trader equity line. This is not the account's equity.
PDT_EQUITY_LINE = Decimal("25000")
PDT_MAX_DAY_TRADES = 3


@dataclass(frozen=True)
class Bar:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def end_minute(self) -> int:
        end = self.start.astimezone(NY) + timedelta(minutes=1)
        return end.hour * 60 + end.minute

    def session_date(self) -> date:
        return self.start.astimezone(NY).date()


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def rth_sessions(bars: list[Bar]) -> dict[date, list[Bar]]:
    grouped: dict[date, list[Bar]] = {}
    for bar in bars:
        if bar.start.tzinfo is None or not rth_start(bar.start):
            continue
        grouped.setdefault(bar.session_date(), []).append(bar)
    for day in grouped:
        grouped[day].sort(key=lambda item: item.start)
    return grouped


def session_open(bars: list[Bar]) -> Decimal | None:
    for bar in bars:
        local = bar.start.astimezone(NY)
        if local.hour == 9 and local.minute == 30:
            return bar.open
    return None


def close_at(bars: list[Bar], minute_end: int) -> Decimal | None:
    for bar in bars:
        if bar.end_minute() == minute_end:
            return bar.close
    return None


def prior_sessions(sessions: dict[date, list[Bar]], day: date) -> list[list[Bar]]:
    """Completed sessions strictly before `day`, oldest first. Today is excluded."""
    return [sessions[key] for key in sorted(sessions) if key < day]


def sigma_at(prior: list[list[Bar]], minute_end: int, lookback: int = LOOKBACK) -> Decimal | None:
    """Mean absolute open-to-minute move over the last `lookback` sessions that have it.

    The caller must pass sessions before the decision day. Today's close is not
    an input. A later minute on a prior day is not an input for this minute.
    """
    moves: list[Decimal] = []
    for bars in prior:
        opened = session_open(bars)
        price = close_at(bars, minute_end)
        if opened is None or price is None or opened <= 0:
            continue
        moves.append(abs(price / opened - Decimal(1)))
    if len(moves) < lookback:
        return None
    window = moves[-lookback:]
    return sum(window, Decimal(0)) / Decimal(lookback)


def bands(open_px: Decimal, prior_close: Decimal, sigma: Decimal) -> tuple[Decimal, Decimal]:
    """Gap-adjusted noise zone from spec v5."""
    upper = max(open_px, prior_close) * (Decimal(1) + sigma)
    lower = min(open_px, prior_close) * (Decimal(1) - sigma)
    return upper, lower


def vwap_through(bars: list[Bar], minute_end: int) -> Decimal | None:
    """Session VWAP using only bars whose end is at or before the decision minute.

    Typical price (H+L+C)/3, weighted by volume, anchored at 09:30.
    """
    numerator = Decimal(0)
    denominator = Decimal(0)
    for bar in bars:
        if bar.end_minute() > minute_end:
            continue
        typical = (bar.high + bar.low + bar.close) / Decimal(3)
        numerator += typical * bar.volume
        denominator += bar.volume
    if denominator <= 0:
        return None
    return numerator / denominator


def daily_closes(prior: list[list[Bar]]) -> list[Decimal]:
    closes: list[Decimal] = []
    for bars in prior:
        if bars:
            closes.append(bars[-1].close)
    return closes


def population_stdev(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values))
    return variance.sqrt()


def daily_vol(prior: list[list[Bar]], lookback: int = LOOKBACK) -> Decimal | None:
    """Population standard deviation of the last `lookback` close-to-close returns.

    Needs lookback+1 completed sessions before today.
    """
    closes = daily_closes(prior)
    if len(closes) < lookback + 1:
        return None
    window = closes[-(lookback + 1) :]
    returns = [window[index + 1] / window[index] - Decimal(1) for index in range(lookback) if window[index] > 0]
    if len(returns) < lookback:
        return None
    return population_stdev(returns)


def share_qty(equity: Decimal, price: Decimal, daily_volatility: Decimal) -> int:
    """Whole shares. Weight is min(1, 0.02 / vol) of *this* equity, never above 1x.

    Equity and price come from the caller. There is no hardcoded account size.
    """
    equity = _d(equity)
    price = _d(price)
    daily_volatility = _d(daily_volatility)
    if equity <= 0 or price <= 0 or daily_volatility <= 0:
        return 0
    weight = min(Decimal(1), VOL_TARGET / daily_volatility)
    notional = equity * weight
    if notional > equity:
        notional = equity
    return int((notional / price).to_integral_value(rounding=ROUND_DOWN))


def stop_level(side: str, upper: Decimal, lower: Decimal, vwap: Decimal | None) -> Decimal:
    if side == "short":
        return lower if vwap is None else min(lower, vwap)
    return upper if vwap is None else max(upper, vwap)


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    price: Decimal | None = None
    upper: Decimal | None = None
    lower: Decimal | None = None
    vwap: Decimal | None = None
    stop: Decimal | None = None
    sigma: Decimal | None = None
    qty: int = 0
    minute_end: int | None = None


def evaluate_position(
    *,
    price: Decimal,
    upper: Decimal,
    lower: Decimal,
    vwap: Decimal | None,
    side: str,
    allow_short: bool,
) -> Decision:
    """Entry or trailing stop at a decision minute. `side` is long, short, or flat."""
    if side == "long":
        level = stop_level("long", upper, lower, vwap)
        if price <= level:
            return Decision("EXIT", "TRAIL", price, upper, lower, vwap, level)
        return Decision("HOLD", "TRAIL", price, upper, lower, vwap, level)
    if side == "short":
        level = stop_level("short", upper, lower, vwap)
        if price >= level:
            return Decision("EXIT", "TRAIL", price, upper, lower, vwap, level)
        return Decision("HOLD", "TRAIL", price, upper, lower, vwap, level)
    if price > upper:
        return Decision("ENTER_LONG", "UPPER", price, upper, lower, vwap, upper)
    if price < lower:
        if allow_short:
            return Decision("ENTER_SHORT", "LOWER", price, upper, lower, vwap, lower)
        return Decision("FLAT", "LONG_ONLY", price, upper, lower, vwap, lower)
    return Decision("FLAT", "INSIDE", price, upper, lower, vwap, None)


def day_trades_left(
    *,
    equity: Decimal | None,
    daytrade_count: int | None,
    pattern_day_trader: bool,
    guard: bool,
) -> int | None:
    """Remaining same-day round trips before a 4th would violate PDT.

    None means the guard is off or the account is at/above the $25,000 line,
    so the count does not block. 0 means do not open a trade we must close today.
    """
    if not guard:
        return None
    if equity is None:
        return 0
    if equity >= PDT_EQUITY_LINE:
        return None
    if pattern_day_trader:
        return 0
    if daytrade_count is None:
        return 0
    return max(0, PDT_MAX_DAY_TRADES - int(daytrade_count))


def pdt_block_reason(
    *,
    equity: Decimal | None,
    daytrade_count: int | None,
    pattern_day_trader: bool,
    guard: bool,
) -> str | None:
    if not guard:
        return None
    if equity is not None and equity >= PDT_EQUITY_LINE:
        return None
    if equity is None or (daytrade_count is None and not pattern_day_trader):
        return "PDT_UNKNOWN"
    left = day_trades_left(
        equity=equity,
        daytrade_count=daytrade_count,
        pattern_day_trader=pattern_day_trader,
        guard=True,
    )
    if left == 0:
        return "PDT_LIMIT"
    return None


def is_pdt_rejection(payload: dict | None, status_code: int = 0) -> bool:
    body = payload or {}
    try:
        code = int(body.get("code"))
    except (TypeError, ValueError):
        code = None
    if code in {40310000, 40310100}:
        return True
    if status_code in {40310000, 40310100}:
        return True
    text = " ".join(str(body.get(key) or "") for key in ("message", "reject_reason", "detail")).lower()
    return "pattern day" in text or "day trading protection" in text or "day trade" in text


def should_retry_order(status: str) -> bool:
    """A PDT rejection is terminal. Transport failures may be retried."""
    return status not in {"pdt_rejected", "filled", "canceled", "expired", "rejected"}


def loss_hit(equity: Decimal | None, equity_start: Decimal | None) -> bool:
    if equity is None or equity_start is None or equity_start <= 0:
        return False
    return (equity - equity_start) / equity_start <= -DAILY_LOSS


def drawdown_hit(equity: Decimal | None, peak: Decimal | None) -> bool:
    if equity is None or peak is None or peak <= 0:
        return False
    return equity <= peak * (Decimal(1) - DRAWDOWN)


def slot_snapshot(
    sessions: dict[date, list[Bar]],
    day: date,
    minute_end: int,
) -> tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    """price, open, prior_close, sigma, upper, lower, vwap — vwap returned separately via the tuple.

    Returns (price, open, prior_close, sigma, upper, lower, vwap).
    """
    if not is_decision_minute(minute_end):
        return (None, None, None, None, None, None, None)
    today = sessions.get(day) or []
    prior = prior_sessions(sessions, day)
    opened = session_open(today)
    price = close_at(today, minute_end)
    yesterday = prior[-1] if prior else None
    previous_close = yesterday[-1].close if yesterday else None
    sigma = sigma_at(prior, minute_end)
    upper = lower = None
    if opened is not None and previous_close is not None and sigma is not None:
        upper, lower = bands(opened, previous_close, sigma)
    vwap = vwap_through(today, minute_end)
    return price, opened, previous_close, sigma, upper, lower, vwap


def series_for_day(sessions: dict[date, list[Bar]], day: date) -> list[dict]:
    """Chart rows for one session. Bounds use only prior days, so the line cannot look ahead."""
    today = sessions.get(day) or []
    prior = prior_sessions(sessions, day)
    opened = session_open(today)
    previous_close = prior[-1][-1].close if prior else None
    rows: list[dict] = []
    for bar in today:
        minute = bar.end_minute()
        sigma = sigma_at(prior, minute)
        upper = lower = None
        if opened is not None and previous_close is not None and sigma is not None:
            upper, lower = bands(opened, previous_close, sigma)
        vwap = vwap_through(today, minute)
        local = (bar.start.astimezone(NY) + timedelta(minutes=1)).strftime("%H:%M")
        rows.append(
            {
                "t": local,
                "price": _text(bar.close),
                "upper": _text(upper),
                "lower": _text(lower),
                "vwap": _text(vwap),
            }
        )
    return rows


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")

