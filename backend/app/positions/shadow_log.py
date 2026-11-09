"""Persist shadow entry-rule evaluations off the order path.

`note_entry_shadow` returns immediately. A daemon thread does the reads and the
insert. A full queue or a raised exception is dropped. Neither reaches the caller.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.positions.shadow_rules import (
    SETUP_NOT_AVAILABLE,
    STACK_WINDOW_MINUTES,
    OpenLeg,
    RecentEntry,
    SessionPath,
    ShadowView,
    analogs_from_sessions,
    evaluate_all,
    loss_fraction,
    move_bucket,
)

_NY = ZoneInfo("America/New_York")
_QUEUE: queue.Queue = queue.Queue(maxsize=128)
_WORKER: threading.Thread | None = None
_START_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
# (session date, paused) remembered inside this process. The latest row reloads it after a restart.
_PAUSE: tuple[str, bool] = ("", False)
_SETUP_TODO = (
    "TODO: no cheap prior-session minute history for this underlying. "
    "market_snapshots is the only local series; do not add a paid history feed for this rule."
)


def note_entry_shadow(snapshot: dict) -> None:
    """Enqueue one evaluation. Never raises and never waits on the database."""
    try:
        if not isinstance(snapshot, dict):
            return
        if snapshot.get("enabled") is False:
            return
        _ensure_worker()
        _QUEUE.put_nowait(snapshot)
    except Exception:
        return


def drain_shadow(timeout: float = 3.0) -> None:
    """Wait until queued evaluations have been attempted. Tests use this."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _QUEUE.unfinished_tasks == 0:
            return
        time.sleep(0.01)


def reset_shadow_memory() -> None:
    """Drop the in-process pause flag. The table is left as it is."""
    global _PAUSE
    with _STATE_LOCK:
        _PAUSE = ("", False)


def process_snapshot(snapshot: dict) -> None:
    """Evaluate and insert one snapshot. Safe to call on the worker or in a test."""
    if not isinstance(snapshot, dict) or snapshot.get("enabled") is False:
        return
    with _STATE_LOCK:
        _process_locked(snapshot)


def shadow_report(limit: int = 100) -> dict:
    """Recent rows and a per-rule split of closed P&L. Read-only."""
    from app.config.settings import get_settings
    from app.models.db import database_ready, session_scope

    bounded = max(1, min(int(limit or 100), 500))
    enabled = True
    try:
        enabled = bool(get_settings().trading().shadow_entry_rules_enabled)
    except Exception:
        enabled = True
    if not database_ready():
        return {"shadow_entry_rules_enabled": enabled, "available": False, "rows": [], "summary": []}
    with session_scope() as session:
        from sqlalchemy import select

        from app.models.tables import ShadowRuleEval

        stored = list(
            session.scalars(select(ShadowRuleEval).order_by(ShadowRuleEval.evaluated_at.desc()).limit(bounded))
        )
        closed = _closed_pnl(session, stored)
    rows = [_public_row(row, closed.get(row.recommendation_id)) for row in stored]
    return {
        "shadow_entry_rules_enabled": enabled,
        "available": True,
        "behavior": "log_only",
        "rows": rows,
        "summary": _summary(rows),
    }


