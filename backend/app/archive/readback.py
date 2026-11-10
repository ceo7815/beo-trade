"""Paper 100K screens read the archive and stored fills. They do not call Alpaca."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.archive.job import _row_in_period, equity_points
from app.archive.tables import ArchiveAccount, ArchiveLocal, ArchivePosition, ArchiveRun
from app.models.db import database_ready, session_scope
from app.models.tables import BrokerFillRecord

PERIOD_ID = "paper_100k"
_CENT = Decimal("0.01")


def is_paper_100k(period_id: str | None) -> bool:
    return (period_id or "").strip() == PERIOD_ID


def _cash(value: Decimal) -> str:
    return f"{value.quantize(_CENT)}"


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


def load_equity_points() -> list[dict]:
    if not database_ready():
        return []
    with session_scope() as session:
        return [row for row in equity_points(session, "all") if row.get("equity") not in {None, ""}]


def sessions_from_points(points: list[dict]) -> list[dict]:
    """One equity level per New York session. A later point on the same session replaces the earlier one."""
    chosen: dict[str, dict] = {}
    for point in points:
        day = point.get("session_date")
        if not day or point.get("equity") in {None, ""}:
            continue
        chosen[str(day)] = point
    ordered = [chosen[day] for day in sorted(chosen)]
    rows: list[dict] = []
    previous: Decimal | None = None
    for point in ordered:
        equity = Decimal(str(point["equity"]))
        pnl = None if previous is None else equity - previous
        rows.append(
            {
                "date": point["session_date"],
                "start": None if previous is None else _cash(previous),
                "end": _cash(equity),
                "pnl": None if pnl is None else _cash(pnl),
                "equity": _cash(equity),
                "timestamp": point.get("ts"),
            }
        )
        previous = equity
    return rows


def _traded_sessions(sessions: list[dict]) -> list[dict]:
    return [row for row in sessions if row.get("pnl") not in {None, ""}]


def archive_result_from(sessions: list[dict]) -> dict | None:
    from app.analytics.periods import resolve_period

    if not sessions:
        return None
    meta = resolve_period(PERIOD_ID)
    final = Decimal(str(sessions[-1]["equity"]))
    basis = None if meta is None or meta.capital_basis_usd is None else meta.capital_basis_usd
    opened = basis if basis is not None else Decimal(str(sessions[0]["equity"]))
    pnl = final - opened
    pct = None if opened == 0 else (pnl / opened * Decimal("100")).quantize(_CENT)
    traded = _traded_sessions(sessions)
    return {
        "capital_basis_usd": None if basis is None else _cash(basis),
        "final_equity": _cash(final),
        "pnl": _cash(pnl),
        "return_pct": None if pct is None else str(pct),
        "start": traded[0]["date"] if traded else sessions[0]["date"],
        "end": traded[-1]["date"] if traded else sessions[-1]["date"],
        "sessions": len(traded) if traded else len(sessions),
        "read_only": True,
    }


def _equity_levels(sessions: list[dict]) -> list[dict]:
    levels = []
    for row in sessions:
        if row.get("timestamp") in {None, ""} or row.get("equity") in {None, ""}:
            continue
        levels.append({"timestamp": row["timestamp"], "equity": row["equity"]})
    return levels


def _last_session(sessions: list[dict]) -> tuple[str | None, str]:
    traded = _traded_sessions(sessions)
    if not traded:
        return None, "ארכיון · קריאה בלבד. אין נקודת הון ליום מסחר."
    last = traded[-1]
    return last["pnl"], f"ארכיון · קריאה בלבד. רווח והפסד של יום המסחר האחרון ({last['date']}), לא של היום."


def _account_gap(account: dict | None) -> str | None:
    if not account:
        return None
    equity = account.get("equity")
    last = account.get("last_equity")
    if equity in {None, ""} or last in {None, ""}:
        return None
    try:
        return _cash(Decimal(str(equity)) - Decimal(str(last)))
    except Exception:
        return None


def closed_book(settings) -> dict:
    from app.analytics.closed_trades import canonical_book

    fills = period_fill_payloads()
    book = canonical_book([], fills, archived_positions(), settings.trading().contract_multiplier)
    closed = list(book["closed"])
    closed.sort(key=lambda row: row.get("closed_at") or "", reverse=True)
    orphans = book.get("orphan_sells") or 0
    return {"closed": closed, "orphan_sells": orphans}


def closed_round_trips(settings) -> list[dict]:
    return closed_book(settings)["closed"]


_TRADE_FIELDS = (
    "symbol",
    "underlying",
    "right",
    "strike",
    "expiration",
    "dte_at_entry",
    "qty",
    "entry_price",
    "exit_price",
    "invested",
    "proceeds",
    "pnl",
    "return_pct",
    "outcome",
    "opened_at",
    "closed_at",
    "held_minutes",
    "entry_fills",
    "exit_fills",
    "exit_reason",
)


def public_round_trip(row: dict) -> dict:
    return {key: row.get(key) for key in _TRADE_FIELDS}


def closed_trade_page(settings, limit: int, offset: int) -> dict:
    closed = [public_round_trip(row) for row in closed_round_trips(settings)]
    return {
        "archive": PERIOD_ID,
        "total": len(closed),
        "limit": limit,
        "offset": offset,
        "items": closed[offset : offset + limit],
    }


def _parsed_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _monthly_from_trades(trades: list[dict]) -> list[dict]:
    from app.analytics.periods import session_day

    buckets: dict[str, Decimal] = {}
    for row in trades:
        day = session_day(row.get("closed_at"))
        pnl = row.get("pnl")
        if day is None or pnl in {None, ""}:
            continue
        key = day.strftime("%Y-%m")
        buckets[key] = buckets.get(key, Decimal("0")) + Decimal(str(pnl))
    return [{"month": key, "pnl": _cash(buckets[key])} for key in sorted(buckets)]


def _best_worst(trades: list[dict]) -> dict:
    ranked = [row for row in trades if row.get("pnl") not in {None, ""}]
    if len(ranked) < 3:
        return {"note": "אין מספיק מידע לניתוח"}
    ordered = sorted(ranked, key=lambda row: Decimal(str(row["pnl"])))
    return {"note": None, "worst_symbol": ordered[0].get("symbol"), "best_symbol": ordered[-1].get("symbol")}


def _trade_breakdown(
    settings,
    *,
    side: str = "",
    outcome: str = "",
    symbol: str = "",
    dte: str = "",
    start: str = "",
    end: str = "",
    exit_reason: str = "",
    hour: str = "",
) -> dict:
    from app.analytics.closed_trades import finance_row, finance_totals, ratio_return
    from app.analytics.finance import dte_bucket, filter_trades, split_by

    rows = []
    for trade in closed_round_trips(settings):
        shaped = finance_row(trade)
        if shaped.get("bucket") in {None, ""} and shaped.get("dte") is not None:
            shaped["bucket"] = dte_bucket(shaped.get("dte"))
        rows.append(shaped)
    filtered = filter_trades(
        rows,
        start=_parsed_time(start),
        end=_parsed_time(end),
        side=side,
        outcome=outcome,
        symbol=symbol,
        dte=dte,
        exit_reason=exit_reason,
        hour=hour,
    )
    grouped = [ratio_return(row) for row in filtered]
    return {
        "trades": finance_totals(filtered),
        "by_side": split_by(grouped, "right"),
        "by_dte": split_by([row for row in grouped if row.get("bucket")], "bucket"),
        "by_symbol": split_by(grouped, "underlying"),
        "best_worst": _best_worst(filtered),
        "monthly": _monthly_from_trades(filtered),
    }


def _period_meta() -> dict | None:
    from app.analytics.periods import resolve_period

    meta = resolve_period(PERIOD_ID)
    if meta is None:
        return None
    return {
        "id": meta.id,
        "label": meta.label,
        "start": meta.start.isoformat(),
        "end": None if meta.end is None else meta.end.isoformat(),
    }


def _curve(points: list[dict]) -> list[dict]:
    curve = []
    for row in points:
        if row.get("equity") in {None, ""}:
            continue
        curve.append(
            {
                "ts": row.get("ts"),
                "session_date": row.get("session_date"),
                "equity": row.get("equity"),
                "day_pnl": row.get("day_pnl"),
            }
        )
    return curve


def archived_trade_report(settings, period: str, start: str, end: str) -> dict:
    from app.analytics.periods import public_periods
    from app.analytics.trades import summarize, window, within

    book = closed_book(settings)
    closed = book["closed"]
    if period not in {"", "all", "paper_100k"}:
        frame = window(period, start, end, zone=settings.exchange_timezone)
        closed = [row for row in closed if within(row.get("closed_at"), frame)]
    summary = summarize(closed, [], None)
    points = load_equity_points()
    sessions = sessions_from_points(points)
    return {
        "environment": "PAPER",
        "available": True,
        "source": "archive",
        "read_only": True,
        "summary": summary,
        "closed": closed,
        "open": [],
        "orphan_sells": book["orphan_sells"],
        "paper_limitation": "ארכיון פייפר 100K. הנתונים נקראים מהמסד שלנו, בלי Alpaca.",
        "trading_period": _period_meta(),
        "trading_periods": public_periods(),
        "archive_result": archive_result_from(sessions),
        "equity_curve": _curve(points),
    }


def archived_risk() -> dict:
    account = archived_account() or {}
    sessions = sessions_from_points(load_equity_points())
    session_pnl, session_note = _last_session(sessions)
    if session_pnl is not None:
        pnl, note = session_pnl, session_note
    else:
        pnl = _account_gap(account)
        note = "ארכיון. אין קריאה ל-Alpaca." if pnl is not None else session_note
    return {
        "environment": "PAPER",
        "source": "archive",
        "read_only": True,
        "state": "ארכיון",
        "per_trade_limit": None,
        "exposure_limit": None,
        "daily_loss_limit": None,
        "daily_kill_loss_usd": None,
        "daily_loss_used": None,
        "daily_loss_state": "archive",
        "today_pnl": pnl,
        "display_pnl": pnl,
        "day_pnl_note": note,
        "max_positions": 0,
        "open_positions": len(archived_positions()),
        "day_floor": None,
        "protection": None,
        "archive_result": archive_result_from(sessions),
    }


def archived_finance(
    settings=None,
    *,
    side: str = "",
    outcome: str = "",
    symbol: str = "",
    dte: str = "",
    start: str = "",
    end: str = "",
    exit_reason: str = "",
    hour: str = "",
) -> dict:
    from app.analytics.finance import drawdown_from_equity, period_cards
    from app.analytics.periods import public_periods

    account = archived_account()
    points = load_equity_points()
    sessions = sessions_from_points(points)
    levels = _equity_levels(sessions)
    cards = period_cards(levels)
    result = archive_result_from(sessions)
    if result is not None and result.get("pnl") not in {None, ""}:
        cards["cumulative"] = result["pnl"]
    daily = _traded_sessions(sessions)
    today, note = _last_session(sessions)
    breakdown = {
        "trades": {"enough": False, "note": "ארכיון"},
        "by_side": {},
        "by_dte": {},
        "by_symbol": {},
        "best_worst": {"note": "ארכיון"},
        "monthly": [],
    }
    if settings is not None:
        breakdown = _trade_breakdown(
            settings,
            side=side,
            outcome=outcome,
            symbol=symbol,
            dte=dte,
            start=start,
            end=end,
            exit_reason=exit_reason,
            hour=hour,
        )
    history = []
    for row in points:
        try:
            equity_value = float(row["equity"])
        except (TypeError, ValueError):
            continue
        history.append(
            {
                "timestamp": row.get("ts"),
                "equity": equity_value,
                "session_date": row.get("session_date"),
            }
        )
    return {
        "environment": "PAPER",
        "available": True,
        "source": "archive",
        "read_only": True,
        "account": None
        if account is None
        else {
            "equity": account.get("equity"),
            "cash": account.get("cash"),
            "buying_power": account.get("buying_power"),
            "portfolio_value": account.get("portfolio_value"),
        },
        "today_pnl": today,
        "day_pnl_note": note,
        "paper_limitation": "ארכיון פייפר 100K. הנתונים נקראים מהמסד שלנו, בלי Alpaca.",
        "open_positions": len(archived_positions()),
        "exposure": None,
        "history": {"points": history},
        "drawdown": drawdown_from_equity(levels),
        "periods": cards,
        "trading_period": _period_meta(),
        "trading_periods": public_periods(),
        "daily": [{"date": row["date"], "start": row["start"], "end": row["end"], "pnl": row["pnl"]} for row in daily],
        "monthly": breakdown["monthly"],
        "trades": breakdown["trades"],
        "by_side": breakdown["by_side"],
        "by_dte": breakdown["by_dte"],
        "by_symbol": breakdown["by_symbol"],
        "sector": {"note": "נתוני סקטור אינם זמינים"},
        "best_worst": breakdown["best_worst"],
        "archive_result": result,
    }
