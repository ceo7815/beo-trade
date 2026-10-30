"""Mark a trade as excluded after a system bug so it does not skew risk or reports.

Excluded lots still exit at the broker. They stay visible in the UI as a bug trade
with displayed P&L forced to zero. Day P&L for the kill switch, day floor, risk
screen, and learning stats omit both the unrealized and realized contribution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

BUG_REASON = "באג יציאה REPLACED — לא נספרת"
BUG_LABEL = "עסקת באג"

# The stuck META 630C lot from 29.10 (Dubai ~21:47 / 17:47 UTC), 7 contracts at 5.55.
# Earlier closed META 630C lots on 29.10 are not covered by the entry window.
SEED = {
    "symbol": "META261030C00630000",
    "quantity": 7,
    "entry_price": "5.55",
    "entry_after": "2026-10-29T17:30:00+00:00",
    "entry_before": "2026-10-30T04:00:00+00:00",
    "reason": BUG_REASON,
    # Filled sell 30.10 17:30:22 Dubai (13:30:22 UTC) at 1.06. Full round-trip
    # is about -$3,143; the NY session day piece in account P&L on 30.10 is the
    # amount below (today_pnl was -1,827 with this lot as the only mover).
    "exit_price": "1.06",
    "exit_at": "2026-10-30T13:30:22+00:00",
    "session_day_pnl": {"2026-10-30": "-1827.00"},
}


def ensure_seeded(session: Session) -> dict | None:
    """Idempotently insert the known bug lot and emit TRADE_EXCLUDED once."""
    from app.models.tables import TradeExclusion

    existing = session.scalar(
        select(TradeExclusion).where(
            TradeExclusion.symbol == SEED["symbol"],
            TradeExclusion.quantity == SEED["quantity"],
            TradeExclusion.entry_price == float(Decimal(SEED["entry_price"])),
            TradeExclusion.active.is_(True),
        )
    )
    if existing is not None:
        return _public_row(existing)
    row = TradeExclusion(
        symbol=SEED["symbol"],
        quantity=SEED["quantity"],
        entry_price=float(Decimal(SEED["entry_price"])),
        entry_after=_parse(SEED["entry_after"]),
        entry_before=_parse(SEED["entry_before"]),
        reason=SEED["reason"],
        active=True,
    )
    session.add(row)
    session.flush()
    _audit_once(session, SEED["symbol"], SEED["reason"])
    return _public_row(row)


def active_exclusions(session: Session | None = None) -> list[dict]:
    rows = _load(session)
    return [_public_row(row) for row in rows]


def matches_trade(row: dict, exclusions: Iterable[dict] | None = None) -> bool:
    """True when a position or closed/open round-trip is the excluded lot."""
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    if not rules:
        return False
    symbol = _symbol(row.get("symbol") or row.get("option_symbol"))
    if not symbol:
        return False
    qty = _int(row.get("qty") or row.get("quantity") or row.get("entry_fill_quantity"))
    entry = _decimal(row.get("avg_entry_price") or row.get("entry_price") or row.get("entry_fill_price"))
    opened = _parse(row.get("opened_at") or row.get("entry_time") or row.get("created_at"))
    for rule in rules:
        if not rule.get("active", True):
            continue
        if _symbol(rule.get("symbol")) != symbol:
            continue
        rule_qty = _int(rule.get("quantity"))
        if rule_qty is not None and qty is not None and rule_qty != qty:
            continue
        rule_entry = _decimal(rule.get("entry_price"))
        if rule_entry is not None and entry is not None and abs(rule_entry - entry) > Decimal("0.011"):
            continue
        after = _parse(rule.get("entry_after"))
        before = _parse(rule.get("entry_before"))
        if opened is not None:
            if after is not None and opened < after:
                continue
            if before is not None and opened > before:
                continue
        elif after is not None or before is not None:
            # Open broker rows often lack opened_at; symbol+qty+entry is enough.
            if rule_qty is None or entry is None or rule_entry is None:
                continue
        return True
    return False


def countable_positions(positions: list[dict], exclusions: Iterable[dict] | None = None) -> list[dict]:
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    return [row for row in positions if not matches_trade(row, rules)]


def excluded_open_rows(positions: list[dict], exclusions: Iterable[dict] | None = None) -> list[dict]:
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    found = []
    for row in positions:
        if not matches_trade(row, rules):
            continue
        found.append(present_excluded(row, open_row=True))
    return found


def adjust_day_pnl(
    measured: Decimal | None,
    positions: list[dict] | None,
    exclusions: Iterable[dict] | None = None,
    session_date=None,
) -> Decimal | None:
    """Remove excluded lots from account day P&L.

    Open lots: subtract unrealized_intraday_pl (else unrealized_pl).
    Closed seeded lots: subtract SEED session_day_pnl for that NY date when the
    lot is no longer in positions (so we do not double-count).
    """
    if measured is None:
        return None
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    adjusted = measured
    open_hit = False
    for row in positions or []:
        if not matches_trade(row, rules):
            continue
        open_hit = True
        piece = _decimal(row.get("unrealized_intraday_pl"))
        if piece is None:
            piece = _decimal(row.get("unrealized_pl") or row.get("unrealized_pnl"))
        if piece is None:
            continue
        adjusted -= piece
    if not open_hit:
        day = _session_key(session_date)
        seeded = SEED.get("session_day_pnl") or {}
        if day and day in seeded and _seed_active(rules):
            piece = _decimal(seeded[day])
            if piece is not None:
                adjusted -= piece
    return adjusted


def _session_key(session_date) -> str | None:
    if session_date is None:
        from datetime import date as date_cls

        try:
            from zoneinfo import ZoneInfo

            session_date = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
        except Exception:
            session_date = datetime.now(timezone.utc).date()
    if hasattr(session_date, "isoformat"):
        return session_date.isoformat()
    text = str(session_date).strip()
    return text[:10] if text else None


def _seed_active(rules: list[dict]) -> bool:
    want = _symbol(SEED["symbol"])
    for rule in rules:
        if not rule.get("active", True):
            continue
        if _symbol(rule.get("symbol")) == want:
            return True
    return False


def for_stats(rows: list[dict], exclusions: Iterable[dict] | None = None) -> list[dict]:
    """Closed/open rows that still count toward PF, win-rate, and day capital."""
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    return [row for row in rows if not matches_trade(row, rules)]


def annotate_display(row: dict, exclusions: Iterable[dict] | None = None) -> dict:
    """Keep the trade visible; force displayed P&L to zero and tag it as a bug trade."""
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    shown = dict(row)
    if not matches_trade(shown, rules):
        shown.setdefault("bug_trade", False)
        shown.setdefault("excluded", False)
        return shown
    return present_excluded(shown, open_row=bool(shown.get("unrealized_pnl") is not None or shown.get("unrealized_pl") is not None))


def present_excluded(row: dict, *, open_row: bool = False) -> dict:
    shown = dict(row)
    reason = str(shown.get("excluded_reason") or BUG_REASON)
    shown["bug_trade"] = True
    shown["excluded"] = True
    shown["excluded_reason"] = reason
    shown["bug_label"] = BUG_LABEL
    shown["pnl"] = "0.00"
    shown["gross_pnl"] = "0.00"
    shown["net_pnl"] = "0.00"
    shown["return_pct"] = "0.00"
    shown["unrealized_pnl"] = "0.00"
    shown["unrealized_pl"] = "0.00"
    shown["unrealized_plpc"] = "0"
    shown["unrealized_intraday_pl"] = "0.00"
    if open_row:
        shown["display_pnl"] = "0.00"
    return shown


def annotate_positions(items: list[dict], exclusions: Iterable[dict] | None = None) -> list[dict]:
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    return [annotate_display(row, rules) for row in items]


def annotate_trades(rows: list[dict], exclusions: Iterable[dict] | None = None) -> list[dict]:
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    return [annotate_display(row, rules) for row in rows]


def _load(session: Session | None) -> list:
    from app.models.tables import TradeExclusion
    from app.models.db import SessionLocal, database_ready

    if session is not None:
        return list(session.scalars(select(TradeExclusion).where(TradeExclusion.active.is_(True))))
    if not database_ready() or SessionLocal is None:
        return []
    with SessionLocal() as own:
        return list(own.scalars(select(TradeExclusion).where(TradeExclusion.active.is_(True))))


def _audit_once(session: Session, symbol: str, reason: str) -> None:
    from app.broker.store import record_trade_event
    from app.models.tables import BrokerTradeEvent

    trade_id = f"excluded:{symbol}"
    prior = session.scalar(
        select(BrokerTradeEvent).where(
            BrokerTradeEvent.trade_id == trade_id,
            BrokerTradeEvent.state == "TRADE_EXCLUDED",
        )
    )
    if prior is not None:
        return
    record_trade_event(session, trade_id, "TRADE_EXCLUDED", "beo-trade", reason)


def _public_row(row) -> dict:
    return {
        "id": getattr(row, "id", None),
        "symbol": row.symbol,
        "quantity": row.quantity,
        "entry_price": str(Decimal(str(row.entry_price)).quantize(Decimal("0.01"))),
        "entry_after": None if row.entry_after is None else row.entry_after.isoformat(),
        "entry_before": None if row.entry_before is None else row.entry_before.isoformat(),
        "reason": row.reason,
        "active": bool(row.active),
    }


def _symbol(value: object) -> str:
    return str(value or "").replace(" ", "").upper()


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(float(str(value)))
    except Exception:
        return None


def _parse(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
