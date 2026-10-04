from __future__ import annotations

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.config.settings import Settings
from app.broker.execution import place_order
from app.broker.runtime import open_paper_broker
from app.broker.normalize import execution_pnl
from app.broker.store import remember_account, remember_orders, remember_positions
from app.broker.stream import ingest_update, probe_trade_stream
from app.integrations.alpaca import (
    ALLOWED_INTENTS,
    PAPER_WS,
    AlpacaNotConfigured,
    PaperOnlyError,
)
from app.integrations.secrets import resolve_secret, save_secrets, scrub
from app.integrations.service import list_integrations, record_event, run_check
from app.models.db import session_scope
from app.models.tables import BrokerFillRecord, BrokerReconciliation
from app.security.auth import user_id_from_header


class SecretBody(BaseModel):
    fields: dict[str, str]


class OrderBody(BaseModel):
    symbol: str
    qty: int
    limit_price: str
    position_intent: str
    approved: bool = False
    recommendation_id: str = ""
    trade_id: str = ""


def _broker(settings: Settings):
    try:
        return open_paper_broker(settings)
    except (AlpacaNotConfigured, PaperOnlyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def register(app: FastAPI, settings: Settings) -> None:
    @app.get("/api/v1/integrations")
    def integrations(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        with session_scope() as session:
            return list_integrations(settings, session)

    @app.post("/api/v1/integrations/{integration_id}/secrets")
    def save(integration_id: str, body: SecretBody, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        if integration_id not in {"openai", "alpaca", "thetadata", "benzinga", "fred", "redis", "supabase"}:
            raise HTTPException(status_code=404, detail="אין שמירת מפתח לספק הזה")
        save_secrets(settings, body.fields)
        with session_scope() as session:
            record_event(session, integration_id, "connection_attempt", "מפתח נשמר בשרת. אין עדיין בדיקה.")
            session.commit()
            item = next(row for row in list_integrations(settings, session)["items"] if row["id"] == integration_id)
        return {"saved": True, "key": item["key"], "status": "unknown", "detail": "המפתח נשמר. יש להריץ בדיקת חיבור."}

    @app.post("/api/v1/integrations/{integration_id}/test")
    def test(integration_id: str, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        with session_scope() as session:
            try:
                return run_check(settings, session, integration_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="הממשק לא נמצא") from exc

    @app.get("/api/v1/broker/alpaca/account")
    def alpaca_account(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            account = adapter.get_account()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        with session_scope() as session:
            remember_account(session, account)
            session.commit()
        return {"environment": "PAPER", "source": "alpaca-paper", "source_provider": "alpaca", "account": account}

    @app.get("/api/v1/broker/alpaca/positions")
    def alpaca_positions(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            items = adapter.sync_positions()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        with session_scope() as session:
            remember_positions(session, items)
            session.commit()
        return {"environment": "PAPER", "source": "alpaca-paper", "items": items}

    @app.get("/api/v1/broker/alpaca/orders")
    def alpaca_orders(
        authorization: str | None = Header(default=None),
        status: str = "all",
        symbol: str = "",
        asset_class: str = "",
        after: str = "",
        until: str = "",
    ):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            items = adapter.get_orders(status=status, symbol=symbol, asset_class=asset_class, after=after, until=until)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        with session_scope() as session:
            remember_orders(session, items)
            session.commit()
        return {"environment": "PAPER", "source": "alpaca-paper", "items": items}

    @app.post("/api/v1/broker/alpaca/orders")
    def alpaca_submit(body: OrderBody, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        if not body.approved:
            raise HTTPException(status_code=409, detail="נדרש אישור משתמש. ה-AI לא שולח פקודות.")
        if body.position_intent not in ALLOWED_INTENTS:
            raise HTTPException(status_code=422, detail="כוונת הפוזיציה אינה נתמכת בגרסה הזו")
        adapter = _broker(settings)
        try:
            with session_scope() as session:
                try:
                    result = place_order(
                        settings,
                        session,
                        adapter,
                        {
                            "symbol": body.symbol,
                            "qty": body.qty,
                            "limit_price": body.limit_price,
                            "position_intent": body.position_intent,
                            "approved": body.approved,
                            "recommendation_id": body.recommendation_id,
                            "trade_id": body.trade_id,
                        },
                    )
                except PaperOnlyError as exc:
                    session.commit()
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                record_event(session, "alpaca", "order_submitted", body.symbol.strip().upper())
                session.commit()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        return {"environment": "PAPER", "source": "alpaca-paper", "duplicate": result["duplicate"], "trade_id": result["trade_id"], "order": result["order"]}

    @app.delete("/api/v1/broker/alpaca/orders/{order_id}")
    def alpaca_cancel(order_id: str, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            adapter.cancel_order(order_id)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        with session_scope() as session:
            record_event(session, "alpaca", "order_canceled", order_id)
            session.commit()
        return {"environment": "PAPER", "canceled": order_id}

    @app.get("/api/v1/broker/alpaca/stream")
    def alpaca_stream(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        key, _ = resolve_secret(settings, "alpaca_api_key")
        secret, _ = resolve_secret(settings, "alpaca_api_secret")
        if settings.trading_mode.upper() != "PAPER":
            raise HTTPException(status_code=409, detail="TRADING_MODE אינו PAPER")
        if not key or not secret:
            return {"armed": False, "url": PAPER_WS, "detail": "נדרש API Key. אין חיבור שקטים."}
        return {
            "armed": True,
            "url": PAPER_WS,
            "streams": ["trade_updates"],
            "detail": "הזרם מוכן ל-Paper בלבד. אירועי fill, cancel ו-reject מתורגמים לסטטוס פקודה.",
        }

    @app.post("/api/v1/broker/alpaca/updates")
    def alpaca_update(message: dict, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        with session_scope() as session:
            update = ingest_update(session, message)
            event = {
                "filled": "order_filled",
                "canceled": "order_canceled",
                "rejected": "order_rejected",
            }.get(update["status"], "data_received")
            record_event(session, "alpaca", event, f"{update['symbol']} {update['status']}".strip())
            session.commit()
        return update

    @app.get("/api/v1/broker/alpaca/activities")
    def alpaca_activities(authorization: str | None = Header(default=None), activity_types: str = ""):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            items = adapter.get_activities(activity_types)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        return {"environment": "PAPER", "source": "alpaca-paper", "items": items}

    @app.get("/api/v1/broker/alpaca/fills")
    def alpaca_fills(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            items = adapter.get_fills()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        with session_scope() as session:
            from app.broker.store import remember_fill

            for item in items:
                remember_fill(session, item)
            session.commit()
        return {"environment": "PAPER", "source": "alpaca-paper", "items": items}

    @app.get("/api/v1/broker/alpaca/clock")
    def alpaca_clock(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            clock = adapter.get_market_clock()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        return {"environment": "PAPER", "source": "alpaca-paper", "clock": clock}

    @app.get("/api/v1/broker/alpaca/assets/{symbol}")
    def alpaca_asset(symbol: str, authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            asset = adapter.get_asset(symbol.strip().upper())
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        return {"environment": "PAPER", "source": "alpaca-paper", "asset": asset}

    @app.get("/api/v1/broker/alpaca/capabilities")
    def alpaca_capabilities(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        return {
            "environment": "PAPER",
            "order_types": ["market", "limit", "stop", "stop_limit"],
            "documented_position_intents": ["buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close"],
            "strategy_position_intents": list(ALLOWED_INTENTS),
            "protective_orders": "strategy_exits_stay_in_beo_trade",
            "order_class": "simple",
        }

    @app.get("/api/v1/broker/alpaca/health")
    def alpaca_health(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        flags = {
            "CREDENTIALS_VALID": False,
            "API_REACHABLE": False,
            "ACCOUNT_READABLE": False,
            "ORDERS_READABLE": False,
            "POSITIONS_READABLE": False,
            "STREAM_CONNECTED": False,
            "RECONCILIATION_OK": False,
            "PAPER_ONLY": settings.trading_mode.upper() == "PAPER",
        }
        mismatches: list[dict] = []
        detail = ""
        adapter = _broker(settings)
        try:
            account = adapter.get_account()
            flags["CREDENTIALS_VALID"] = True
            flags["API_REACHABLE"] = True
            flags["ACCOUNT_READABLE"] = bool(account.get("status"))
            flags["POSITIONS_READABLE"] = isinstance(adapter.get_positions(), list)
            flags["ORDERS_READABLE"] = isinstance(adapter.get_orders("open"), list)
            report = adapter.reconcile()
            flags["RECONCILIATION_OK"] = bool(report.get("ok"))
            mismatches = report.get("mismatches") or []
        except httpx.HTTPError as exc:
            detail = scrub(str(exc))
        finally:
            adapter.close()
        key, _ = resolve_secret(settings, "alpaca_api_key")
        secret, _ = resolve_secret(settings, "alpaca_api_secret")
        stream = probe_trade_stream(key, secret) if key and secret else {"connected": False, "detail": "נדרש API Key"}
        flags["STREAM_CONNECTED"] = bool(stream.get("connected"))
        return {
            "environment": "PAPER",
            "source": "alpaca-paper",
            "flags": flags,
            "mismatches": mismatches,
            "stream": stream.get("detail") or "",
            "detail": detail or "Alpaca Paper",
        }

    @app.post("/api/v1/broker/alpaca/sync")
    def alpaca_sync(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        adapter = _broker(settings)
        try:
            report = adapter.reconcile()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=scrub(str(exc))) from exc
        finally:
            adapter.close()
        return report

    @app.get("/api/v1/broker/alpaca/performance")
    def alpaca_performance(authorization: str | None = Header(default=None)):
        user_id_from_header(settings, authorization)
        with session_scope() as session:
            fills = list(session.scalars(select(BrokerFillRecord).order_by(BrokerFillRecord.observed_at.asc())))
            last = session.scalar(select(BrokerReconciliation).order_by(BrokerReconciliation.observed_at.desc()).limit(1))
        grouped: dict[str, list] = {}
        for row in fills:
            grouped.setdefault(row.trade_id or row.symbol, []).append(row.payload)
        closed = []
        for key, rows in grouped.items():
            buys = [row for row in rows if str(row.get("side") or "").lower() == "buy" and row.get("price") is not None]
            sells = [row for row in rows if str(row.get("side") or "").lower() == "sell" and row.get("price") is not None]
            if not buys or not sells:
                continue
            result = execution_pnl(str(buys[0]["price"]), str(sells[-1]["price"]), str(buys[0].get("qty") or "0"))
            if result is None:
                continue
            result["trade_id"] = key
            result["symbol"] = buys[0].get("symbol") or ""
            closed.append(result)
        return {
            "source": "alpaca-paper",
            "fills": [row.payload for row in fills],
            "closed": closed,
            "reconciliation_ok": None if last is None else last.ok,
        }
