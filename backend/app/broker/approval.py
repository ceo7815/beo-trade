from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.store import record_trade_event
from app.models.tables import ApprovalRecord


def approve_recommendation(session: Session, recommendation_id: str, now: datetime | None = None) -> ApprovalRecord:
    existing = session.scalar(select(ApprovalRecord).where(ApprovalRecord.recommendation_id == recommendation_id))
    if existing is not None:
        return existing
    moment = now or datetime.now(timezone.utc)
    trade_id = str(uuid4())
    row = ApprovalRecord(
        trade_id=trade_id,
        recommendation_id=recommendation_id,
        status="APPROVED",
        approved_at=moment,
    )
    session.add(row)
    session.flush()
    record_trade_event(session, trade_id, "APPROVED", "beo-trade", "אישור נשמר", recommendation_id)
    return row
