from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.normalize import OPEN_ORDER_STATES
from app.models.tables import (
    BrokerAccountSnapshot,
    BrokerActivityRecord,
    BrokerFillRecord,
    BrokerOrderRecord,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerReconciliation,
    BrokerTrade,
    BrokerTradeEvent,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record_trade_event(session: Session, trade_id: str, state: str, source: str, reason: str = "", recommendation_id: str = "") -> None:
    trade = session.get(BrokerTrade, trade_id)
    if trade is None:
        session.add(BrokerTrade(id=trade_id, recommendation_id=recommendation_id, state=state, source=source, reason=reason))
        session.flush()
    else:
        trade.state = state
        trade.source = source
        trade.reason = reason
        if recommendation_id:
            trade.recommendation_id = recommendation_id
    previous = session.scalar(
        select(BrokerTradeEvent).where(BrokerTradeEvent.trade_id == trade_id).order_by(BrokerTradeEvent.created_at.desc()).limit(1)
    )
    if previous is not None and previous.state == state and previous.reason == reason:
        return
    session.add(BrokerTradeEvent(trade_id=trade_id, state=state, source=source, reason=reason))


def latest_account(session: Session) -> BrokerAccountSnapshot | None:
    return session.scalar(select(BrokerAccountSnapshot).order_by(BrokerAccountSnapshot.observed_at.desc()).limit(1))


def latest_positions(session: Session) -> list[dict]:
    account = latest_account(session)
    if account is None:
        return []
    rows = session.scalars(select(BrokerPositionSnapshot).where(BrokerPositionSnapshot.observed_at == account.observed_at)).all()
    return [row.payload for row in rows if row.symbol]


def latest_orders(session: Session) -> list[dict]:
    account = latest_account(session)
    if account is None:
        return []
    rows = session.scalars(select(BrokerOrderSnapshot).where(BrokerOrderSnapshot.observed_at == account.observed_at)).all()
    return [row.payload for row in rows]


def remember_account(session: Session, account: dict, observed_at: datetime | None = None) -> None:
    configuration = account.get("configuration") if isinstance(account.get("configuration"), dict) else {}
    payload = {key: value for key, value in account.items() if key != "configuration"}
    session.add(
        BrokerAccountSnapshot(
            payload=payload,
            configuration=configuration,
            observed_at=observed_at or _now(),
        )
    )


def remember_positions(session: Session, positions: list[dict], observed_at: datetime | None = None) -> None:
    stamp = observed_at or _now()
    for row in positions:
        session.add(BrokerPositionSnapshot(symbol=str(row.get("symbol") or ""), payload=row, observed_at=stamp))


def remember_orders(session: Session, orders: list[dict], observed_at: datetime | None = None) -> None:
    stamp = observed_at or _now()
    for row in orders:
        broker_order_id = str(row.get("broker_order_id") or row.get("id") or "")
        if not broker_order_id:
            continue
        session.add(
            BrokerOrderSnapshot(
                broker_order_id=broker_order_id,
                client_order_id=str(row.get("client_order_id") or ""),
                trade_id=str(row.get("trade_id") or ""),
                symbol=str(row.get("symbol") or ""),
                internal_state=str(row.get("internal_state") or "ERROR"),
                payload=row,
                observed_at=stamp,
            )
        )


def remember_fill(session: Session, fill: dict, observed_at: datetime | None = None) -> bool:
    execution_id = str(fill.get("execution_id") or "").strip()
    if not execution_id:
        return False
    existing = session.scalar(select(BrokerFillRecord).where(BrokerFillRecord.execution_id == execution_id))
    if existing is not None:
        return False
    session.add(
        BrokerFillRecord(
            execution_id=execution_id,
            trade_id=str(fill.get("trade_id") or ""),
            broker_order_id=str(fill.get("broker_order_id") or ""),
            symbol=str(fill.get("symbol") or ""),
            payload=fill,
            observed_at=observed_at or _now(),
        )
    )
    try:
        from app.config.settings import get_settings
        from app.positions.capture import capture_entry_fill

        capture_entry_fill(session, fill, get_settings().trading())
    except Exception:
        pass
    return True


def remember_activities(session: Session, activities: list[dict], observed_at: datetime | None = None) -> int:
    stamp = observed_at or _now()
    added = 0
    for row in activities:
        activity_id = str(row.get("id") or "").strip()
        if not activity_id:
            continue
        existing = session.scalar(select(BrokerActivityRecord).where(BrokerActivityRecord.activity_id == activity_id))
        if existing is not None:
            continue
        session.add(
            BrokerActivityRecord(
                activity_id=activity_id,
                activity_type=str(row.get("activity_type") or ""),
                payload=row,
                observed_at=stamp,
            )
        )
        added += 1
    return added


def order_by_client(session: Session, client_order_id: str) -> BrokerOrderRecord | None:
    return session.scalar(select(BrokerOrderRecord).where(BrokerOrderRecord.client_order_id == client_order_id).limit(1))


def open_order_for_symbol(session: Session, symbol: str, intent: str) -> dict | None:
    for row in latest_orders(session):
        if row.get("symbol") == symbol and row.get("position_intent") == intent and row.get("internal_state") in OPEN_ORDER_STATES:
            return row
    return None


def reconcile_broker(session: Session, adapter) -> dict:
    account = adapter.get_account()
    positions = adapter.sync_positions()
    orders = adapter.get_orders("all")
    fills = adapter.get_fills()
    activities = adapter.get_activities()
    previous_account = latest_account(session)
    previous_positions = {str(row.get("symbol") or ""): str(row.get("qty") or "") for row in latest_positions(session)}
    previous_orders = {str(row.get("broker_order_id") or ""): str(row.get("internal_state") or "") for row in latest_orders(session)}
    mismatches: list[dict] = []
    if previous_account is not None:
        for field in ("cash", "equity", "buying_power", "portfolio_value"):
            local = previous_account.payload.get(field)
            remote = account.get(field)
            if local is not None and remote is not None and str(local) != str(remote):
                mismatches.append({"kind": "account", "field": field, "local": local, "broker": remote})
        remote_positions = {str(row.get("symbol") or ""): str(row.get("qty") or "") for row in positions}
        for symbol, qty in previous_positions.items():
            broker_qty = remote_positions.get(symbol)
            if broker_qty != qty:
                mismatches.append({"kind": "position", "symbol": symbol, "local": qty, "broker": broker_qty})
        for symbol, qty in remote_positions.items():
            if symbol not in previous_positions:
                mismatches.append({"kind": "position", "symbol": symbol, "local": None, "broker": qty})
        remote_orders = {str(row.get("broker_order_id") or ""): str(row.get("internal_state") or "") for row in orders}
        for order_id, state in previous_orders.items():
            broker_state = remote_orders.get(order_id)
            if broker_state != state:
                mismatches.append({"kind": "order", "broker_order_id": order_id, "local": state, "broker": broker_state})
        for order_id, state in remote_orders.items():
            if order_id not in previous_orders:
                mismatches.append({"kind": "order", "broker_order_id": order_id, "local": None, "broker": state})
        remote_fills = {str(row.get("execution_id") or ""): row for row in fills}
        for stored in session.scalars(select(BrokerFillRecord)).all():
            remote = remote_fills.get(stored.execution_id)
            if remote is None:
                continue
            if str(stored.payload.get("qty")) != str(remote.get("qty")) or str(stored.payload.get("price")) != str(remote.get("price")):
                mismatches.append({"kind": "fill", "execution_id": stored.execution_id, "local": stored.payload.get("price"), "broker": remote.get("price")})
    stamp = _now()
    remember_account(session, account, stamp)
    remember_positions(session, positions, stamp)
    remember_orders(session, orders, stamp)
    remember_activities(session, activities, stamp)
    for fill in fills:
        remember_fill(session, fill, stamp)
    report = {"ok": not mismatches, "mismatches": mismatches, "source": "alpaca-paper", "observed_at": stamp.isoformat()}
    session.add(BrokerReconciliation(ok=report["ok"], mismatches=mismatches, observed_at=stamp))
    return report
