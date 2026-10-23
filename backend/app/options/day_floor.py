"""Stepped day floor. The floor ratchets up with the day's peak and never comes back down.

4,000 arms a floor of 2,000. That floor stays through 6,999. From 7,000 each extra 1,000 of peak
lifts the floor by 1,000, through 15,000 which locks 11,000. A higher peak does not lift it further.
Touching the active floor closes the day: sell everything, no new buys.
A missing P&L does not arm or release the latch. The latch advances only while the session is open,
so a stale prior-day mark before the open cannot close the next session.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.config.settings import Settings

def store_path():
    from app.ops.paths import DATA_DIR

    return DATA_DIR / "day_floor.json"


def advance(prior: dict | None, session_date: str, pnl: Decimal | None, steps: tuple | None = None) -> dict:
    """Raise the floor to the highest step the peak has reached. A closed day stays closed."""
    ladder = _ladder(steps)
    state = prior if prior and prior.get("session_date") == session_date else _blank(session_date)
    if not ladder:
        return _blank(session_date)
    if state.get("closed"):
        return state
    if pnl is None:
        return state
    peak = _decimal(state.get("peak"))
    if peak is None or pnl > peak:
        peak = pnl
    _, floor = _level(peak, ladder)
    armed = floor is not None
    closed = armed and pnl <= floor
    return {"session_date": session_date, "armed": armed, "closed": closed, "peak": _text(peak)}


def floor_sell_price(bid: Decimal | None, mark: Decimal | None, discount: Decimal) -> Decimal | None:
    """Sell at the live bid. Without a bid, sell under the broker mark so the order can fill."""
    if bid is not None and bid > 0:
        return bid
    if mark is None or mark <= 0:
        return None
    tick = Decimal("0.05") if mark >= 3 else Decimal("0.01")
    price = (mark * (Decimal("1") - discount) / tick).to_integral_value(rounding="ROUND_FLOOR") * tick
    return max(price, Decimal("0.01"))


def observe_day_floor(settings: Settings, pnl: Decimal | None, moment: datetime, session_open: bool) -> dict:
    trading = settings.trading()
    session_date = _session_date(settings, moment)
    prior = _for_date(_read(), session_date)
    if not session_open:
        return _public(prior, trading.day_floor_steps)
    updated = advance(prior, session_date, pnl, trading.day_floor_steps)
    stored = _commit(prior, updated)
    return _public(stored, trading.day_floor_steps)


def public_from_store(settings: Settings, moment: datetime) -> dict:
    trading = settings.trading()
    session_date = _session_date(settings, moment)
    return _public(_for_date(_read(), session_date), trading.day_floor_steps)


def day_is_closed(settings: Settings, moment: datetime) -> bool:
    return bool(public_from_store(settings, moment).get("closed"))


def _session_date(settings: Settings, moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(ZoneInfo(settings.exchange_timezone)).date().isoformat()


def _blank(session_date: str) -> dict:
    return {"session_date": session_date, "armed": False, "closed": False, "peak": None}


def _ladder(steps: tuple | None) -> tuple[tuple[Decimal, Decimal], ...]:
    if steps is None:
        from app.config.settings import TradingConfig

        steps = TradingConfig().day_floor_steps
    raw = steps or ()
    parsed: list[tuple[Decimal, Decimal]] = []
    for item in raw:
        arm, lock = Decimal(str(item[0])), Decimal(str(item[1]))
        if arm <= 0 or lock <= 0 or lock >= arm:
            return ()
        parsed.append((arm, lock))
    parsed.sort(key=lambda step: step[0])
    return tuple(parsed)


def _level(peak: Decimal | None, ladder: tuple[tuple[Decimal, Decimal], ...]) -> tuple[Decimal | None, Decimal | None]:
    arm: Decimal | None = None
    floor: Decimal | None = None
    if peak is None:
        return None, None
    for step_arm, step_floor in ladder:
        if peak >= step_arm:
            arm, floor = step_arm, step_floor
    return arm, floor


def _public(state: dict | None, steps: tuple | None) -> dict:
    ladder = _ladder(steps)
    row = state or {}
    first_arm, first_floor = ladder[0] if ladder else (Decimal("0"), Decimal("0"))
    arm, floor = _level(_decimal(row.get("peak")), ladder)
    if not row.get("armed") or floor is None:
        arm, floor = first_arm, first_floor
    return {
        "armed": bool(row.get("armed")),
        "closed": bool(row.get("closed")),
        "arm": _text(arm) or "0.00",
        "floor": _text(floor) or "0.00",
    }


def _for_date(raw: dict | None, session_date: str) -> dict | None:
    if not raw or raw.get("session_date") != session_date:
        return None
    return raw


def _read() -> dict | None:
    path = store_path()
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _commit(prior: dict | None, updated: dict) -> dict:
    disk = _for_date(_read(), updated.get("session_date", ""))
    base = _merge(disk, prior) if disk and prior else (disk or prior)
    merged = _merge(base, updated)
    if base == merged:
        return merged
    if base is None and not merged.get("armed") and not merged.get("closed"):
        return merged
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(merged), encoding="utf-8")
    temporary.replace(path)
    return merged


def _merge(prior: dict | None, updated: dict) -> dict:
    if not prior:
        return updated
    peaks = [item for item in (_decimal(prior.get("peak")), _decimal(updated.get("peak"))) if item is not None]
    return {
        "session_date": updated["session_date"],
        "armed": bool(prior.get("armed") or updated.get("armed")),
        "closed": bool(prior.get("closed") or updated.get("closed")),
        "peak": _text(max(peaks) if peaks else None),
    }


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))
