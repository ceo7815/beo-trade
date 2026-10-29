"""Pace gates for a new buy. Exits, stops, the trail, the kill switch, and the day floor do not read this.

Three checks, all on the New York trading day:
- at most N submitted buys in a rolling window (pending and filled count; a canceled buy does not)
- no opposite side inside that same window
- after a close with realized P&L below zero, the opposite side waits out its own window

The book is loaded from stored orders and closed trades, so a worker restart does not forget it.
A missing read blocks the buy. The same side stays eligible after a loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.config.settings import TradingConfig

ENTRY_RATE_CAP = "ENTRY_RATE_CAP"
SIDE_MIX_BLOCK = "SIDE_MIX_BLOCK"
POST_LOSS_OPPOSITE_BLOCK = "POST_LOSS_OPPOSITE_BLOCK"

SIDES = frozenset({"CALL", "PUT"})
# A replaced order is a finished one: the replacement, if it was sent, is its own row.
_DEAD = frozenset({"rejected", "canceled", "cancelled", "expired", "blocked", "done_for_day", "replaced"})
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class EntryStamp:
    at: datetime
    right: str


@dataclass(frozen=True)
class LossStamp:
    at: datetime
    right: str


@dataclass
class PaceBook:
    entries: list[EntryStamp] = field(default_factory=list)
    losses: list[LossStamp] = field(default_factory=list)
    readable: bool = True

    def note_entry(self, side: str, moment: datetime) -> None:
        """A buy this process just submitted. The next candidate in the same scan sees it."""
        right = str(side or "").upper()
        self.entries.append(EntryStamp(_aware(moment), right if right in SIDES else ""))


def entry_pace_reason(side: str, moment: datetime, book: PaceBook | None, config: TradingConfig) -> str | None:
    """A skip code, or None when this new buy may continue to the other gates."""
    if not config.entry_pace_enabled:
        return None
    if book is None or not book.readable:
        return ENTRY_RATE_CAP
    right = str(side or "").upper()
    window = int(config.entry_pace_window_minutes)
    recent = [item for item in book.entries if _in_window(item.at, moment, window)]
    if int(config.entry_pace_max_entries) > 0 and window > 0 and len(recent) >= int(config.entry_pace_max_entries):
        return ENTRY_RATE_CAP
    if window > 0 and right in SIDES and any(item.right in SIDES and item.right != right for item in recent):
        return SIDE_MIX_BLOCK
    block_for = int(config.entry_pace_opposite_block_minutes)
    if block_for > 0 and right in SIDES:
        recent_losses = [item for item in book.losses if _in_window(item.at, moment, block_for)]
        if any(item.right in SIDES and item.right != right for item in recent_losses):
            return POST_LOSS_OPPOSITE_BLOCK
    return None


def losses_from_closed(rows: list[dict], moment: datetime, minutes: int) -> list[LossStamp]:
    """Losing closes inside the window. A flat or winning close does not start a block."""
    found: list[LossStamp] = []
    for row in rows:
        pnl = _decimal(row.get("pnl"))
        if pnl is None or pnl >= 0:
            continue
        right = str(row.get("right") or "").upper()
        if right not in SIDES:
            from app.broker.normalize import parse_option_symbol

            contract = parse_option_symbol(str(row.get("symbol") or ""))
            right = "" if contract is None else str(contract["right"])
        closed_at = _moment(row.get("closed_at"))
        if closed_at is None or right not in SIDES:
            continue
        if _in_window(closed_at, moment, minutes):
            found.append(LossStamp(closed_at, right))
    return found


def load_pace_book(moment: datetime, config: TradingConfig) -> PaceBook | None:
    """Orders and closes already stored. None means the book could not be read."""
    from app.models.db import database_ready, session_scope

    if not database_ready():
        return None
    try:
        with session_scope() as session:
            entries = _stored_entries(session, moment, int(config.entry_pace_window_minutes))
            losses = _stored_losses(session, moment, int(config.entry_pace_opposite_block_minutes))
        return PaceBook(entries, losses, True)
    except Exception as exc:
        from app.ops.failure import log_failure

        log_failure("worker.scan_caught", exc, service="worker", where="entry_pace")
        return None


def _stored_entries(session, moment: datetime, window_minutes: int) -> list[EntryStamp]:
    from sqlalchemy import select

    from app.broker.normalize import parse_option_symbol
    from app.models.tables import BrokerOrderRecord

    if window_minutes <= 0:
        return []
    from app.core.sessions import new_york_day_start

    # Yesterday is included so a timestamp stored without an offset is not dropped.
    # The window check below is what decides which buys count.
    day_start = new_york_day_start(moment) - timedelta(days=1)
    rows = list(
        session.scalars(
            select(BrokerOrderRecord)
            .where(
                BrokerOrderRecord.position_intent == "buy_to_open",
                BrokerOrderRecord.created_at >= day_start,
            )
            .order_by(BrokerOrderRecord.created_at.desc())
            .limit(500)
        )
    )
    in_window = [row for row in rows if row.created_at is not None and _in_window(_aware(row.created_at), moment, window_minutes)]
    latest = _latest_order_state(
        session,
        [str(row.broker_order_id or "") for row in in_window],
        new_york_day_start(moment) - timedelta(days=1),
    )
    found: list[EntryStamp] = []
    for row in rows:
        if row.created_at is None:
            continue
        placed = _aware(row.created_at)
        if not _in_window(placed, moment, window_minutes):
            continue
        order_id = str(row.broker_order_id or "")
        # The latest broker snapshot is the live state. The submit row is the fallback, so a
        # pending buy with no snapshot yet still counts and a later cancel does not.
        state = latest[order_id] if order_id and order_id in latest else str(row.status or "")
        if _dead(state):
            continue
        contract = parse_option_symbol(row.symbol)
        right = "" if contract is None else str(contract["right"])
        found.append(EntryStamp(placed, right))
    return found


def _latest_order_state(session, order_ids: list[str], since: datetime) -> dict[str, str]:
    """Latest state for these orders. The payload column is not loaded.

    Snapshots are one row per reconcile. Selecting the JSON payload for every
    historical order was enough to exhaust the worker during submit.
    """
    from sqlalchemy import select

    from app.models.tables import BrokerOrderSnapshot

    ids = [item for item in order_ids if item]
    if not ids:
        return {}
    found: dict[str, str] = {}
    rows = session.execute(
        select(
            BrokerOrderSnapshot.broker_order_id,
            BrokerOrderSnapshot.internal_state,
        )
        .where(
            BrokerOrderSnapshot.broker_order_id.in_(ids),
            BrokerOrderSnapshot.observed_at >= since,
        )
        .order_by(BrokerOrderSnapshot.observed_at.desc())
    )
    for broker_order_id, internal_state in rows:
        key = str(broker_order_id or "")
        if key and key not in found:
            found[key] = str(internal_state or "")
    return found


def _stored_losses(session, moment: datetime, minutes: int) -> list[LossStamp]:
    from datetime import time

    from sqlalchemy import select

    from app.analytics.trades import round_trips
    from app.models.tables import BrokerFillRecord, PaperTrade

    if minutes <= 0:
        return []
    day_start = datetime.combine(_ny_date(moment), time.min, tzinfo=_NY).astimezone(timezone.utc)
    payloads: list[dict] = []
    fills = session.scalars(
        select(BrokerFillRecord)
        .where(BrokerFillRecord.observed_at >= day_start)
        .order_by(BrokerFillRecord.observed_at.asc(), BrokerFillRecord.id.asc())
    )
    for row in fills:
        payload = dict(row.payload) if isinstance(row.payload, dict) else {}
        if row.symbol and not payload.get("symbol"):
            payload["symbol"] = row.symbol
        if row.execution_id and not payload.get("execution_id"):
            payload["execution_id"] = row.execution_id
        payloads.append(payload)
    closed = list(round_trips(payloads).get("closed") or [])
    for trade in session.scalars(select(PaperTrade).where(PaperTrade.exit_at.is_not(None), PaperTrade.exit_at >= day_start)):
        closed.append(
            {
                "symbol": trade.option_symbol,
                "pnl": trade.pnl_dollars,
                "closed_at": trade.exit_at,
            }
        )
    return losses_from_closed(closed, moment, minutes)


def _dead(status: object) -> bool:
    return str(status or "").strip().lower() in _DEAD


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _moment(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _ny_date(moment: datetime):
    return _aware(moment).astimezone(_NY).date()


def _in_window(at: datetime, moment: datetime, minutes: int) -> bool:
    """True when `at` is inside the rolling window and on the same New York date.

    A stamp exactly `minutes` ago has left the window. Yesterday does not count.
    """
    if minutes <= 0:
        return False
    stamp = _aware(at)
    now = _aware(moment)
    if stamp > now or stamp <= now - timedelta(minutes=minutes):
        return False
    return _ny_date(stamp) == _ny_date(now)