def snapshot_from_entry(
    row,
    *,
    outcome: str,
    block_reason: str,
    moment: datetime,
    positions: list | None,
    day_pnl,
    open_equity,
    spot,
    order_id: str,
    scan_entries: list | None,
    enabled: bool,
) -> dict:
    """Plain dict copied from values the submit loop already holds. No network and no extra query."""
    side = _row_side(row)
    expiration = getattr(row, "expiration", None)
    price, opened, prior, vwap = _spot_fields(spot)
    signed_open = _ratio(price, opened)
    signed_vwap = _ratio(price, vwap)
    signed_prior = _ratio(price, prior)
    return {
        "enabled": bool(enabled),
        "recommendation_id": str(getattr(row, "recommendation_id", "") or ""),
        "order_id": str(order_id or ""),
        "outcome": str(outcome or ""),
        "existing_block_reason": str(block_reason or ""),
        "evaluated_at": _aware(moment).isoformat(),
        "underlying": str(getattr(row, "underlying", "") or ""),
        "option_symbol": str(getattr(row, "option_symbol", "") or ""),
        "side": side,
        "dte": _dte(expiration, moment),
        "minutes_since_open": _minutes_since_open(moment),
        "underlying_price": _text(price),
        "session_open": _text(opened),
        "prior_close": _text(prior),
        "vwap": _text(vwap),
        "price_vs_open_pct": _text(signed_open),
        "price_vs_vwap_pct": _text(signed_vwap),
        "change_vs_prior_close_pct": _text(signed_prior),
        "positions": [_position_view(item) for item in (positions or [])],
        "day_pnl": _text(day_pnl),
        "open_equity": _text(open_equity),
        "scan_entries": list(scan_entries or []),
    }


def _process_locked(snapshot: dict) -> None:
    from app.models.db import database_ready, session_scope

    if not database_ready():
        return
    moment = _parse_moment(snapshot.get("evaluated_at")) or datetime.now(timezone.utc)
    session_date = moment.astimezone(_NY).date().isoformat()
    with session_scope() as session:
        legs = _legs(session, snapshot.get("positions") or [], moment)
        entries = _recent(session, moment, snapshot.get("scan_entries") or [], legs)
        paused = _paused_for(session, session_date)
        day_pnl = _decimal(snapshot.get("day_pnl"))
        opened = _decimal(snapshot.get("open_equity"))
        signed, loss = loss_fraction(day_pnl, opened)
        samples, detail = _setup(session, snapshot, moment)
        view = ShadowView(
            side=str(snapshot.get("side") or ""),
            moment=moment,
            open_legs=legs,
            recent_entries=entries,
            day_pnl_pct=signed,
            loss_pct=loss,
            day_pnl=day_pnl,
            open_equity=opened,
            paused=paused,
            same_pause_session=True,
            setup_samples=samples,
            setup_detail=detail,
        )
        # same_pause_session is applied by _paused_for, which already reset on a new date.
        rules, new_paused = evaluate_all(view)
        _remember(session_date, new_paused)
        _insert(session, snapshot, moment, rules, view)
        session.commit()


def _ensure_worker() -> None:
    global _WORKER
    with _START_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        thread = threading.Thread(target=_worker_loop, name="shadow-entry-rules", daemon=True)
        thread.start()
        _WORKER = thread


def _worker_loop() -> None:
    while True:
        item = _QUEUE.get()
        try:
            process_snapshot(item)
        except Exception:
            pass
        finally:
            _QUEUE.task_done()


def _paused_for(session, session_date: str) -> bool:
    global _PAUSE
    if _PAUSE[0] == session_date:
        return _PAUSE[1]
    if _PAUSE[0] and _PAUSE[0] != session_date:
        _PAUSE = (session_date, False)
        return False
    found = _load_paused(session, session_date)
    _PAUSE = (session_date, found)
    return found


def _remember(session_date: str, paused: bool) -> None:
    global _PAUSE
    _PAUSE = (session_date, bool(paused))


def _load_paused(session, session_date: str) -> bool:
    from sqlalchemy import select

    from app.models.tables import ShadowRuleEval

    rows = session.scalars(select(ShadowRuleEval).order_by(ShadowRuleEval.evaluated_at.desc()).limit(50))
    for row in rows:
        stamp = row.evaluated_at
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        if stamp.astimezone(_NY).date().isoformat() != session_date:
            continue
        for rule in row.rules or []:
            if not isinstance(rule, dict) or rule.get("rule") != "day_loss_pause":
                continue
            inputs = rule.get("inputs") if isinstance(rule.get("inputs"), dict) else {}
            if "paused_after" in inputs:
                return bool(inputs.get("paused_after"))
    return False


