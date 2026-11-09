"""Candidate entry filters evaluated for the log only.

Nothing in this module is read by admission, pacing, sizing, or exits.
A would_block flag is a counterfactual. The order path must not branch on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

# Primary stack_confirm gate. Other (X, T) pairs can be rebuilt from the raw inputs.
STACK_CONFIRM_MAX_AGE_MINUTES = 10
STACK_CONFIRM_MAX_PNL_PCT = Decimal("0")
# Option P&L percents are fractions: 0 is flat, -0.12 is a 12% loss.
STACK_WINDOW_MINUTES = 30
STACK_WINDOW_MIN_ENTRIES = 3
DAY_LOSS_PAUSE_AT = Decimal("0.05")
DAY_LOSS_RESUME_BELOW = Decimal("0.035")
SETUP_MIN_RATE = Decimal("0.45")
SETUP_MIN_SAMPLES = 20
SETUP_TIME_TOLERANCE_MINUTES = 10
SETUP_HORIZON_MINUTES = 30
SETUP_CONTINUATION = Decimal("0.01")
SETUP_NOT_AVAILABLE = "not_available"

RULES = (
    "stack_confirm",
    "stack_window",
    "open_loser_same_dir",
    "day_loss_pause",
    "setup_history",
)


@dataclass(frozen=True)
class OpenLeg:
    """One open option. pnl_pct is the option premium change as a fraction."""

    side: str
    symbol: str = ""
    opened_at: datetime | None = None
    pnl_pct: Decimal | None = None
    pnl_dollars: Decimal | None = None


@dataclass(frozen=True)
class RecentEntry:
    """A same-session buy already submitted. This candidate is not included."""

    at: datetime
    side: str
    symbol: str = ""
    pnl_pct: Decimal | None = None


@dataclass(frozen=True)
class SessionPath:
    """One prior session of underlying prices, in minutes since the New York open."""

    open_price: Decimal
    points: tuple[tuple[float, Decimal], ...]


@dataclass
class ShadowView:
    side: str
    moment: datetime
    open_legs: list[OpenLeg] = field(default_factory=list)
    recent_entries: list[RecentEntry] = field(default_factory=list)
    # Signed day P&L / session-open equity. Negative is a loss. None when the mark is missing.
    day_pnl_pct: Decimal | None = None
    loss_pct: Decimal | None = None
    day_pnl: Decimal | None = None
    open_equity: Decimal | None = None
    paused: bool = False
    same_pause_session: bool = True
    # None means no cheap history. An empty list means history was read and nothing qualified.
    setup_samples: list[bool] | None = None
    setup_detail: dict = field(default_factory=dict)


def evaluate_all(view: ShadowView) -> tuple[list[dict], bool]:
    """Every shadow rule, plus the pause flag to store for the next evaluation."""
    paused = bool(view.paused) if view.same_pause_session else False
    decisions = [
        stack_confirm(view),
        stack_window(view),
        open_loser_same_dir(view),
        day_loss_pause(view, paused),
        setup_history(view),
    ]
    pause_rule = decisions[3]
    return decisions, bool(pause_rule["inputs"].get("paused_after"))


def stack_confirm(view: ShadowView, *, max_age_minutes: int = STACK_CONFIRM_MAX_AGE_MINUTES, max_pnl_pct: Decimal = STACK_CONFIRM_MAX_PNL_PCT) -> dict:
    """Block a new same-side buy when a young open position is still under X.

    The primary pair is X = 0% and T = 10 minutes. The inputs keep the raw age,
    the last position's P&L, the open count, and the combined dollars so another
    pair can be scored later without another pass through the book.
    """
    side = _side(view.side)
    same = [leg for leg in view.open_legs if _side(leg.side) == side]
    dated = [leg for leg in same if leg.opened_at is not None]
    last = max(dated, key=lambda leg: leg.opened_at) if dated else None
    minutes_since = None if last is None or last.opened_at is None else _minutes_between(last.opened_at, view.moment)
    dollars = [leg.pnl_dollars for leg in same if leg.pnl_dollars is not None]
    combined = sum(dollars, Decimal("0")) if dollars else None
    young = []
    for leg in same:
        if leg.opened_at is None or leg.pnl_pct is None:
            continue
        age = _minutes_between(leg.opened_at, view.moment)
        if age is None or age < 0 or age >= max_age_minutes:
            continue
        if leg.pnl_pct < max_pnl_pct:
            young.append(leg.symbol or side)
    return _decision(
        "stack_confirm",
        bool(young),
        "young_open_below_threshold" if young else "pass",
        {
            "max_pnl_pct": _text(max_pnl_pct),
            "max_age_minutes": max_age_minutes,
            "minutes_since_last_same_direction_entry": None if minutes_since is None else _text(minutes_since),
            "last_same_direction_pnl_pct": None if last is None else _text(last.pnl_pct),
            "last_same_direction_symbol": "" if last is None else last.symbol,
            "open_same_direction_count": len(same),
            "open_same_direction_pnl_dollars": _text(combined),
            "open_same_direction_pnl_complete": bool(same) and len(dollars) == len(same),
            "young_symbols": young,
        },
    )


def stack_window(view: ShadowView, *, window_minutes: int = STACK_WINDOW_MINUTES, min_entries: int = STACK_WINDOW_MIN_ENTRIES) -> dict:
    """Block when this buy would be the third-or-later same-side entry in 30 minutes and the first is red.

    Flat (0%) counts as green. A missing P&L on the first entry does not invent a block.
    """
    side = _side(view.side)
    window = [
        item
        for item in view.recent_entries
        if _side(item.side) == side and _inside(item.at, view.moment, window_minutes)
    ]
    window.sort(key=lambda item: item.at)
    prospective = len(window) + 1
    first = window[0] if window else None
    reason = "below_count"
    block = False
    if prospective >= min_entries:
        if first is None or first.pnl_pct is None:
            reason = "first_pnl_unknown"
        elif first.pnl_pct < 0:
            block = True
            reason = "first_not_green"
        else:
            reason = "first_green"
    return _decision(
        "stack_window",
        block,
        reason,
        {
            "window_minutes": window_minutes,
            "min_entries": min_entries,
            "prior_same_direction_count": len(window),
            "prospective_count": prospective,
            "first_entry_at": None if first is None else first.at.isoformat(),
            "first_symbol": "" if first is None else first.symbol,
            "first_pnl_pct": None if first is None else _text(first.pnl_pct),
        },
    )


def open_loser_same_dir(view: ShadowView) -> dict:
    """Block when any open same-side position is currently under water."""
    side = _side(view.side)
    same = [leg for leg in view.open_legs if _side(leg.side) == side]
    losers = [leg for leg in same if leg.pnl_pct is not None and leg.pnl_pct < 0]
    unknown = [leg.symbol for leg in same if leg.pnl_pct is None]
    worst = min((leg.pnl_pct for leg in losers), default=None)
    reason = "open_loser" if losers else ("pnl_unknown" if unknown and not same else "pass")
    if unknown and not losers and same:
        reason = "pnl_unknown"
    return _decision(
        "open_loser_same_dir",
        bool(losers),
        reason,
        {
            "open_same_direction_count": len(same),
            "open_loser_count": len(losers),
            "unknown_pnl_count": len(unknown),
            "worst_pnl_pct": _text(worst),
        },
    )


def day_loss_pause(view: ShadowView, paused: bool) -> dict:
    """Pause new buys once the day loss reaches 5% of session-open equity. Resume under 3.5%.

    The percent uses the same base as the daily kill (session-open equity). The pause
    flag lives only in this shadow state. A missing mark does not start a pause.
    """
    if not view.same_pause_session:
        paused = False
    loss = view.loss_pct
    if loss is None:
        return _decision(
            "day_loss_pause",
            False,
            SETUP_NOT_AVAILABLE,
            {
                "day_pnl_pct": _text(view.day_pnl_pct),
                "loss_pct": None,
                "day_pnl": _text(view.day_pnl),
                "open_equity": _text(view.open_equity),
                "pause_at": _text(DAY_LOSS_PAUSE_AT),
                "resume_below": _text(DAY_LOSS_RESUME_BELOW),
                "paused_before": paused,
                "paused_after": paused,
            },
        )
    if not paused and loss >= DAY_LOSS_PAUSE_AT:
        now_paused = True
        reason = "pause_at_or_above_5pct"
    elif paused and loss < DAY_LOSS_RESUME_BELOW:
        now_paused = False
        reason = "resume_below_3_5pct"
    elif paused:
        now_paused = True
        reason = "still_paused"
    else:
        now_paused = False
        reason = "below_pause"
    return _decision(
        "day_loss_pause",
        now_paused,
        reason,
        {
            "day_pnl_pct": _text(view.day_pnl_pct),
            "loss_pct": _text(loss),
            "day_pnl": _text(view.day_pnl),
            "open_equity": _text(view.open_equity),
            "pause_at": _text(DAY_LOSS_PAUSE_AT),
            "resume_below": _text(DAY_LOSS_RESUME_BELOW),
            "paused_before": paused,
            "paused_after": now_paused,
        },
    )


def setup_history(view: ShadowView, *, min_samples: int = SETUP_MIN_SAMPLES, min_rate: Decimal = SETUP_MIN_RATE) -> dict:
    """Block when the historical continuation rate is under 45% with enough analogs.

    Samples are True when a +1% move in the trade direction printed before a -1%
    reversal inside 30 minutes. None means no cheap prior-session history.
    """
    detail = dict(view.setup_detail or {})
    samples = view.setup_samples
    if samples is None:
        return _decision(
            "setup_history",
            False,
            SETUP_NOT_AVAILABLE,
            {
                "rate": None,
                "samples": 0,
                "successes": 0,
                "min_samples": min_samples,
                "min_rate": _text(min_rate),
                "source": detail.get("source") or SETUP_NOT_AVAILABLE,
                "todo": detail.get("todo") or _SETUP_TODO,
                **{key: detail[key] for key in ("move_bucket", "minutes_since_open", "skipped_sparse") if key in detail},
            },
        )
    counted = len(samples)
    successes = sum(1 for item in samples if item)
    rate = None if counted == 0 else Decimal(successes) / Decimal(counted)
    if counted < min_samples:
        block = False
        reason = "insufficient_samples"
    elif rate is not None and rate < min_rate:
        block = True
        reason = "rate_below_45pct"
    else:
        block = False
        reason = "rate_ok"
    return _decision(
        "setup_history",
        block,
        reason,
        {
            "rate": _text(rate),
            "samples": counted,
            "successes": successes,
            "min_samples": min_samples,
            "min_rate": _text(min_rate),
            "source": detail.get("source") or "provided",
            "move_bucket": detail.get("move_bucket"),
            "minutes_since_open": detail.get("minutes_since_open"),
            "skipped_sparse": detail.get("skipped_sparse", 0),
            "tolerance_minutes": SETUP_TIME_TOLERANCE_MINUTES,
            "horizon_minutes": SETUP_HORIZON_MINUTES,
            "continuation": _text(SETUP_CONTINUATION),
        },
    )


def move_bucket(move: Decimal | None) -> str | None:
    """Signed move from the session open, in the same bands the desk already ranks on."""
    if move is None:
        return None
    magnitude = abs(move)
    if magnitude < Decimal("0.006"):
        return "flat"
    if magnitude < Decimal("0.02"):
        band = "small"
    elif magnitude < Decimal("0.04"):
        band = "mid"
    else:
        band = "large"
    sign = "up" if move > 0 else "down"
    return f"{sign}_{band}"


def continuation_hit(reference: Decimal, path: list[tuple[float, Decimal]], side: str, *, move: Decimal = SETUP_CONTINUATION) -> bool | None:
    """True when a 1% continuation prints before a 1% reversal. None when the path is empty.

    `path` is (minutes after the anchor, price), already inside the horizon.
    One price cannot be both +1% and -1%, so the first print that reaches either level decides.
    """
    if reference <= 0 or not path:
        return None
    favorable = reference * (Decimal("1") + move) if _side(side) == "CALL" else reference * (Decimal("1") - move)
    adverse = reference * (Decimal("1") - move) if _side(side) == "CALL" else reference * (Decimal("1") + move)
    for _minute, price in sorted(path, key=lambda item: item[0]):
        if price <= 0:
            continue
        if _side(side) == "CALL":
            if price >= favorable:
                return True
            if price <= adverse:
                return False
        else:
            if price <= favorable:
                return True
            if price >= adverse:
                return False
    # The window was observed and neither level printed. That is not a continuation.
    return False


def analogs_from_sessions(
    sessions: list[SessionPath],
    *,
    minutes_since_open: float,
    side: str,
    move_from_open: Decimal | None,
    tolerance_minutes: int = SETUP_TIME_TOLERANCE_MINUTES,
    horizon_minutes: int = SETUP_HORIZON_MINUTES,
) -> tuple[list[bool], dict]:
    """Prior sessions at the same minute-of-day and the same move bucket."""
    bucket = move_bucket(move_from_open)
    kept: list[bool] = []
    skipped_sparse = 0
    considered = 0
    for session in sessions:
        if session.open_price <= 0 or not session.points:
            continue
        anchor = _nearest(session.points, minutes_since_open, tolerance_minutes)
        if anchor is None:
            continue
        anchor_minute, anchor_price = anchor
        if anchor_price <= 0:
            continue
        change = (anchor_price - session.open_price) / session.open_price
        analog_bucket = move_bucket(change)
        if not _bucket_matches(side, bucket, analog_bucket):
            continue
        considered += 1
        forward = [
            (minute - anchor_minute, price)
            for minute, price in session.points
            if anchor_minute < minute <= anchor_minute + horizon_minutes
        ]
        hit = continuation_hit(anchor_price, forward, side)
        if hit is None:
            skipped_sparse += 1
            continue
        kept.append(hit)
    return kept, {
        "move_bucket": bucket,
        "minutes_since_open": minutes_since_open,
        "skipped_sparse": skipped_sparse,
        "sessions_matched": considered,
        "source": "market_snapshots",
    }


def loss_fraction(day_pnl: Decimal | None, open_equity: Decimal | None) -> tuple[Decimal | None, Decimal | None]:
    """Signed P&L percent and the loss percent (0 when the day is green). Both are fractions."""
    if day_pnl is None or open_equity is None or open_equity <= 0:
        return None, None
    signed = day_pnl / open_equity
    loss = -signed if signed < 0 else Decimal("0")
    return signed, loss


def _bucket_matches(side: str, candidate: str | None, analog: str | None) -> bool:
    if candidate is None or analog is None or candidate != analog:
        return False
    inferred = None
    if analog.startswith("up_"):
        inferred = "CALL"
    elif analog.startswith("down_"):
        inferred = "PUT"
    if inferred is None:
        return True
    return inferred == _side(side)


def _nearest(points: tuple[tuple[float, Decimal], ...], target: float, tolerance: int) -> tuple[float, Decimal] | None:
    best = None
    best_gap = None
    for minute, price in points:
        gap = abs(minute - target)
        if gap > tolerance:
            continue
        if best_gap is None or gap < best_gap:
            best = (minute, price)
            best_gap = gap
    return best


def _inside(at: datetime, moment: datetime, minutes: int) -> bool:
    if minutes <= 0:
        return False
    age = _minutes_between(at, moment)
    return age is not None and 0 <= age < minutes


def _minutes_between(start: datetime, end: datetime) -> Decimal | None:
    try:
        return Decimal(str((end - start).total_seconds())) / Decimal("60")
    except Exception:
        return None


def _side(value: str) -> str:
    text = str(value or "").upper()
    if text in {"C", "CALL"}:
        return "CALL"
    if text in {"P", "PUT"}:
        return "PUT"
    return text


def _decision(rule: str, would_block: bool, reason: str, inputs: dict) -> dict:
    return {"rule": rule, "would_block": bool(would_block), "reason": reason, "inputs": inputs}


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


_SETUP_TODO = (
    "TODO: no cheap prior-session minute history for this underlying. "
    "market_snapshots is the only local series; do not add a paid history feed for this rule."
)

