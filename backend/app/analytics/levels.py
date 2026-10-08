"""Live exit levels for an open position, mirroring the exit engine's order of checks."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.config.settings import TradingConfig


def _dec(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _when(value: object) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(Decimal("0.01")))


def position_levels(trade: dict, state, config: TradingConfig, now: datetime, session_day: date) -> dict:
    """Stored levels win over recomputed ones; the recomputation only fills a missing record."""
    entry = _dec(trade.get("entry_price"))
    if entry is None or entry <= 0:
        return {"available": False}
    qty = _dec(trade.get("qty_open") or trade.get("qty")) or Decimal("0")
    multiplier = Decimal(config.contract_multiplier)
    one_r = entry * Decimal(str(config.initial_stop_decline_pct))
    stop = entry - one_r
    protect_at = entry + one_r * Decimal(str(config.protect_at_r))
    protected_stop = entry - one_r * Decimal(str(config.protective_stop_r))
    trail_on = entry + one_r * Decimal(str(config.trail_activate_r))
    current = _dec(trade.get("current_price"))
    peak = max(entry, current) if current is not None else entry
    protected = trailing = False
    started = _when(trade.get("opened_at"))
    if state is not None:
        stored_peak = _dec(state.peak_option_price)
        if stored_peak is not None:
            peak = max(peak, stored_peak)
        protected = bool(state.protected_mode)
        trailing = bool(state.trailing_active)
        started = _when(state.entry_time) or started
    protected = protected or peak - entry >= one_r * Decimal(str(config.protect_at_r))
    trailing = config.trailing_enabled and (trailing or peak - entry >= one_r * Decimal(str(config.trail_activate_r)))
    trail_trigger = peak * (Decimal("1") - Decimal(str(config.trailing_pct))) if trailing else None
    if trailing and trail_trigger is not None:
        active, stage = max(trail_trigger, protected_stop), "TRAILING"
    elif protected:
        active, stage = protected_stop, "PROTECTED"
    else:
        active, stage = stop, "AT_RISK"
    expiration = None
    try:
        expiration = date.fromisoformat(str(trade.get("expiration") or ""))
    except ValueError:
        expiration = None
    dte = None if expiration is None else (expiration - session_day).days
    limit = None
    if dte is not None:
        limit = config.holding_minutes_0dte if dte <= 0 else config.holding_minutes_1dte if dte == 1 else None
    time_exit = None if limit is None or started is None else started + timedelta(minutes=limit)
    locked = (active - entry) * qty * multiplier
    return {
        "available": True,
        "stage": stage,
        "entry": _text(entry),
        "current": _text(current),
        "stop": _text(stop),
        "protect_at": _text(protect_at),
        "protected_stop": _text(protected_stop),
        "trail_on": _text(trail_on),
        "trailing_pct": config.trailing_pct,
        "peak": _text(peak),
        "trail_trigger": _text(trail_trigger),
        "active_exit": _text(active),
        "pnl_at_active_exit": _text(locked),
        "dte": dte,
        "time_limit_minutes": limit,
        "time_exit_at": None if time_exit is None else time_exit.isoformat(),
        "time_exit_passed": bool(time_exit is not None and now >= time_exit),
        "close_exit_minutes": config.exit_before_expiration_minutes if dte is not None and dte <= 1 else None,
        "from_record": state is not None,
    }