def _insert(session, snapshot: dict, moment: datetime, rules: list[dict], view: ShadowView) -> None:
    from app.models.tables import ShadowRuleEval

    session.add(
        ShadowRuleEval(
            recommendation_id=str(snapshot.get("recommendation_id") or "")[:36],
            order_id=str(snapshot.get("order_id") or "")[:64],
            evaluated_at=moment,
            outcome=str(snapshot.get("outcome") or "")[:16],
            existing_block_reason=str(snapshot.get("existing_block_reason") or "")[:300],
            underlying=str(snapshot.get("underlying") or "")[:32],
            option_symbol=str(snapshot.get("option_symbol") or "")[:64],
            side=str(snapshot.get("side") or "")[:4],
            dte=snapshot.get("dte") if isinstance(snapshot.get("dte"), int) else None,
            minutes_since_open=_num(snapshot.get("minutes_since_open")),
            underlying_price=_num(snapshot.get("underlying_price")),
            session_open=_num(snapshot.get("session_open")),
            prior_close=_num(snapshot.get("prior_close")),
            vwap=_num(snapshot.get("vwap")),
            price_vs_open_pct=_num(snapshot.get("price_vs_open_pct")),
            price_vs_vwap_pct=_num(snapshot.get("price_vs_vwap_pct")),
            change_vs_prior_close_pct=_num(snapshot.get("change_vs_prior_close_pct")),
            rules=rules,
            context={
                "day_pnl": snapshot.get("day_pnl"),
                "open_equity": snapshot.get("open_equity"),
                "day_pnl_pct": _text(view.day_pnl_pct),
                "loss_pct": _text(view.loss_pct),
                "positions": snapshot.get("positions") or [],
                "scan_entries": snapshot.get("scan_entries") or [],
                "move_bucket": move_bucket(_decimal(snapshot.get("price_vs_open_pct"))),
            },
        )
    )


def _legs(session, positions: list, moment: datetime) -> list[OpenLeg]:
    opened = _open_times(session)
    legs: list[OpenLeg] = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").replace(" ", "")
        side = str(row.get("side") or "")
        when = _parse_moment(row.get("opened_at")) or opened.get(symbol)
        legs.append(
            OpenLeg(
                side=side,
                symbol=symbol,
                opened_at=when,
                pnl_pct=_decimal(row.get("pnl_pct")),
                pnl_dollars=_decimal(row.get("pnl_dollars")),
            )
        )
    return legs


def _open_times(session) -> dict[str, datetime]:
    from sqlalchemy import select

    from app.models.tables import PositionState

    found: dict[str, datetime] = {}
    try:
        rows = session.scalars(select(PositionState).where(PositionState.status == "open"))
    except Exception:
        return found
    for state in rows:
        symbol = str(state.symbol or "").replace(" ", "")
        if symbol and state.entry_time is not None:
            found[symbol] = _aware(state.entry_time)
    return found


