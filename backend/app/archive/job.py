"""Idempotent copy of the Paper 100K account into archive tables.

Reads Alpaca with GET only. Local journal rows for the paper_100k period are
copied too. Nothing in this module deletes archive rows or sends an order.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db import session_scope

from app.analytics.periods import get_period, session_day
from app.archive.tables import (
    ArchiveAccount,
    ArchiveActivity,
    ArchiveEquity,
    ArchiveLocal,
    ArchiveOrder,
    ArchivePosition,
    ArchiveRun,
)
from app.models.tables import (
    BrokerActivityRecord,
    BrokerFillRecord,
    BrokerOrderRecord,
    BrokerOrderSnapshot,
    BrokerTrade,
    BrokerTradeEvent,
    PaperPosition,
    PaperTrade,
    PositionState,
    TradeOutcome,
)

PERIOD_ID = "paper_100k"
EXPECTED_LAST4 = "NXN0"
LOCAL_BATCH_SIZE = 500
STALE_RUN_AFTER = timedelta(minutes=15)
_start_lock = threading.Lock()
_owned_runs: set[str] = set()
EQUITY_PERIODS = ("all", "1A", "6M", "3M", "1M", "1W", "1D")
_ACCOUNT_DROP = {"id", "account_number"}
# Fills first. A later table must not stop the journal copy, and each table commits alone.
_LOCAL_SOURCES = (
    (BrokerFillRecord, "broker_fills", "observed_at", "id"),
    (BrokerActivityRecord, "broker_activities", "observed_at", "id"),
    (BrokerTrade, "broker_trades", "created_at", "id"),
    (BrokerTradeEvent, "broker_trade_events", "created_at", "id"),
    (PaperTrade, "paper_trades", "entry_at", "id"),
    (PaperPosition, "paper_positions", "opened_at", "id"),
    (TradeOutcome, "trade_outcomes", "observed_at", "id"),
    (PositionState, "position_states", "entry_time", "trade_id"),
    (BrokerOrderRecord, "broker_orders", "created_at", "id"),
    (BrokerOrderSnapshot, "broker_order_snapshots", "observed_at", "id"),
)
_PAYLOAD_TIMES = ("timestamp", "filled_at", "transaction_time", "submitted_at", "created_at", "date")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def row_payload(row) -> dict:
    data = {}
    for column in row.__table__.columns:
        data[column.name] = _jsonable(getattr(row, column.name))
    return data


def redact_account(raw: dict) -> tuple[dict, str]:
    number = _text(raw.get("account_number"))
    last4 = number[-4:] if len(number) >= 4 else ""
    payload = {}
    for key, value in raw.items():
        lowered = key.lower()
        if key in _ACCOUNT_DROP or any(word in lowered for word in ("key", "secret", "token", "password")):
            continue
        payload[key] = value
    payload["account_last4"] = last4
    return payload, last4


def activity_key(row: dict) -> str:
    raw = _text(row.get("id") or row.get("activity_id")).strip()
    if raw:
        return raw[:80]
    blob = json.dumps(row, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:80]


def _coerce_moment(value):
    """SQLite stores UTC wall time as ``2026-10-07 15:04:00.000000`` or with ``+00:00``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _in_period(moment) -> bool:
    period = get_period(PERIOD_ID)
    if period is None:
        return False
    day = session_day(_coerce_moment(moment))
    if day is None:
        return False
    return period.contains_day(day)


def _row_in_period(row, stamp_attr: str) -> bool:
    """Keep the row when the column or the payload trade time falls in paper_100k.

    ``observed_at`` is when we stored the fill. A fill synced after 2026-11-02
    still belongs in the archive when the trade itself is inside the period.
    """
    moments = [getattr(row, stamp_attr, None)]
    payload = getattr(row, "payload", None)
    if isinstance(payload, dict):
        moments.extend(payload.get(key) for key in _PAYLOAD_TIMES)
    return any(_in_period(moment) for moment in moments)


