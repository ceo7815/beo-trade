"""Stepped day floor. The floor ratchets up with the day's peak and never comes back down.

4,000 arms a floor of 2,000. That floor stays through 6,999. From 7,000 each extra 1,000 of peak
lifts the floor by 1,000, through 15,000 which locks 11,000. A higher peak does not lift it further.
Touching the active floor closes the day: sell everything, no new buys.
A missing P&L does not arm or release the latch. The latch advances only while the session is open,
so a stale prior-day mark before the open cannot close the next session.

The daily kill uses the same latch with reason DAILY_HARD_STOP. The next New York session starts clear.
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
    return {
        "session_date": session_date,
        "armed": armed,
        "closed": closed,
        "peak": _text(peak),
        "reason": "DAY_FLOOR" if closed else "",
        "cleared": bool(state.get("cleared")),
    }


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


def latch_hard_stop(settings: Settings, moment: datetime) -> dict:
    """Close the day the way a touched floor does: flatten, and no new buys until the next session."""
    trading = settings.trading()
    session_date = _session_date(settings, moment)
    prior = _for_date(_read(), session_date)
    base = prior or _blank(session_date)
    updated = {**base, "closed": True, "reason": "DAILY_HARD_STOP"}
    stored = _commit(prior, updated)
    return _public(stored, trading.day_floor_steps)


def mark_kill_cleared(settings: Settings, moment: datetime) -> dict:
    """Remember that this session's open orders were already canceled, so a later tick does not cancel the liquidation sells."""
    trading = settings.trading()
    session_date = _session_date(settings, moment)
    prior = _for_date(_read(), session_date)
    base = prior or _blank(session_date)
    updated = {**base, "closed": True, "reason": str(base.get("reason") or "DAILY_HARD_STOP"), "cleared": True}
    stored = _commit(prior, updated)
    return _public(stored, trading.day_floor_steps)


# One-time: clear the false DAILY_HARD_STOP latch caused by excluded bug lots on this NY day.
FALSE_HARD_STOP_RELEASE_DATE = "2026-10-30"
FALSE_HARD_STOP_RELEASE_REASON = (
    "false hard stop from excluded bug lots, approved by Or 30.10"
)


def _release_marker_path():
    from app.ops.paths import DATA_DIR

    return DATA_DIR / f"day_floor_false_hs_released_{FALSE_HARD_STOP_RELEASE_DATE}"


