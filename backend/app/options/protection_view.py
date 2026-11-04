"""Desk copy for the day-protection ladder.

Reads the configured steps and the stored peak. Does not arm a floor, move a
floor, or sell. The lock itself still lives in day_floor.advance.

Before the regular session opens, Alpaca equity − last_equity is still the
previous session. present_broker_day turns that into the number the screen
shows. The risk engine keeps the raw mark.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config.settings import TradingConfig

FRESH_DAY_NOTE = "יום חדש — מתאפס בפתיחה"


def display_moment() -> datetime:
    return datetime.now(timezone.utc)


def broker_mark_is_previous_session(moment: datetime, calendar) -> bool:
    """True when equity − last_equity still belongs to an earlier New York session.

    After today's open, and through the close, the mark is this session.
    Before the open, and on a day with no session, it is the previous one.
    """
    from app.core.sessions import session_bounds

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    bounds = session_bounds(moment, calendar)
    if bounds is None:
        return True
    start, _end = bounds
    local = moment.astimezone(ZoneInfo(calendar.timezone))
    return local < start


def present_broker_day(pnl: Decimal | None, moment: datetime, calendar) -> dict:
    """The day P&L the desk may show. A missing mark stays missing.

    A previous session is shown as zero with a fresh-day note, so the big
    number, the percent line, and the protection sentence share one figure.
    """
    if pnl is None:
        return {"pnl": None, "stale": False, "note": None}
    if broker_mark_is_previous_session(moment, calendar):
        return {"pnl": Decimal("0"), "stale": True, "note": FRESH_DAY_NOTE}
    return {"pnl": pnl, "stale": False, "note": None}


def protection_progress(
    pnl: Decimal | None,
    peak: Decimal | None,
    steps: tuple,
    kill: Decimal,
    *,
    armed: bool,
) -> dict:
    """Next ladder step the displayed P&L has not reached, and the distance to it.

    With nothing locked, the active protection is the dollar kill. A missing
    P&L returns status unknown and does not invent a zero. A P&L that is
    already at or past a step never produces "עוד 0$": the sentence moves to
    the following step, or stops once the ladder is capped.
    """
    ladder = _steps(steps)
    kill_amount = abs(Decimal(kill))
    if pnl is None:
        return _unknown(kill_amount)
    locked_floor = _locked_floor(peak, ladder) if armed else None
    reference = peak if locked_floor is not None and peak is not None else pnl
    next_arm, next_floor = _next_step(reference, ladder)
    if next_arm is not None and _reached(pnl, next_arm):
        next_arm, next_floor = _next_step(pnl, ladder)
    distance = None if next_arm is None else next_arm - pnl
    if distance is not None and _reached(pnl, next_arm):
        distance = None
        next_arm, next_floor = None, None
    kind = "floor" if locked_floor is not None else "kill"
    view = {
        "status": "ok",
        "line": _line(locked_floor, next_arm, next_floor, distance),
        "kill": _money(kill_amount),
        "pnl": _money(pnl),
        "floor": None if locked_floor is None else _money(locked_floor),
        "peak": None if peak is None else _money(peak),
        "next_threshold": None if next_arm is None else _money(next_arm),
        "next_floor": None if next_floor is None else _money(next_floor),
        "distance": None if distance is None else _money(distance),
        "protection_kind": kind,
        "armed": bool(locked_floor is not None),
    }
    return view


def build_protection(
    settings,
    pnl: Decimal | None,
    latch: dict | None,
    *,
    unknown: bool,
    open_equity: Decimal | None = None,
) -> dict:
    trading = settings.trading()
    from app.capital import day_giveback_enabled, day_trail_enabled, effective_kill_usd, ladder_basis_usd, scaled_day_floor_steps
    from app.capital.session_lock import read_locked_open_equity

    opened = open_equity if open_equity is not None else read_locked_open_equity()
    kill = effective_kill_usd(trading, open_equity=opened)
    if unknown or pnl is None:
        return _unknown(abs(kill) if kill > 0 else Decimal("0"))
    row = latch if isinstance(latch, dict) else {}
    if day_giveback_enabled(trading) or str(row.get("mode") or "") == "ladder_trail":
        steps = scaled_day_floor_steps(trading, ladder_basis_usd(trading, opened))
        armed = bool(row.get("armed"))
        peak = _decimal(row.get("peak")) or (_stored_peak(settings, datetime.now(timezone.utc)) if armed else None)
        view = protection_progress(pnl, peak, steps, kill, armed=armed)
        trail_floor = _decimal(row.get("trail_floor"))
        ladder_floor = _decimal(row.get("ladder_floor"))
        active = _decimal(row.get("floor")) if armed else None
        if active is not None:
            view["floor"] = _money(active)
            view["armed"] = True
            view["protection_kind"] = "floor"
        if trail_floor is not None and ladder_floor is not None and active is not None:
            binder = "טרייל 40%" if trail_floor >= ladder_floor else "מדרגה"
            view["line"] = f"{view['line']} · הגנה גבוהה יותר: {binder}"
        return view
    if day_trail_enabled(trading) or str(row.get("mode") or "") == "trail":
        return _trail_protection(pnl, row, kill)
    armed = bool(row.get("armed"))
    peak = _stored_peak(settings, datetime.now(timezone.utc)) if armed else None
    if armed and peak is None:
        peak = _peak_for_floor(_steps(trading.day_floor_steps), _decimal(row.get("floor")))
    return protection_progress(pnl, peak, trading.day_floor_steps, kill, armed=armed)


def _trail_protection(pnl: Decimal, row: dict, kill: Decimal) -> dict:
    peak = _decimal(row.get("peak"))
    floor = _decimal(row.get("floor"))
    arm = _decimal(row.get("arm"))
    armed = bool(row.get("armed")) and floor is not None
    distance = None
    if armed and floor is not None:
        distance = pnl - floor
    if not armed and arm is not None:
        distance = arm - pnl
        line = f"עוד {_words(distance)} (ב-{_words(arm)}) נכנסת הגנה של טרייל יומי"
    elif armed and floor is not None and peak is not None:
        line = f"מוגנים {_words(floor)} · שיא {_words(peak)} · טרייל 30%"
        if distance is not None:
            line = f"{line} · מרחק {_words(distance)}"
    else:
        line = "טרייל יומי"
    return {
        "status": "ok",
        "line": line,
        "kill": _money(abs(kill)),
        "pnl": _money(pnl),
        "floor": None if floor is None else _money(floor),
        "peak": None if peak is None else _money(peak),
        "next_threshold": None if arm is None else _money(arm),
        "next_floor": None if floor is None else _money(floor),
        "distance": None if distance is None else _money(distance),
        "protection_kind": "trail" if armed else "kill",
        "armed": armed,
    }


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


def _line(floor: Decimal | None, next_arm: Decimal | None, next_floor: Decimal | None, distance: Decimal | None) -> str:
    if next_arm is None or next_floor is None or distance is None:
        if floor is None:
            return "לא ידוע"
        return f"מוגנים {_words(floor)}"
    if floor is None:
        return f"עוד {_words(distance)} (ב-{_words(next_arm)}) נכנסת הגנה של {_words(next_floor)}"
    return f"מוגנים {_words(floor)} · עוד {_words(distance)} (ב-{_words(next_arm)}) ההגנה עולה ל-{_words(next_floor)}"


def _locked_floor(peak: Decimal | None, ladder: tuple[tuple[Decimal, Decimal], ...]) -> Decimal | None:
    found = None
    if peak is None:
        return None
    for arm, floor in ladder:
        if peak >= arm:
            found = floor
    return found


def _next_step(mark: Decimal | None, ladder: tuple[tuple[Decimal, Decimal], ...]) -> tuple[Decimal | None, Decimal | None]:
    cursor = Decimal("-1") if mark is None else mark
    for arm, floor in ladder:
        if arm > cursor:
            return arm, floor
    return None, None


def _reached(pnl: Decimal, arm: Decimal | None) -> bool:
    if arm is None:
        return False
    return (arm - pnl).quantize(Decimal("0.01")) <= 0


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