def _iso(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _pull_account(session: Session, reader, moment: datetime) -> None:
    raw = reader.account() or {}
    payload, last4 = redact_account(raw if isinstance(raw, dict) else {})
    row = session.get(ArchiveAccount, "snapshot")
    if row is None:
        row = ArchiveAccount(id="snapshot")
        session.add(row)
    row.equity = _text(raw.get("equity"))
    row.cash = _text(raw.get("cash"))
    row.last_equity = _text(raw.get("last_equity"))
    row.account_last4 = last4
    row.payload = payload
    row.archived_at = moment
    session.commit()


def _pull_positions(session: Session, reader, run_id: str, moment: datetime) -> int:
    rows = reader.positions() or []
    kept = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        session.add(
            ArchivePosition(
                run_id=run_id,
                symbol=_text(raw.get("symbol"))[:64],
                payload=raw,
                archived_at=moment,
            )
        )
        kept += 1
    session.commit()
    return kept


def _pull_orders(session: Session, reader, moment: datetime, page_size: int) -> int:
    seen: set[str] = set()
    until: str | None = None
    pages = 0
    while pages < 500:
        page = reader.orders_page(until=until, limit=page_size) or []
        pages += 1
        if not page:
            break
        fresh = 0
        oldest = ""
        for raw in page:
            if not isinstance(raw, dict):
                continue
            order_id = _text(raw.get("id")).strip()
            submitted = _text(raw.get("submitted_at"))
            if submitted and (not oldest or submitted < oldest):
                oldest = submitted
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            fresh += 1
            row = session.get(ArchiveOrder, order_id)
            if row is None:
                row = ArchiveOrder(alpaca_order_id=order_id)
                session.add(row)
            row.status = _text(raw.get("status"))[:32]
            row.symbol = _text(raw.get("symbol"))[:64]
            row.submitted_at = submitted[:40]
            row.payload = raw
            row.archived_at = moment
        session.commit()
        session.expunge_all()
        if fresh == 0 or len(page) < page_size or not oldest or oldest == until:
            break
        until = oldest
    return len(seen)


def _pull_activities(session: Session, reader, moment: datetime, page_size: int) -> int:
    seen: set[str] = set()
    token: str | None = None
    pages = 0
    while pages < 2000:
        page = reader.activities_page(page_token=token, limit=page_size) or []
        pages += 1
        if not page:
            break
        fresh = 0
        last_key = ""
        for raw in page:
            if not isinstance(raw, dict):
                continue
            key = activity_key(raw)
            last_key = key
            if key in seen:
                continue
            seen.add(key)
            fresh += 1
            row = session.get(ArchiveActivity, key)
            if row is None:
                row = ArchiveActivity(activity_id=key)
                session.add(row)
            row.activity_type = _text(raw.get("activity_type"))[:32]
            row.payload = raw
            row.archived_at = moment
        session.commit()
        session.expunge_all()
        if fresh == 0 or len(page) < page_size or not last_key or last_key == token:
            break
        token = last_key
    return len(seen)


def _pull_equity(session: Session, reader, moment: datetime) -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = {}
    errors: list[str] = []
    for period in EQUITY_PERIODS:
        try:
            body = reader.portfolio_history(period, "1D") or {}
        except Exception as exc:
            errors.append(f"{period}:{type(exc).__name__}")
            counts[period] = 0
            continue
        stamps = body.get("timestamp") or []
        equities = body.get("equity") or []
        profits = body.get("profit_loss") or []
        pcts = body.get("profit_loss_pct") or []
        kept = 0
        for index, stamp in enumerate(stamps):
            try:
                ts = int(stamp)
            except (TypeError, ValueError):
                continue
            existing = session.scalar(
                select(ArchiveEquity).where(
                    ArchiveEquity.period == period,
                    ArchiveEquity.timeframe == "1D",
                    ArchiveEquity.ts == ts,
                )
            )
            equity = equities[index] if index < len(equities) else None
            profit = profits[index] if index < len(profits) else None
            pct = pcts[index] if index < len(pcts) else None
            if existing is None:
                session.add(
                    ArchiveEquity(
                        period=period,
                        timeframe="1D",
                        ts=ts,
                        equity=_text(equity),
                        profit_loss=_text(profit),
                        profit_loss_pct=_text(pct),
                        archived_at=moment,
                    )
                )
            else:
                existing.equity = _text(equity)
                existing.profit_loss = _text(profit)
                existing.profit_loss_pct = _text(pct)
                existing.archived_at = moment
            kept += 1
        counts[period] = kept
        session.commit()
    return counts, errors


def _as_utc(value) -> datetime | None:
    moment = _coerce_moment(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _touch_run(session: Session, run_id: str) -> None:
    row = session.get(ArchiveRun, run_id)
    if row is not None and row.status == "running":
        row.started_at = _now()


def _pull_local_source(session: Session, spec, moment: datetime, *, batch_size: int = LOCAL_BATCH_SIZE, run_id: str = "") -> int:
    """Keyset pages. Each batch is committed and dropped so the identity map cannot grow to the whole table."""
    model, source, stamp_attr, id_attr = spec
    key = getattr(model, id_attr)
    kept = 0
    last_id = ""
    size = max(1, int(batch_size))
    while True:
        query = select(model).order_by(key.asc()).limit(size)
        if last_id:
            query = query.where(key > last_id)
        rows = session.scalars(query).all()
        if not rows:
            break
        for row in rows:
            source_id = _text(getattr(row, id_attr, "")).strip()
            if source_id > last_id:
                last_id = source_id
            if not source_id or not _row_in_period(row, stamp_attr):
                continue
            existing = session.scalar(select(ArchiveLocal).where(ArchiveLocal.source == source, ArchiveLocal.source_id == source_id[:64]))
            payload = row_payload(row)
            if existing is None:
                session.add(ArchiveLocal(source=source, source_id=source_id[:64], payload=payload, archived_at=moment))
            else:
                existing.payload = payload
                existing.archived_at = moment
            kept += 1
        if run_id:
            _touch_run(session, run_id)
        session.commit()
        session.expunge_all()
        if len(rows) < size or not last_id:
            break
    return kept


def _pull_local(session: Session, moment: datetime, *, batch_size: int = LOCAL_BATCH_SIZE, run_id: str = "") -> tuple[int, list[str]]:
    """Copy each journal table on its own. One bad table does not drop the fills."""
    kept = 0
    errors: list[str] = []
    for spec in _LOCAL_SOURCES:
        try:
            kept += _pull_local_source(session, spec, moment, batch_size=batch_size, run_id=run_id)
        except Exception as exc:
            errors.append(f"{spec[1]}:{type(exc).__name__}")
            session.rollback()
    return kept, errors


def recover_stale_runs(session: Session, *, now: datetime | None = None, stale_after: timedelta = STALE_RUN_AFTER, own_ids: set[str] | None = None) -> int:
    """Close runs this process is not actively finishing.

    A run still owned by this process and touched within ``stale_after`` stays
    ``running``. Everything else left in ``running`` is a killed request.
    """
    moment = now or _now()
    cutoff = moment - stale_after
    owned = _owned_runs if own_ids is None else own_ids
    rows = session.scalars(select(ArchiveRun).where(ArchiveRun.status == "running")).all()
    changed = 0
    for row in rows:
        started = _as_utc(row.started_at)
        fresh = started is not None and started >= cutoff
        if row.id in owned and fresh:
            continue
        row.status = "failed"
        row.detail = "interrupted"
        row.finished_at = moment
        changed += 1
    if changed:
        session.commit()
    return changed


def _persist_run(session: Session, run_id: str, status: str, detail: str, counts: dict) -> None:
    """Write the run row even after an earlier rollback left the session unusable."""
    try:
        session.rollback()
    except Exception:
        pass
    row = session.get(ArchiveRun, run_id)
    if row is None:
        return
    row.status = status
    row.detail = (detail or "")[:500]
    row.finished_at = _now()
    row.counts = counts or {}
    session.commit()


def _public_run(row: ArchiveRun) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "detail": row.detail or "",
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


def _fresh_running(session: Session) -> ArchiveRun | None:
    return session.scalar(select(ArchiveRun).where(ArchiveRun.status == "running").order_by(ArchiveRun.started_at.desc()).limit(1))


def run_paper_100k_archive(
    session: Session,
    reader,
    *,
    order_page_size: int = 500,
    activity_page_size: int = 100,
    local_batch_size: int = LOCAL_BATCH_SIZE,
    run_id: str | None = None,
) -> dict:
    """Copy the account. ``reader`` None archives local rows only.

    Pass ``run_id`` when the row was already inserted and this call only does the work.
    """
    recover_stale_runs(session)
    if run_id is None:
        active = _fresh_running(session)
        if active is not None:
            summary = archive_summary(session)
            summary["run"] = _public_run(active)
            summary["last_run"] = summary["run"]
            summary["started"] = False
            summary["already_running"] = True
            summary["ok"] = False
            return summary
        moment = _now()
        run = ArchiveRun(started_at=moment, status="running")
        session.add(run)
        session.commit()
        run_id = run.id
    _owned_runs.add(run_id)
    try:
        return _execute_archive(
            session,
            reader,
            run_id,
            order_page_size=order_page_size,
            activity_page_size=activity_page_size,
            local_batch_size=local_batch_size,
        )
    finally:
        _owned_runs.discard(run_id)


def _execute_archive(
    session: Session,
    reader,
    run_id: str,
    *,
    order_page_size: int,
    activity_page_size: int,
    local_batch_size: int,
) -> dict:
    moment = _now()
    errors: list[str] = []
    if reader is None:
        errors.append("alpaca:not_configured")
    else:
        for name, pull in (
            ("account", lambda: _pull_account(session, reader, moment)),
            ("positions", lambda: _pull_positions(session, reader, run_id, moment)),
            ("orders", lambda: _pull_orders(session, reader, moment, order_page_size)),
            ("activities", lambda: _pull_activities(session, reader, moment, activity_page_size)),
        ):
            try:
                pull()
            except Exception as exc:
                errors.append(f"{name}:{type(exc).__name__}")
                session.rollback()
        equity_warnings: list[str] = []
        try:
            _counts, equity_warnings = _pull_equity(session, reader, moment)
            if equity_warnings and not any(_counts.values()):
                errors.append("equity:unavailable")
        except Exception as exc:
            equity_warnings = []
            errors.append(f"equity:{type(exc).__name__}")
            session.rollback()
    local_errors: list[str] = []
    try:
        _kept, local_errors = _pull_local(session, moment, batch_size=local_batch_size, run_id=run_id)
    except Exception as exc:
        local_errors = [f"local:{type(exc).__name__}"]
        session.rollback()
    errors.extend(local_errors)
    if reader is not None and equity_warnings:
        errors.extend(f"equity_skip:{item}" for item in equity_warnings)
    summary: dict = {"counts": {}}
    try:
        summary = archive_summary(session)
    except Exception as exc:
        errors.append(f"summary:{type(exc).__name__}")
        session.rollback()
    counts = summary.get("counts") or {}
    hard = [item for item in errors if not str(item).startswith("equity_skip:")]
    if reader is None and hard == ["alpaca:not_configured"]:
        status = "local_only"
    elif not hard:
        status = "ok"
    elif counts.get("orders") or counts.get("local_rows") or counts.get("activities"):
        status = "partial"
    else:
        status = "failed"
    detail = ",".join(errors)
    _persist_run(session, run_id, status, detail, counts)
    finished = session.get(ArchiveRun, run_id)
    summary["run"] = _public_run(finished) if finished is not None else {"id": run_id, "status": status, "detail": detail[:500], "finished_at": None}
    summary["counts"] = counts or summary.get("counts") or {}
    summary["ok"] = status in {"ok", "local_only", "partial"}
    summary["last_run"] = summary["run"]
    summary["started"] = True
    summary["already_running"] = False
    return summary


def finish_archive_run(settings, run_id: str) -> None:
    """Background entry. Builds its own reader and session so the request can return."""
    from app.archive.routes import build_reader
    from app.archive.reader import ArchiveNotConfigured

    reader = None
    try:
        try:
            reader = build_reader(settings)
        except ArchiveNotConfigured:
            reader = None
        with session_scope() as session:
            _execute_archive(
                session,
                reader,
                run_id,
                order_page_size=500,
                activity_page_size=100,
                local_batch_size=LOCAL_BATCH_SIZE,
            )
    except Exception:
        try:
            with session_scope() as session:
                _persist_run(session, run_id, "failed", "interrupted", {})
        except Exception:
            return
    finally:
        _owned_runs.discard(run_id)
        if reader is not None and hasattr(reader, "close"):
            reader.close()


def start_paper_100k_archive(settings) -> dict:
    """Insert a run row and return. The copy continues on a daemon thread."""
    from app.models.db import session_scope

    with _start_lock:
        with session_scope() as session:
            recover_stale_runs(session)
            active = _fresh_running(session)
            if active is not None:
                return {"started": False, "already_running": True, "run": _public_run(active)}
            run = ArchiveRun(started_at=_now(), status="running", detail="")
            session.add(run)
            session.commit()
            run_id = run.id
            _owned_runs.add(run_id)
        threading.Thread(
            target=finish_archive_run,
            args=(settings, run_id),
            name="archive-paper-100k",
            daemon=True,
        ).start()
        return {
            "started": True,
            "already_running": False,
            "run": {"id": run_id, "status": "running", "detail": "", "started_at": _iso(_now()), "finished_at": None},
        }


def archive_summary(session: Session) -> dict:
    account = session.get(ArchiveAccount, "snapshot")
    orders = session.scalar(select(func.count()).select_from(ArchiveOrder)) or 0
    activities = session.scalar(select(func.count()).select_from(ArchiveActivity)) or 0
    local_rows = session.scalar(select(func.count()).select_from(ArchiveLocal)) or 0
    periods: dict[str, int] = {}
    for period in EQUITY_PERIODS:
        periods[period] = session.scalar(
            select(func.count()).select_from(ArchiveEquity).where(
                ArchiveEquity.period == period,
                ArchiveEquity.timeframe == "1D",
            )
        ) or 0
    days = periods.get("all") or max(periods.values() or [0])
    latest_run = session.scalar(select(ArchiveRun).order_by(ArchiveRun.started_at.desc()).limit(1))
    positions = 0
    if latest_run is not None:
        positions = session.scalar(
            select(func.count()).select_from(ArchivePosition).where(ArchivePosition.run_id == latest_run.id)
        ) or 0
    status_rows = session.execute(select(ArchiveOrder.status, func.count()).group_by(ArchiveOrder.status)).all()
    type_rows = session.execute(select(ArchiveActivity.activity_type, func.count()).group_by(ArchiveActivity.activity_type)).all()
    last4 = "" if account is None else account.account_last4
    return {
        "archive": PERIOD_ID,
        "ready": bool(account or orders or activities or days or local_rows),
        "source": "archive",
        "account": None
        if account is None
        else {
            "equity": account.equity or None,
            "cash": account.cash or None,
            "last_equity": account.last_equity or None,
            "last4": last4 or None,
            "account_match": (not last4) or last4 == EXPECTED_LAST4,
            "archived_at": account.archived_at.isoformat() if account.archived_at else None,
        },
        "counts": {
            "orders": int(orders),
            "activities": int(activities),
            "days": int(days),
            "positions": int(positions),
            "local_rows": int(local_rows),
        },
        "equity_periods": periods,
        "orders_by_status": {str(status or ""): int(count) for status, count in status_rows},
        "activity_types": {str(kind or ""): int(count) for kind, count in type_rows},
        "last_run": None
        if latest_run is None
        else {
            "id": latest_run.id,
            "status": latest_run.status,
            "detail": latest_run.detail or "",
            "started_at": _iso(latest_run.started_at),
            "finished_at": _iso(latest_run.finished_at),
        },
    }


def equity_points(session: Session, period: str) -> list[dict]:
    rows = session.scalars(
        select(ArchiveEquity)
        .where(ArchiveEquity.period == period, ArchiveEquity.timeframe == "1D")
        .order_by(ArchiveEquity.ts.asc())
    ).all()
    points = []
    previous: float | None = None
    for row in rows:
        try:
            equity = float(row.equity) if row.equity != "" else None
        except ValueError:
            equity = None
        day_pnl = None if equity is None or previous is None else equity - previous
        if equity is not None:
            previous = equity
        points.append(
            {
                "ts": row.ts,
                "t": datetime.fromtimestamp(row.ts, tz=timezone.utc).isoformat(),
                "equity": row.equity or None,
                "profit_loss": row.profit_loss or None,
                "day_pnl": None if day_pnl is None else f"{day_pnl:.2f}",
            }
        )
    return points