def _recent(session, moment: datetime, scan_entries: list, legs: list[OpenLeg]) -> list[RecentEntry]:
    from datetime import timedelta

    from sqlalchemy import select

    from app.broker.normalize import parse_option_symbol
    from app.models.tables import BrokerOrderRecord
    from app.positions.entry_pace import _dead

    pnl_by_symbol = {leg.symbol: leg.pnl_pct for leg in legs if leg.symbol}
    found: list[RecentEntry] = []
    since = moment - timedelta(minutes=STACK_WINDOW_MINUTES + 1)
    try:
        rows = session.scalars(
            select(BrokerOrderRecord)
            .where(
                BrokerOrderRecord.position_intent == "buy_to_open",
                BrokerOrderRecord.created_at >= since,
            )
            .order_by(BrokerOrderRecord.created_at.desc())
            .limit(200)
        )
        for row in rows:
            if row.created_at is None or _dead(row.status):
                continue
            placed = _aware(row.created_at)
            age = (moment - placed).total_seconds() / 60
            if age < 0 or age >= STACK_WINDOW_MINUTES:
                continue
            contract = parse_option_symbol(row.symbol)
            side = "" if contract is None else str(contract["right"])
            symbol = str(row.symbol or "").replace(" ", "")
            found.append(RecentEntry(placed, side, symbol, pnl_by_symbol.get(symbol)))
    except Exception:
        found = []
    for item in scan_entries:
        if not isinstance(item, dict):
            continue
        placed = _parse_moment(item.get("at"))
        if placed is None:
            continue
        age = (moment - placed).total_seconds() / 60
        if age < 0 or age >= STACK_WINDOW_MINUTES:
            continue
        symbol = str(item.get("symbol") or "").replace(" ", "")
        found.append(RecentEntry(placed, str(item.get("side") or ""), symbol, pnl_by_symbol.get(symbol)))
    unique: list[RecentEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for item in found:
        key = (item.symbol, item.side, item.at.replace(microsecond=0).isoformat())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _setup(session, snapshot: dict, moment: datetime) -> tuple[list[bool] | None, dict]:
    """Local market_snapshots only. A miss is not_available, not a network fetch.

    TODO: the desk does not keep a dedicated prior-session minute-bar series.
    Do not call ThetaData (or any paid history) from this path.
    """
    detail = {
        "source": SETUP_NOT_AVAILABLE,
        "todo": _SETUP_TODO,
        "move_bucket": move_bucket(_decimal(snapshot.get("price_vs_open_pct"))),
        "minutes_since_open": snapshot.get("minutes_since_open"),
    }
    symbol = str(snapshot.get("underlying") or "").strip().upper()
    minutes = snapshot.get("minutes_since_open")
    move = _decimal(snapshot.get("price_vs_open_pct"))
    if not symbol or minutes is None:
        return None, detail
    try:
        sessions = _sessions_from_snapshots(session, symbol, moment)
    except Exception:
        return None, detail
    if sessions is None:
        return None, detail
    if not sessions:
        return None, detail
    samples, extra = analogs_from_sessions(
        sessions,
        minutes_since_open=float(minutes),
        side=str(snapshot.get("side") or ""),
        move_from_open=move,
    )
    extra["todo"] = ""
    return samples, extra


def _sessions_from_snapshots(session, symbol: str, moment: datetime) -> list[SessionPath] | None:
    """None when this underlying has no stored snapshots. Bounded to the latest rows."""
    from sqlalchemy import select

    from app.models.tables import MarketSnapshot, Underlying

    underlying = session.scalar(select(Underlying).where(Underlying.symbol == symbol))
    if underlying is None:
        return None
    rows = list(
        session.scalars(
            select(MarketSnapshot)
            .where(MarketSnapshot.underlying_id == underlying.id)
            .order_by(MarketSnapshot.observed_at.desc())
            .limit(4000)
        )
    )
    if not rows:
        return None
    today = moment.astimezone(_NY).date()
    grouped: dict = {}
    for row in rows:
        stamp = row.observed_at
        if stamp is None:
            continue
        local = _aware(stamp).astimezone(_NY)
        if local.date() >= today:
            continue
        minute = (local.hour * 60 + local.minute) - (9 * 60 + 30)
        price = _decimal(row.price)
        opened = _decimal(row.open)
        if price is None or opened is None or opened <= 0:
            continue
        bucket = grouped.setdefault(local.date(), {"open": opened, "points": []})
        if opened > 0:
            bucket["open"] = opened
        bucket["points"].append((float(minute), price))
    sessions: list[SessionPath] = []
    for payload in grouped.values():
        points = tuple(sorted(payload["points"], key=lambda item: item[0]))
        sessions.append(SessionPath(payload["open"], points))
    return sessions


def _closed_pnl(session, rows: list) -> dict[str, str]:
    """Final dollars for a submitted recommendation, once the trade is closed."""
    from sqlalchemy import select

    from app.analytics.trades import round_trips
    from app.models.tables import BrokerFillRecord, BrokerOrderRecord, PaperTrade

    wanted = {
        str(row.recommendation_id or "")
        for row in rows
        if str(row.outcome or "") == "submitted" and row.recommendation_id
    }
    if not wanted:
        return {}
    found: dict[str, str] = {}
    trades = session.scalars(
        select(PaperTrade).where(PaperTrade.recommendation_id.in_(wanted), PaperTrade.exit_at.is_not(None))
    )
    for trade in trades:
        if trade.pnl_dollars is None or not trade.recommendation_id:
            continue
        found[str(trade.recommendation_id)] = str(Decimal(str(trade.pnl_dollars)))
    missing = wanted - set(found)
    if not missing:
        return found
    orders = list(
        session.scalars(
            select(BrokerOrderRecord).where(
                BrokerOrderRecord.recommendation_id.in_(missing),
                BrokerOrderRecord.position_intent == "buy_to_open",
            )
        )
    )
    by_order: dict[str, str] = {}
    symbols: set[str] = set()
    for order in orders:
        broker_id = str(order.broker_order_id or "")
        if broker_id and order.recommendation_id:
            by_order[broker_id] = str(order.recommendation_id)
            symbols.add(str(order.symbol or "").replace(" ", "").upper())
    if not by_order or not symbols:
        return found
    payloads: list[dict] = []
    fills = session.scalars(select(BrokerFillRecord).where(BrokerFillRecord.symbol.in_(symbols)).limit(2000))
    for fill in fills:
        payload = dict(fill.payload) if isinstance(fill.payload, dict) else {}
        if fill.symbol and not payload.get("symbol"):
            payload["symbol"] = fill.symbol
        payloads.append(payload)
    for trip in round_trips(payloads).get("closed") or []:
        pnl = trip.get("pnl")
        if pnl in {None, ""}:
            continue
        for order_id in trip.get("order_ids") or []:
            recommendation = by_order.get(str(order_id))
            if recommendation and recommendation not in found:
                found[recommendation] = str(pnl)
    return found


def _public_row(row, closed_pnl: str | None) -> dict:
    return {
        "id": row.id,
        "recommendation_id": row.recommendation_id,
        "order_id": row.order_id,
        "evaluated_at": None if row.evaluated_at is None else _aware(row.evaluated_at).isoformat(),
        "outcome": row.outcome,
        "existing_block_reason": row.existing_block_reason,
        "underlying": row.underlying,
        "option_symbol": row.option_symbol,
        "side": row.side,
        "dte": row.dte,
        "minutes_since_open": _text(row.minutes_since_open),
        "underlying_price": _text(row.underlying_price),
        "session_open": _text(row.session_open),
        "prior_close": _text(row.prior_close),
        "vwap": _text(row.vwap),
        "price_vs_open_pct": _text(row.price_vs_open_pct),
        "price_vs_vwap_pct": _text(row.price_vs_vwap_pct),
        "change_vs_prior_close_pct": _text(row.change_vs_prior_close_pct),
        "rules": list(row.rules or []),
        "context": row.context or {},
        "closed_pnl": closed_pnl,
    }


def _summary(rows: list[dict]) -> list[dict]:
    totals: dict[str, dict] = {}
    for row in rows:
        if row.get("outcome") != "submitted":
            continue
        pnl = _decimal(row.get("closed_pnl"))
        for rule in row.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            name = str(rule.get("rule") or "")
            if not name:
                continue
            bucket = totals.setdefault(
                name,
                {
                    "rule": name,
                    "evaluated": 0,
                    "would_block": 0,
                    "would_allow": 0,
                    "closed_would_block": 0,
                    "closed_would_allow": 0,
                    "would_block_pnl": Decimal("0"),
                    "would_allow_pnl": Decimal("0"),
                },
            )
            bucket["evaluated"] += 1
            blocked = bool(rule.get("would_block"))
            if blocked:
                bucket["would_block"] += 1
            else:
                bucket["would_allow"] += 1
            if pnl is None:
                continue
            if blocked:
                bucket["closed_would_block"] += 1
                bucket["would_block_pnl"] += pnl
            else:
                bucket["closed_would_allow"] += 1
                bucket["would_allow_pnl"] += pnl
    report = []
    for name in ("stack_confirm", "stack_window", "open_loser_same_dir", "day_loss_pause", "setup_history"):
        bucket = totals.get(name)
        if bucket is None:
            continue
        report.append(
            {
                "rule": name,
                "evaluated": bucket["evaluated"],
                "would_block": bucket["would_block"],
                "would_allow": bucket["would_allow"],
                "closed_would_block": bucket["closed_would_block"],
                "closed_would_allow": bucket["closed_would_allow"],
                "would_block_pnl": format(bucket["would_block_pnl"], "f"),
                "would_allow_pnl": format(bucket["would_allow_pnl"], "f"),
            }
        )
    return report


def _position_view(row: dict) -> dict:
    from app.broker.normalize import parse_option_symbol

    symbol = str(row.get("symbol") or "").replace(" ", "")
    contract = parse_option_symbol(symbol)
    side = "" if contract is None else str(contract["right"])
    pnl_pct = _decimal(row.get("unrealized_plpc"))
    if pnl_pct is None:
        pnl_pct = _decimal(row.get("pnl_pct"))
    dollars = _decimal(row.get("unrealized_pl"))
    if dollars is None:
        dollars = _decimal(row.get("pnl_dollars"))
    if pnl_pct is None:
        entry = _decimal(row.get("avg_entry_price"))
        qty = _decimal(row.get("qty"))
        if dollars is not None and entry is not None and qty is not None and entry > 0 and qty > 0:
            pnl_pct = dollars / (entry * qty * Decimal("100"))
    return {
        "symbol": symbol,
        "side": side,
        "pnl_pct": _text(pnl_pct),
        "pnl_dollars": _text(dollars),
    }


def _spot_fields(spot):
    if spot is None:
        return None, None, None, None
    price = _decimal(getattr(spot, "price", None))
    opened = _decimal(getattr(spot, "open", None))
    prior = _decimal(getattr(spot, "prior_close", None))
    vwap = _vwap(getattr(spot, "bars", None))
    return price, opened, prior, vwap


def _vwap(bars) -> Decimal | None:
    if not bars:
        return None
    try:
        from app.quant.bars import vwap

        prices = []
        volumes = []
        for bar in list(bars)[-600:]:
            close = getattr(bar, "close", None)
            volume = getattr(bar, "volume", None)
            if close is None or volume is None:
                continue
            prices.append(float(close))
            volumes.append(int(volume))
        value = vwap(prices, volumes)
    except Exception:
        return None
    if value is None:
        return None
    return Decimal(str(value))


def _minutes_since_open(moment: datetime) -> float | None:
    try:
        local = _aware(moment).astimezone(_NY)
        opened = local.replace(hour=9, minute=30, second=0, microsecond=0)
        return (local - opened).total_seconds() / 60
    except Exception:
        return None


def _dte(expiration, moment: datetime) -> int | None:
    try:
        if hasattr(expiration, "year") and not isinstance(expiration, datetime):
            day = expiration
        else:
            day = datetime.fromisoformat(str(expiration)[:10]).date()
        return (day - _aware(moment).astimezone(_NY).date()).days
    except Exception:
        return None


def _row_side(row) -> str:
    call_put = getattr(row, "call_put", "")
    return str(getattr(call_put, "value", call_put) or "").upper()


def _ratio(price, basis) -> Decimal | None:
    if price is None or basis is None or basis <= 0:
        return None
    return (price - basis) / basis


def _parse_moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _decimal(value) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if value in {None, ""}:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _num(value):
    number = _decimal(value)
    if number is None:
        return None
    return float(number)


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)
