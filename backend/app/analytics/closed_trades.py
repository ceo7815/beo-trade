"""Canonical closed trades for finance, history, days, and reports.

A closed trade is one option held from flat back to flat. `round_trips`
pairs every fill. That is the book the history screen, the day lists, and
`/api/v1/report/trades` already used, and it is the book that matched the
broker (the audit on 27.10.2026: −2,888 over 155 trades).

Finance used to call `_stored_closed`, which grouped `broker_fills` by
trade id and priced only the first buy and the last sell. A second round
trip on the same contract collapsed into the first one, so the screen
showed a different count and a different P&L (+4,963 over 138 trades).

The canonical fill set is the union of those stored rows and the live
Alpaca paper FILL activities. The same execution keeps the Alpaca price
and size. A fill Alpaca no longer returns stays in the book. Finance,
history, the day lists, and the report all read the closed trades built
from that union.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog

from app.analytics.finance import dte_bucket, summarize_trades
from app.analytics.trades import round_trips, summarize, window, within
from app.models.db import database_ready, session_scope

log = structlog.get_logger()
_DUBAI = ZoneInfo("Asia/Dubai")
_CENT = Decimal("0.01")
_RATIO = Decimal("0.0001")


def _moment(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _identity(row: dict) -> str:
    execution_id = str(row.get("execution_id") or "").strip()
    if execution_id:
        return "id:" + execution_id
    symbol = str(row.get("symbol") or "").replace(" ", "").upper()
    side = str(row.get("side") or "").lower()
    stamp = str(row.get("timestamp") or "")[:19]
    return f"fp:{symbol}|{side}|{row.get('qty')}|{row.get('price')}|{stamp}"


def merge_fills(stored: list[dict], live: list[dict]) -> list[dict]:
    """Union of stored fills and Alpaca fills. The live row wins on the same execution."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for row in stored:
        key = _identity(row)
        if key not in merged:
            order.append(key)
        merged[key] = dict(row)
    for row in live:
        key = _identity(row)
        if key not in merged:
            order.append(key)
            merged[key] = dict(row)
            continue
        replacement = dict(row)
        previous = merged[key]
        if previous.get("trade_id") and not replacement.get("trade_id"):
            replacement["trade_id"] = previous["trade_id"]
        merged[key] = replacement
    return [merged[key] for key in order]


def stored_fill_payloads() -> list[dict]:
    """Fills already reconciled into `broker_fills`. Empty when the database is down."""
    if not database_ready():
        return []
    from sqlalchemy import select

    from app.models.tables import BrokerFillRecord

    with session_scope() as session:
        rows = session.scalars(select(BrokerFillRecord).order_by(BrokerFillRecord.observed_at.asc(), BrokerFillRecord.id.asc()))
        payloads = []
        for row in rows:
            payload = dict(row.payload) if isinstance(row.payload, dict) else {}
            if row.execution_id and not payload.get("execution_id"):
                payload["execution_id"] = row.execution_id
            if row.symbol and not payload.get("symbol"):
                payload["symbol"] = row.symbol
            if row.broker_order_id and not payload.get("broker_order_id"):
                payload["broker_order_id"] = row.broker_order_id
            payloads.append(payload)
        return payloads


def _held_symbols(positions: list[dict] | None) -> set[str]:
    return {str(row.get("symbol") or "").replace(" ", "").upper() for row in positions or [] if row.get("symbol")}


def canonical_book(
    live_fills: list[dict],
    stored_fills: list[dict] | None = None,
    positions: list[dict] | None = None,
    multiplier: int = 100,
    now: datetime | None = None,
) -> dict:
    """Closed and open round-trips from stored fills reconciled with Alpaca fills.

    Open rows that exist only because a stored buy never came back from Alpaca,
    and that the broker does not hold, stay out of the open list. Closed trades
    from those stored fills stay in the book.
    """
    stored = list(stored_fills or [])
    live = list(live_fills or [])
    report = round_trips(merge_fills(stored, live), positions, now=now, multiplier=multiplier)
    if stored:
        live_open = {row["symbol"] for row in round_trips(live, positions, now=now, multiplier=multiplier)["open"]}
        held = _held_symbols(positions)
        report["open"] = [row for row in report["open"] if row["symbol"] in held or row["symbol"] in live_open]
    report["source"] = "broker_fills reconciled with alpaca-paper FILL"
    return report


