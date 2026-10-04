from __future__ import annotations

import json

from sqlalchemy import select

from app.broker.store import order_by_client, record_trade_event, remember_fill
from app.integrations.alpaca import PAPER_WS, apply_trade_update
from app.integrations.secrets import scrub
from app.models.tables import BrokerOrderSnapshot


def probe_trade_stream(key: str, secret: str, timeout: float = 8) -> dict:
    try:
        from websockets.sync.client import connect
    except Exception:
        return {"connected": False, "detail": "ספריית הזרם לא זמינה"}
    try:
        with connect(PAPER_WS, open_timeout=timeout, close_timeout=2) as socket:
            socket.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
            auth = json.loads(socket.recv(timeout=timeout))
            data = auth.get("data") if isinstance(auth, dict) else None
            status = str(data.get("status") or "") if isinstance(data, dict) else ""
            if status != "authorized":
                return {"connected": False, "detail": "האימות לזרם נכשל"}
            socket.send(json.dumps({"action": "listen", "data": {"streams": ["trade_updates"]}}))
            listened = json.loads(socket.recv(timeout=timeout))
            payload = listened.get("data") if isinstance(listened, dict) else None
            streams = payload.get("streams") if isinstance(payload, dict) else None
            if not isinstance(streams, list) or "trade_updates" not in streams:
                return {"connected": True, "detail": "האימות עבר"}
            return {"connected": True, "detail": "trade_updates"}
    except Exception as exc:
        return {"connected": False, "detail": scrub(str(exc), [key, secret])}


def ingest_update(session, message: dict) -> dict:
    update = apply_trade_update(message)
    execution_id = str(update.get("execution_id") or "")
    inserted = False
    if execution_id and update.get("event") in {"fill", "partial_fill"}:
        inserted = remember_fill(
            session,
            {
                "execution_id": execution_id,
                "broker_order_id": update.get("broker_order_id") or "",
                "symbol": update.get("symbol") or "",
                "qty": update.get("qty"),
                "price": update.get("price"),
                "timestamp": update.get("timestamp"),
                "cumulative_qty": update.get("filled_qty"),
                "average_price": update.get("filled_avg_price"),
                "source": "alpaca-paper",
            },
        )
    client_order_id = str(update.get("client_order_id") or "")
    trade_id = ""
    if client_order_id:
        snapshot = session.scalar(
            select(BrokerOrderSnapshot).where(BrokerOrderSnapshot.client_order_id == client_order_id).limit(1)
        )
        if snapshot is not None:
            trade_id = snapshot.trade_id
        elif order_by_client(session, client_order_id) is not None:
            trade_id = ""
    if trade_id:
        record_trade_event(session, trade_id, str(update.get("internal_state") or "ERROR"), "alpaca-paper", str(update.get("event") or ""))
    update["fill_inserted"] = inserted
    update["duplicate_fill"] = bool(execution_id) and not inserted and update.get("event") in {"fill", "partial_fill"}
    return update
