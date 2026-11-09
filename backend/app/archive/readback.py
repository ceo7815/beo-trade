"""Paper 100K screens read the archive and stored fills. They do not call Alpaca."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.archive.job import _row_in_period, equity_points
from app.archive.tables import ArchiveAccount, ArchiveLocal, ArchivePosition, ArchiveRun
from app.models.db import database_ready, session_scope
from app.models.tables import BrokerFillRecord

PERIOD_ID = "paper_100k"


def is_paper_100k(period_id: str | None) -> bool:
    return (period_id or "").strip() == PERIOD_ID


def _fill_from_local(payload: dict) -> dict:
    inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    fill = dict(inner)
    for key in ("execution_id", "symbol", "broker_order_id", "trade_id"):
        if payload.get(key) and not fill.get(key):
            fill[key] = payload.get(key)
    return fill


def period_fill_payloads() -> list[dict]:
    """Fills that belong to paper_100k, from the live table and from the archive copy."""
    if not database_ready():
        return []
    payloads: list[dict] = []
    seen: set[str] = set()
    with session_scope() as session:
        rows = session.scalars(select(BrokerFillRecord).order_by(BrokerFillRecord.observed_at.asc(), BrokerFillRecord.id.asc())).all()
        for row in rows:
            if not _row_in_period(row, "observed_at"):
                continue
            payload = dict(row.payload) if isinstance(row.payload, dict) else {}
            if row.execution_id and not payload.get("execution_id"):
                payload["execution_id"] = row.execution_id
            if row.symbol and not payload.get("symbol"):
                payload["symbol"] = row.symbol
            if row.broker_order_id and not payload.get("broker_order_id"):
                payload["broker_order_id"] = row.broker_order_id
            key = str(payload.get("execution_id") or row.id)
            if key in seen:
                continue
            seen.add(key)
            payloads.append(payload)
        copies = session.scalars(select(ArchiveLocal).where(ArchiveLocal.source == "broker_fills")).all()
        for copy in copies:
            raw = copy.payload if isinstance(copy.payload, dict) else {}
            fill = _fill_from_local(raw)
            key = str(fill.get("execution_id") or copy.source_id)
            if not fill or key in seen:
                continue
            seen.add(key)
            payloads.append(fill)
    return payloads


def archived_account() -> dict | None:
    if not database_ready():
        return None
    with session_scope() as session:
        row = session.get(ArchiveAccount, "snapshot")
        if row is None:
            return None
        return {
            "equity": row.equity or None,
            "cash": row.cash or None,
            "last_equity": row.last_equity or None,
            "buying_power": row.cash or None,
            "portfolio_value": row.equity or None,
        }


def archived_positions() -> list[dict]:
    if not database_ready():
        return []
    with session_scope() as session:
        latest = session.scalar(select(ArchiveRun).order_by(ArchiveRun.started_at.desc()).limit(1))
        if latest is None:
            return []
        rows = session.scalars(select(ArchivePosition).where(ArchivePosition.run_id == latest.id)).all()
        return [dict(row.payload) for row in rows if isinstance(row.payload, dict)]


def archived_trade_report(settings, period: str, start: str, end: str) -> dict:
    from app.analytics.closed_trades import canonical_book
    from app.analytics.periods import public_periods, resolve_period
    from app.analytics.trades import summarize, window, within

    fills = period_fill_payloads()
    book = canonical_book([], fills, archived_positions(), settings.trading().contract_multiplier)
    closed = list(book["closed"])
    if period not in {"", "all", "paper_100k"}:
        frame = window(period, start, end, zone=settings.exchange_timezone)
        closed = [row for row in closed if within(row.get("closed_at"), frame)]
    summary = summarize(closed, [], None)
    meta = resolve_period(PERIOD_ID)
    return {
        "environment": "PAPER",
        "available": True,
        "source": "archive",
        "summary": summary,
        "closed": closed,
        "open": [],
        "orphan_sells": book.get("orphan_sells") or [],
        "paper_limitation": "ארכיון פייפר 100K. הנתונים נקראים מהמסד שלנו, בלי Alpaca.",
        "trading_period": None
        if meta is None
        else {
            "id": meta.id,
            "label": meta.label,
            "start": meta.start.isoformat(),
            "end": None if meta.end is None else meta.end.isoformat(),
        },
        "trading_periods": public_periods(),
    }


def archived_risk() -> dict:
    account = archived_account() or {}
    equity = account.get("equity")
    last = account.get("last_equity")
    pnl = None
    try:
        if equity not in {None, ""} and last not in {None, ""}:
            pnl = f"{(Decimal(str(equity)) - Decimal(str(last))):.2f}"
    except Exception:
        pnl = None
    return {
        "environment": "PAPER",
        "source": "archive",
        "state": "ארכיון",
        "per_trade_limit": None,
        "exposure_limit": None,
        "daily_loss_limit": None,
        "daily_kill_loss_usd": None,
        "daily_loss_used": None,
        "daily_loss_state": "archive",
        "today_pnl": pnl,
        "display_pnl": pnl,
        "day_pnl_note": "ארכיון. אין קריאה ל-Alpaca.",
        "max_positions": 0,
        "open_positions": len(archived_positions()),
        "day_floor": None,
        "protection": None,
    }


def archived_finance() -> dict:
    from app.analytics.finance import daily_rows, drawdown_from_equity, period_cards
    from app.analytics.periods import public_periods, resolve_period

    account = archived_account()
    points = []
    if database_ready():
        with session_scope() as session:
            for row in equity_points(session, "all"):
                if row.get("equity") in {None, ""}:
                    continue
                points.append({"timestamp": row["ts"], "equity": row["equity"], "profit_loss": row.get("day_pnl")})
    meta = resolve_period(PERIOD_ID)
    equity = None if account is None else account.get("equity")
    last = None if account is None else account.get("last_equity")
    pnl = None
    try:
        if equity not in {None, ""} and last not in {None, ""}:
            pnl = f"{(Decimal(str(equity)) - Decimal(str(last))):.2f}"
    except Exception:
        pnl = None
    return {
        "environment": "PAPER",
        "available": True,
        "source": "archive",
        "account": None
        if account is None
        else {
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "portfolio_value": account.get("portfolio_value"),
        },
        "today_pnl": pnl,
        "paper_limitation": "ארכיון פייפר 100K. הנתונים נקראים מהמסד שלנו, בלי Alpaca.",
        "open_positions": len(archived_positions()),
        "exposure": None,
        "history": {"points": points},
        "drawdown": drawdown_from_equity(points),
        "periods": period_cards(points),
        "trading_period": None
        if meta is None
        else {
            "id": meta.id,
            "label": meta.label,
            "start": meta.start.isoformat(),
            "end": None if meta.end is None else meta.end.isoformat(),
        },
        "trading_periods": public_periods(),
        "daily": daily_rows(points),
        "monthly": [],
        "trades": {"enough": False, "note": "ארכיון"},
        "by_side": {},
        "by_dte": {},
        "by_symbol": {},
        "sector": {"note": "ארכיון"},
        "best_worst": {"note": "ארכיון"},
    }