def finance_row(trade: dict) -> dict:
    """One canonical closed trade in the shape the finance filters already expect."""
    moment = _moment(trade.get("closed_at"))
    dte = trade.get("dte_at_entry")
    if not isinstance(dte, int):
        dte = trade.get("dte") if isinstance(trade.get("dte"), int) else None
    pnl = trade.get("pnl")
    return {
        "symbol": trade.get("symbol") or "",
        "underlying": trade.get("underlying"),
        "right": trade.get("right"),
        "pnl": pnl,
        "gross_pnl": pnl,
        "net_pnl": pnl,
        "return_pct": trade.get("return_pct"),
        "closed_at": moment,
        "opened_at": trade.get("opened_at"),
        "dte": dte,
        "bucket": dte_bucket(dte),
        "exit_reason": trade.get("exit_reason") or "",
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("exit_price"),
        "qty": trade.get("qty"),
        "invested": trade.get("invested") or "0.00",
        "held_minutes": trade.get("held_minutes"),
        "hour": None if moment is None else moment.astimezone(_DUBAI).strftime("%H"),
        "outcome": trade.get("outcome"),
    }


def public_closed(row: dict) -> dict:
    moment = row.get("closed_at")
    shown = dict(row)
    if isinstance(moment, datetime):
        shown["closed_at"] = moment.isoformat()
    return shown


def ratio_return(row: dict) -> dict:
    """`summarize_trades` reads return as a fraction. The book stores a percent."""
    copy = dict(row)
    percent = copy.get("return_pct")
    if percent in {None, ""}:
        return copy
    try:
        copy["return_pct"] = str((Decimal(str(percent)) / Decimal("100")).quantize(_RATIO))
    except Exception:
        return copy
    return copy


def finance_totals(closed: list[dict]) -> dict:
    """P&L, count, win rate, and profit factor from the same math as history.

    `closed` rows are canonical round-trips (`pnl`, `invested`, percent `return_pct`).
    Win rate on `win_rate` stays a fraction so the finance screen can keep
    multiplying by 100. `win_rate_pct` and `profit_factor` match history.
    """
    if not closed:
        empty = summarize_trades([])
        empty["gross"] = "0.00"
        empty["net"] = "0.00"
        empty["realized_pnl"] = "0.00"
        empty["closed_count"] = 0
        empty["win_rate"] = None
        empty["win_rate_pct"] = None
        empty["profit_factor"] = None
        empty["wins"] = 0
        empty["losses"] = 0
        return empty
    stats = summarize(closed, [])
    body = summarize_trades([ratio_return(row) for row in closed])
    rate = stats["win_rate_pct"]
    body["count"] = stats["closed_count"]
    body["closed_count"] = stats["closed_count"]
    body["gross"] = stats["realized_pnl"]
    body["net"] = stats["realized_pnl"]
    body["realized_pnl"] = stats["realized_pnl"]
    body["wins"] = stats["wins"]
    body["losses"] = stats["losses"]
    body["win_rate_pct"] = rate
    body["win_rate"] = None if rate is None else str((Decimal(rate) / Decimal("100")).quantize(_RATIO))
    body["profit_factor"] = stats["profit_factor"]
    return body


def _realized(rows: list[dict]) -> Decimal:
    return sum((Decimal(str(row["pnl"])) for row in rows if row.get("pnl") not in {None, ""}), Decimal("0"))


def reconcile_closed(
    canonical: list[dict],
    alpaca_fills: list[dict],
    *,
    start: str = "",
    end: str = "",
    zone: str = "America/New_York",
    multiplier: int = 100,
) -> dict:
    """Canonical closed-trade P&L against closed trades built only from Alpaca fills.

    `count_diff` and `usd_diff` are canonical minus Alpaca. A missing trade in
    the canonical book is a negative count diff and a non-zero dollar diff
    when that trade made or lost money.
    """
    frame = window("range" if start or end else "all", start, end, zone=zone)
    alpaca_closed = [row for row in round_trips(alpaca_fills, multiplier=multiplier)["closed"] if within(row.get("closed_at"), frame)]
    chosen = [row for row in canonical if within(row.get("closed_at"), frame)]
    canonical_pnl = _realized(chosen)
    alpaca_pnl = _realized(alpaca_closed)
    count_diff = len(chosen) - len(alpaca_closed)
    usd_diff = canonical_pnl - alpaca_pnl
    return {
        "mismatch": count_diff != 0 or usd_diff != 0,
        "count_diff": count_diff,
        "usd_diff": str(usd_diff.quantize(_CENT)),
        "canonical": {"count": len(chosen), "realized_pnl": str(canonical_pnl.quantize(_CENT))},
        "alpaca": {"count": len(alpaca_closed), "realized_pnl": str(alpaca_pnl.quantize(_CENT))},
        "start": frame["start"],
        "end": frame["end"],
    }


def warn_mismatch(result: dict) -> dict:
    if result.get("mismatch"):
        log.warning(
            "closed_trade_reconciliation_mismatch",
            count_diff=result.get("count_diff"),
            usd_diff=result.get("usd_diff"),
            start=result.get("start"),
            end=result.get("end"),
        )
    return result