def maybe_release_false_hard_stop(
    settings: Settings,
    moment: datetime,
    adjusted_pnl: Decimal | None,
    equity: Decimal | None = None,
) -> dict | None:
    """Idempotent, date-scoped: reopen only today's false hard-stop latch.

    No-op when the session is not 2026-10-30, the latch is not DAILY_HARD_STOP,
    a release marker already exists, adjusted P&L is missing, or adjusted P&L
    still justifies the kill / percent hard stop. Does not change kill logic —
    a later real breach re-latches normally and will not be auto-released.
    """
    from app.options.risk_limits import day_flatten_reason

    session_date = _session_date(settings, moment)
    if session_date != FALSE_HARD_STOP_RELEASE_DATE:
        return None
    marker = _release_marker_path()
    if marker.exists():
        return None
    prior = _for_date(_read(), session_date)
    if not prior or not prior.get("closed"):
        return None
    if str(prior.get("reason") or "") != "DAILY_HARD_STOP":
        return None
    if adjusted_pnl is None:
        return None
    trading = settings.trading()
    if day_flatten_reason(equity, adjusted_pnl, trading) == "DAILY_HARD_STOP":
        return None
    fresh = _blank(session_date)
    # Write directly — _commit/_merge keep closed sticky and would block a release.
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(fresh), encoding="utf-8")
    temporary.replace(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(FALSE_HARD_STOP_RELEASE_REASON + "\n", encoding="utf-8")
    try:
        from app.analytics.read_cache import invalidate_heavy_reads

        invalidate_heavy_reads()
    except Exception:
        pass
    _audit_latch_released(session_date, adjusted_pnl)
    return _public(fresh, trading.day_floor_steps)


def run_false_hard_stop_release_once(settings: Settings, moment: datetime | None = None) -> dict | None:
    """Startup hook: peek broker snapshot + seed exclusions, then maybe release. No Alpaca call."""
    moment = moment or datetime.now(ZoneInfo("UTC"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    equity = None
    adjusted = None
    try:
        from app.broker.snapshot import peek
        from app.trades.exclusion import SEEDS, adjust_day_pnl, closed_rows_from_seeds, matches_trade

        account = peek("account")
        raw_positions = peek("positions")
        held = [row for row in (raw_positions if isinstance(raw_positions, list) else []) if isinstance(row, dict)]
        if isinstance(account, dict):
            equity = _decimal(account.get("equity"))
            last = _decimal(account.get("last_equity"))
            measured = None if equity is None or last is None else equity - last
        else:
            measured = None
        # Prefer /risk cache when present (already exclusion-adjusted).
        try:
            from app.analytics.read_cache import read_cache

            cached = read_cache.get(("risk",))
            if not isinstance(cached, dict):
                cached = read_cache.get_stale(("risk",))
            if isinstance(cached, dict) and cached.get("today_pnl") not in {None, ""}:
                adjusted = _decimal(cached.get("today_pnl"))
        except Exception:
            pass
        if adjusted is None and measured is not None:
            rules = [{**seed, "active": True} for seed in SEEDS]
            try:
                from app.trades.exclusion import active_exclusions

                loaded = active_exclusions()
                if loaded:
                    rules = loaded
            except Exception:
                pass
            session_date = moment.astimezone(ZoneInfo(settings.exchange_timezone)).date()
            open_excluded = {
                str(row.get("symbol") or row.get("option_symbol") or "").replace(" ", "").upper()
                for row in held
                if matches_trade(row, rules)
            }
            closed = closed_rows_from_seeds(rules, session_date, open_excluded)
            adjusted = adjust_day_pnl(measured, held, rules, session_date=session_date, closed_rows=closed)
    except Exception:
        return None
    return maybe_release_false_hard_stop(settings, moment, adjusted, equity)


def _audit_latch_released(session_date: str, adjusted_pnl: Decimal) -> None:
    try:
        from sqlalchemy import select

        from app.models.db import SessionLocal, database_ready
        from app.models.tables import AuditLog

        if not database_ready() or SessionLocal is None:
            return
        entity_id = f"latch-rel:{session_date}"[:36]
        with SessionLocal() as session:
            prior = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "DAY_LATCH_RELEASED",
                    AuditLog.entity == "day_floor",
                    AuditLog.entity_id == entity_id,
                )
            )
            if prior is not None:
                return
            session.add(
                AuditLog(
                    actor="beo-trade",
                    action="DAY_LATCH_RELEASED",
                    entity="day_floor",
                    entity_id=entity_id,
                    payload={
                        "session_date": session_date,
                        "reason": FALSE_HARD_STOP_RELEASE_REASON,
                        "adjusted_pnl": str(adjusted_pnl),
                    },
                )
            )
            session.commit()
    except Exception:
        pass


def day_close_reason(settings: Settings, moment: datetime) -> str | None:
    view = public_from_store(settings, moment)
    if not view.get("closed"):
        return None
    return str(view.get("reason") or "DAY_FLOOR")


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
    return {"session_date": session_date, "armed": False, "closed": False, "peak": None, "reason": "", "cleared": False}


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
    reason = str(row.get("reason") or "")
    if row.get("closed") and not reason:
        reason = "DAY_FLOOR"
    return {
        "armed": bool(row.get("armed")),
        "closed": bool(row.get("closed")),
        "arm": _text(arm) or "0.00",
        "floor": _text(floor) or "0.00",
        "reason": reason,
        "cleared": bool(row.get("cleared")),
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
    try:
        from app.analytics.read_cache import invalidate_heavy_reads

        invalidate_heavy_reads()
    except Exception:
        pass
    return merged


def _merge(prior: dict | None, updated: dict) -> dict:
    if not prior:
        return updated
    peaks = [item for item in (_decimal(prior.get("peak")), _decimal(updated.get("peak"))) if item is not None]
    reason = str(prior.get("reason") or "")
    incoming = str(updated.get("reason") or "")
    if incoming == "DAILY_HARD_STOP" or not reason:
        reason = incoming or reason
    return {
        "session_date": updated["session_date"],
        "armed": bool(prior.get("armed") or updated.get("armed")),
        "closed": bool(prior.get("closed") or updated.get("closed")),
        "peak": _text(max(peaks) if peaks else None),
        "reason": reason,
        "cleared": bool(prior.get("cleared") or updated.get("cleared")),
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
