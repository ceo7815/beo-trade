"""Archive trigger is admin-only. Reads use the same key rules as the desk."""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.archive.job import PERIOD_ID, archive_summary, equity_points, start_paper_100k_archive
from app.archive.reader import ArchiveNotConfigured, ReadOnlyAlpaca
from app.archive.tables import ArchiveActivity, ArchiveLocal, ArchiveOrder
from app.config.settings import Settings
from app.integrations.secrets import resolve_secret
from app.models.db import session_scope


def build_reader(settings: Settings) -> ReadOnlyAlpaca:
    key, _origin = resolve_secret(settings, "alpaca_100k_api_key")
    secret, _secret_origin = resolve_secret(settings, "alpaca_100k_api_secret")
    if not key or not secret:
        raise ArchiveNotConfigured("נדרש API Key")
    return ReadOnlyAlpaca(key, secret, settings.trading_mode)


def register(app: FastAPI, settings: Settings) -> None:
    @app.post("/api/v1/archive/paper_100k/run")
    def run_archive():
        """Start the copy on a background thread and return the run id. Health stays up."""
        keys = True
        try:
            probe = build_reader(settings)
        except ArchiveNotConfigured:
            keys = False
        else:
            if hasattr(probe, "close"):
                probe.close()
        body = start_paper_100k_archive(settings)
        if not keys:
            body["detail"] = "אין מפתחות ALPACA_100K. נשמר רק היומן המקומי. לא נשלחה פקודה."
        return JSONResponse(body, status_code=200 if body.get("already_running") else 202)

    @app.get("/api/v1/archive/paper_100k/summary")
    def summary():
        with session_scope() as session:
            return archive_summary(session)

    @app.get("/api/v1/archive/paper_100k/orders")
    def orders(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(ArchiveOrder)) or 0
            rows = session.scalars(
                select(ArchiveOrder).order_by(ArchiveOrder.submitted_at.desc()).offset(offset).limit(limit)
            ).all()
            return {
                "archive": PERIOD_ID,
                "total": int(total),
                "limit": limit,
                "offset": offset,
                "items": [
                    {
                        "id": row.alpaca_order_id,
                        "status": row.status,
                        "symbol": row.symbol,
                        "submitted_at": row.submitted_at or None,
                        "side": (row.payload or {}).get("side"),
                        "qty": (row.payload or {}).get("qty"),
                        "filled_qty": (row.payload or {}).get("filled_qty"),
                        "filled_avg_price": (row.payload or {}).get("filled_avg_price"),
                        "type": (row.payload or {}).get("type"),
                        "position_intent": (row.payload or {}).get("position_intent"),
                    }
                    for row in rows
                ],
            }

    @app.get("/api/v1/archive/paper_100k/equity")
    def equity(period: str = Query(default="all")):
        key = period if period in {"all", "1A", "6M", "3M", "1M", "1W", "1D"} else "all"
        with session_scope() as session:
            points = equity_points(session, key)
            return {"archive": PERIOD_ID, "period": key, "timeframe": "1D", "days": len(points), "points": points}

    @app.get("/api/v1/archive/paper_100k/activities")
    def activities(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        with session_scope() as session:
            total = session.scalar(select(func.count()).select_from(ArchiveActivity)) or 0
            rows = session.scalars(
                select(ArchiveActivity).order_by(ArchiveActivity.activity_id.desc()).offset(offset).limit(limit)
            ).all()
            return {
                "archive": PERIOD_ID,
                "total": int(total),
                "limit": limit,
                "offset": offset,
                "items": [
                    {
                        "id": row.activity_id,
                        "activity_type": row.activity_type,
                        "symbol": (row.payload or {}).get("symbol"),
                        "side": (row.payload or {}).get("side"),
                        "qty": (row.payload or {}).get("qty"),
                        "price": (row.payload or {}).get("price"),
                        "at": (row.payload or {}).get("transaction_time") or (row.payload or {}).get("date"),
                    }
                    for row in rows
                ],
            }

    @app.get("/api/v1/archive/paper_100k/trades")
    def trades(limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        with session_scope() as session:
            local = session.scalars(select(ArchiveLocal).order_by(ArchiveLocal.source.asc())).all()
            fills = session.scalars(
                select(ArchiveActivity).where(ArchiveActivity.activity_type == "FILL")
            ).all()
            items = [_local_card(row) for row in local] + [_fill_card(row) for row in fills]
            total = len(items)
            page = items[offset : offset + limit]
            return {"archive": PERIOD_ID, "total": total, "limit": limit, "offset": offset, "items": page}


def _local_card(row: ArchiveLocal) -> dict:
    payload = row.payload or {}
    return {
        "source": row.source,
        "id": row.source_id,
        "symbol": payload.get("symbol") or payload.get("option_symbol") or "",
        "side": payload.get("side") or payload.get("state") or "",
        "qty": payload.get("quantity") or payload.get("qty") or payload.get("entry_fill_quantity"),
        "entry_price": payload.get("entry_price") or payload.get("entry_fill_price"),
        "exit_price": payload.get("exit_price"),
        "pnl": payload.get("pnl_dollars"),
        "reason": payload.get("exit_reason") or payload.get("reason") or payload.get("state") or "",
        "at": payload.get("entry_at") or payload.get("created_at") or payload.get("entry_time") or payload.get("observed_at"),
    }


def _fill_card(row: ArchiveActivity) -> dict:
    payload = row.payload or {}
    return {
        "source": "fill",
        "id": row.activity_id,
        "symbol": payload.get("symbol") or "",
        "side": payload.get("side") or "",
        "qty": payload.get("qty"),
        "entry_price": payload.get("price"),
        "exit_price": None,
        "pnl": None,
        "reason": payload.get("activity_type") or "FILL",
        "at": payload.get("transaction_time") or payload.get("date"),
    }
