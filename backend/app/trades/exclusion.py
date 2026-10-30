"""Mark trades as excluded after a system bug so they do not skew risk or reports.

Excluded lots still exit at the broker. They stay visible in the UI as a bug trade
with displayed P&L forced to zero. Day P&L for the kill switch, day floor, risk
screen, and learning stats omit each excluded lot's own session contribution —
never other symbols' P&L.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

BUG_REASON = "באג יציאה REPLACED — לא נספרת"
BUG_LABEL = "עסקת באג"
HARD_STOP_BUG_REASON = "באג DAILY_HARD_STOP — לא נספרת"

# Each seed is one excluded lot. excluded_day_impact is the account-day piece for
# overnight holds (equity last_equity → exit) when that cannot be split from fills.
# Same-day round-trips prefer (exit-entry)*qty*mult from the lot's own prices.
SEEDS = [
    {
        "symbol": "META261030C00630000",
        "quantity": 7,
        "entry_price": "5.55",
        "entry_after": "2026-10-29T17:30:00+00:00",
        "entry_before": "2026-10-30T04:00:00+00:00",
        "reason": BUG_REASON,
        "exit_price": "1.06",
        "exit_at": "2026-10-30T13:30:22+00:00",
        # Prior-close → exit account day piece (live today_pnl was -1827 with META alone).
        "excluded_day_impact": {"2026-10-30": "-1827.00"},
    },
    {
        "symbol": "NVDA261102P00205000",
        "quantity": 32,
        "entry_price": "1.51",
        "entry_after": "2026-10-30T13:50:00+00:00",
        "entry_before": "2026-10-30T13:55:00+00:00",
        "reason": HARD_STOP_BUG_REASON,
        "exit_price": "1.07",
        "exit_at": "2026-10-30T13:59:52+00:00",
        # (1.07-1.51)*32*100
        "excluded_day_impact": {"2026-10-30": "-1408.00"},
    },
    {
        "symbol": "SPY261102P00760000",
        "quantity": 15,
        "entry_price": "3.19",
        "entry_after": "2026-10-30T13:50:00+00:00",
        "entry_before": "2026-10-30T13:55:00+00:00",
        "reason": HARD_STOP_BUG_REASON,
        "exit_price": "2.58",
        "exit_at": "2026-10-30T13:59:53+00:00",
        # (2.58-3.19)*15*100
        "excluded_day_impact": {"2026-10-30": "-915.00"},
    },
]

# Back-compat for imports/tests that still reference a single SEED.
SEED = SEEDS[0]


def ensure_seeded(session: Session) -> list[dict]:
    """Idempotently insert every known bug lot and emit TRADE_EXCLUDED once each."""
    from app.models.tables import TradeExclusion

    out: list[dict] = []
    for seed in SEEDS:
        existing = session.scalar(
            select(TradeExclusion).where(
                TradeExclusion.symbol == seed["symbol"],
                TradeExclusion.quantity == seed["quantity"],
                TradeExclusion.entry_price == float(Decimal(seed["entry_price"])),
                TradeExclusion.active.is_(True),
            )
        )
        if existing is not None:
            out.append(_public_row(existing))
            continue
        row = TradeExclusion(
            symbol=seed["symbol"],
            quantity=seed["quantity"],
            entry_price=float(Decimal(seed["entry_price"])),
            entry_after=_parse(seed["entry_after"]),
            entry_before=_parse(seed["entry_before"]),
            reason=seed["reason"],
            active=True,
        )
        session.add(row)
        session.flush()
        _audit_once(session, seed["symbol"], seed["reason"])
        out.append(_public_row(row))
    return out


def active_exclusions(session: Session | None = None) -> list[dict]:
    rows = _load(session)
    return [_public_row(row) for row in rows]


def matches_trade(row: dict, exclusions: Iterable[dict] | None = None) -> bool:
    """True when a position or closed/open round-trip is an excluded lot."""
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
    closed_rows: list[dict] | None = None,
    activities: list | None = None,
    contract_multiplier: int = 100,
) -> Decimal | None:
    """Remove each excluded lot's own session contribution from account day P&L.

    Open lots: subtract that row's unrealized_intraday_pl (else unrealized_pl).
    Closed lots: only when session_date is set and the lot is evidenced closed
    that day (closed_rows, sell activities, or a seeded exit_at / day impact).
    Impact is always from that lot alone — never measured minus other symbols.
    """
    if measured is None:
        return None
    rules = list(exclusions) if exclusions is not None else active_exclusions()
    rules = [_enrich_rule(dict(rule)) for rule in rules]
    if not rules:
        return measured
    adjusted = measured
    open_excluded: set[str] = set()
    for row in positions or []:
        if not matches_trade(row, rules):
            continue
        open_excluded.add(_symbol(row.get("symbol") or row.get("option_symbol")))
        piece = _decimal(row.get("unrealized_intraday_pl"))
        if piece is None:
            piece = _decimal(row.get("unrealized_pl") or row.get("unrealized_pnl"))
        if piece is None:
            continue
        adjusted -= piece
    day = _as_date(session_date)
    if day is None:
        return adjusted
    rows = list(closed_rows) if closed_rows is not None else []
    if activities is not None:
        rows.extend(closed_rows_from_activities(activities, rules, day))
    # Do NOT invent closed impact from seeds alone — that would credit back losses
    # that are not inside measured (e.g. open-only marks, stream fixtures). Seed
    # day impact attaches when activities/closed_rows evidence the lot.
    seen: set[str] = set()
    for row in rows:
        sym = _symbol(row.get("symbol") or row.get("option_symbol"))
        if not sym or sym in open_excluded or sym in seen:
            continue
        if not matches_trade(row, rules):
            continue
        if not _closed_on_session(row, day) and not _seed_impact_for(row, rules, day):
            continue
        piece = _session_pnl_of_closed(row, day, contract_multiplier, rules)
        if piece is None:
            continue
        seen.add(sym)
        adjusted -= piece
    return adjusted


def closed_rows_from_activities(activities: list, rules: list[dict], session_date) -> list[dict]:
    """Minimal closed stubs for excluded lots with a sell fill on session_date."""
    rows: list[dict] = []
    for rule in rules:
        if not rule.get("active", True):
            continue
        symbol = _symbol(rule.get("symbol"))
        sell = _session_sell(activities, symbol, session_date)
        if sell is None:
            continue
        impact = _seed_impact_for(rule, rules, session_date)
        stub = {
            "symbol": symbol,
            "quantity": rule.get("quantity") or sell.get("qty") or sell.get("quantity"),
            "qty": rule.get("quantity") or sell.get("qty") or sell.get("quantity"),
            "entry_price": rule.get("entry_price"),
            "avg_entry_price": rule.get("entry_price"),
            "exit_price": sell.get("price") or sell.get("fill_price") or rule.get("exit_price"),
            "opened_at": rule.get("entry_after"),
            "closed_at": sell.get("timestamp") or sell.get("transaction_time") or sell.get("date") or rule.get("exit_at"),
        }
        if impact is not None:
            stub["day_pnl"] = str(impact)
        rows.append(stub)
    return rows


def closed_rows_from_seeds(rules: list[dict], session_date, open_excluded: set[str]) -> list[dict]:
    """Seeded lots with an exit / day impact on session_date and not currently open."""
    rows: list[dict] = []
    day_key = session_date.isoformat()
    for rule in rules:
        if not rule.get("active", True):
            continue
        sym = _symbol(rule.get("symbol"))
        if not sym or sym in open_excluded:
            continue
        impact = _decimal((rule.get("excluded_day_impact") or {}).get(day_key))
        exit_at = _parse(rule.get("exit_at"))
        from zoneinfo import ZoneInfo

        exited_today = exit_at is not None and exit_at.astimezone(ZoneInfo("America/New_York")).date() == session_date
        if impact is None and not exited_today:
            continue
        stub = {
            "symbol": sym,
            "quantity": rule.get("quantity"),
            "qty": rule.get("quantity"),
            "entry_price": rule.get("entry_price"),
            "avg_entry_price": rule.get("entry_price"),
            "exit_price": rule.get("exit_price"),
            "opened_at": rule.get("entry_after"),
            "closed_at": rule.get("exit_at") or f"{day_key}T20:00:00+00:00",
        }
        if impact is not None:
            stub["day_pnl"] = str(impact)
        rows.append(stub)
    return rows


def _session_sell(activities: list, symbol: str, session_date) -> dict | None:
    from zoneinfo import ZoneInfo

    want = _symbol(symbol)
    ny = ZoneInfo("America/New_York")
    for raw in activities or []:
        if not isinstance(raw, dict):
            continue
        sym = _symbol(raw.get("symbol") or raw.get("option_symbol"))
        if sym != want:
            continue
        side = str(raw.get("side") or raw.get("order_side") or "").lower()
        if side not in {"sell", "sell_to_close"}:
            continue
        stamp = _parse(raw.get("timestamp") or raw.get("transaction_time") or raw.get("date"))
        if stamp is None:
            continue
        if stamp.astimezone(ny).date() != session_date:
            continue
        return raw
    return None


def _as_date(session_date):
    if session_date is None:
        return None
    if hasattr(session_date, "year") and hasattr(session_date, "month") and not hasattr(session_date, "hour"):
        return session_date
    if hasattr(session_date, "astimezone"):
        from zoneinfo import ZoneInfo

        return session_date.astimezone(ZoneInfo("America/New_York")).date()
    text = str(session_date).strip()
    if not text:
        return None
    try:
        from datetime import date as date_cls

        return date_cls.fromisoformat(text[:10])
    except ValueError:
        return None


def _closed_on_session(row: dict, session_date) -> bool:
    closed = _parse(row.get("closed_at") or row.get("exit_time"))
    if closed is None:
        return False
    from zoneinfo import ZoneInfo

    return closed.astimezone(ZoneInfo("America/New_York")).date() == session_date


def _seed_impact_for(row: dict, rules: list[dict], session_date) -> Decimal | None:
    day_key = session_date.isoformat()
    sym = _symbol(row.get("symbol") or row.get("option_symbol"))
    for rule in rules:
        if _symbol(rule.get("symbol")) != sym:
            continue
        piece = _decimal((rule.get("excluded_day_impact") or {}).get(day_key))
        if piece is not None:
            return piece
    for seed in SEEDS:
        if _symbol(seed["symbol"]) != sym:
            continue
        piece = _decimal((seed.get("excluded_day_impact") or {}).get(day_key))
        if piece is not None:
            return piece
    return None


def _session_pnl_of_closed(row: dict, session_date, multiplier: int, rules: list[dict] | None = None) -> Decimal | None:
    """This closed lot's contribution to account day P&L for session_date only.

    Prefer explicit day fields, then same-day fill P&L from this lot alone, then
    the seeded overnight day-impact. Never invent impact from other symbols.
    """
    for key in ("session_pnl", "day_pnl", "realized_session_pnl", "unrealized_intraday_pl"):
        piece = _decimal(row.get(key))
        if piece is not None:
            return piece
    opened = _parse(row.get("opened_at") or row.get("entry_time") or row.get("created_at"))
    from zoneinfo import ZoneInfo

    open_day = None if opened is None else opened.astimezone(ZoneInfo("America/New_York")).date()
    closed_today = _closed_on_session(row, session_date)
    if open_day == session_date:
        # Same-day round-trip: full trade P&L from this lot's fills/prices.
        piece = _decimal(row.get("pnl") or row.get("net_pnl") or row.get("gross_pnl"))
        if piece is not None:
            return piece
        priced = _pnl_from_prices(row, multiplier)
        if priced is not None:
            return priced
    # Overnight (or missing prices): seeded account-day impact for this lot only.
    return _seed_impact_for(row, rules or [], session_date)


def _pnl_from_prices(row: dict, multiplier: int) -> Decimal | None:
    entry = _decimal(row.get("entry_price") or row.get("avg_entry_price") or row.get("entry_fill_price"))
    exit_px = _decimal(row.get("exit_price") or row.get("current_price"))
    qty = _int(row.get("qty") or row.get("quantity") or row.get("entry_fill_quantity"))
    if entry is None or exit_px is None or qty is None:
        return None
    return (exit_px - entry) * Decimal(qty) * Decimal(int(multiplier))


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
    """Record TRADE_EXCLUDED on audit_logs (not broker_trade_events)."""
    from app.models.tables import AuditLog

    entity_id = f"excluded:{symbol}"[:36]
    prior = session.scalar(
        select(AuditLog).where(
            AuditLog.action == "TRADE_EXCLUDED",
            AuditLog.entity == "trade_exclusion",
            AuditLog.entity_id == entity_id,
        )
    )
    if prior is not None:
        return
    session.add(
        AuditLog(
            actor="beo-trade",
            action="TRADE_EXCLUDED",
            entity="trade_exclusion",
            entity_id=entity_id,
            payload={"symbol": symbol, "reason": reason},
        )
    )


def _enrich_rule(rule: dict) -> dict:
    """Attach seed-only fields (exit, day impact) onto DB-backed exclusion rows."""
    sym = _symbol(rule.get("symbol"))
    for seed in SEEDS:
        if _symbol(seed["symbol"]) != sym:
            continue
        for key in ("exit_price", "exit_at", "excluded_day_impact", "reason"):
            if key in seed and key not in rule:
                rule[key] = seed[key]
            elif key == "excluded_day_impact" and seed.get(key):
                rule[key] = seed[key]
        break
    return rule


def _public_row(row) -> dict:
    body = {
        "id": getattr(row, "id", None),
        "symbol": row.symbol,
        "quantity": row.quantity,
        "entry_price": str(Decimal(str(row.entry_price)).quantize(Decimal("0.01"))),
        "entry_after": None if row.entry_after is None else row.entry_after.isoformat(),
        "entry_before": None if row.entry_before is None else row.entry_before.isoformat(),
        "reason": row.reason,
        "active": bool(row.active),
    }
    return _enrich_rule(body)


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
