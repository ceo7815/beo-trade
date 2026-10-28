"""Desk copy for the day-protection ladder.

Reads the configured steps and the stored peak. Does not arm a floor, move a
floor, or sell. The lock itself still lives in day_floor.advance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config.settings import TradingConfig


def protection_progress(
    pnl: Decimal | None,
    peak: Decimal | None,
    steps: tuple,
    kill: Decimal,
    *,
    armed: bool,
) -> dict:
    """Next ladder step above the locked peak, and the distance from today's P&L.

    With nothing locked, the active protection is the dollar kill. A missing
    P&L returns status unknown and does not invent a zero.
    """
    ladder = _steps(steps)
    kill_amount = abs(Decimal(kill))
    if pnl is None:
        return _unknown(kill_amount)
    locked_floor = _locked_floor(peak, ladder) if armed else None
    next_arm, next_floor = _next_step(peak if armed else None, ladder)
    distance = None if next_arm is None else next_arm - pnl
    if distance is not None and distance < 0:
        distance = Decimal("0")
    kind = "floor" if locked_floor is not None else "kill"
    view = {
        "status": "ok",
        "line": _line(kind, locked_floor, next_arm, next_floor, distance),
        "kill": _money(kill_amount),
        "pnl": _money(pnl),
        "floor": None if locked_floor is None else _money(locked_floor),
        "peak": None if peak is None else _money(peak),
        "next_threshold": None if next_arm is None else _money(next_arm),
        "next_floor": None if next_floor is None else _money(next_floor),
        "distance": None if distance is None else _money(distance),
        "protection_kind": kind,
        "armed": bool(armed and locked_floor is not None),
    }
    return view


def build_protection(settings, pnl: Decimal | None, latch: dict | None, *, unknown: bool) -> dict:
    trading = settings.trading()
    kill = Decimal(str(trading.daily_kill_loss_usd or 0))
    if unknown or pnl is None:
        return _unknown(abs(kill) if kill > 0 else Decimal("0"))
    row = latch if isinstance(latch, dict) else {}
    armed = bool(row.get("armed"))
    peak = _stored_peak(settings, datetime.now(timezone.utc)) if armed else None
    if armed and peak is None:
        peak = _peak_for_floor(_steps(trading.day_floor_steps), _decimal(row.get("floor")))
    return protection_progress(pnl, peak, trading.day_floor_steps, kill, armed=armed)


def _unknown(kill: Decimal) -> dict:
    return {
        "status": "unknown",
        "line": "לא ידוע",
        "kill": _money(kill) if kill > 0 else None,
        "pnl": None,
        "floor": None,
        "peak": None,
        "next_threshold": None,
        "next_floor": None,
        "distance": None,
        "protection_kind": None,
        "armed": False,
    }


def _line(kind: str, floor: Decimal | None, next_arm: Decimal | None, next_floor: Decimal | None, distance: Decimal | None) -> str:
    if next_arm is None or next_floor is None or distance is None:
        if floor is None:
            return "לא ידוע"
        return f"ההגנה נעולה על {_words(floor)}"
    if kind == "kill":
        return f"עוד {_words(distance)} (ב-{_words(next_arm)}) נכנסת הגנה של {_words(next_floor)}"
    return f"עוד {_words(distance)} (ב-{_words(next_arm)}) ההגנה עולה ל-{_words(next_floor)}"


def _locked_floor(peak: Decimal | None, ladder: tuple[tuple[Decimal, Decimal], ...]) -> Decimal | None:
    found = None
    if peak is None:
        return None
    for arm, floor in ladder:
        if peak >= arm:
            found = floor
    return found


def _next_step(peak: Decimal | None, ladder: tuple[tuple[Decimal, Decimal], ...]) -> tuple[Decimal | None, Decimal | None]:
    mark = peak if peak is not None else Decimal("-1")
    for arm, floor in ladder:
        if arm > mark:
            return arm, floor
    return None, None


def _peak_for_floor(ladder: tuple[tuple[Decimal, Decimal], ...], floor: Decimal | None) -> Decimal | None:
    if floor is None:
        return None
    found = None
    for arm, lock in ladder:
        if lock == floor:
            found = arm
    return found


def _steps(steps: tuple) -> tuple[tuple[Decimal, Decimal], ...]:
    parsed = [(Decimal(str(arm)), Decimal(str(floor))) for arm, floor in (steps or ())]
    parsed.sort(key=lambda item: item[0])
    return tuple(parsed)


def _stored_peak(settings, moment: datetime) -> Decimal | None:
    from app.options.day_floor import store_path

    path = store_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    session_date = moment.astimezone(ZoneInfo(settings.exchange_timezone)).date().isoformat()
    if raw.get("session_date") != session_date:
        return None
    return _decimal(raw.get("peak"))


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _money(value: Decimal) -> str:
    quantized = Decimal(value).quantize(Decimal("0.01"))
    return f"{quantized:.2f}"


def _words(value: Decimal) -> str:
    quantized = Decimal(value).quantize(Decimal("0.01"))
    absolute = abs(quantized)
    if absolute == absolute.to_integral_value():
        text = f"{int(absolute):,}"
    else:
        text = f"{absolute:,.2f}"
    return f"{text}$"


def preview_at(pnl: Decimal, trading: TradingConfig | None = None) -> dict:
    """What the ladder shows if today's mark and the locked peak are both `pnl`."""
    config = trading or TradingConfig()
    armed = False
    peak = None
    ladder = _steps(config.day_floor_steps)
    if _locked_floor(pnl, ladder) is not None:
        armed = True
        peak = pnl
    return protection_progress(pnl, peak, config.day_floor_steps, Decimal(str(config.daily_kill_loss_usd)), armed=armed)
