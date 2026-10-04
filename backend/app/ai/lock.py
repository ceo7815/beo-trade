from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import AIFingerprintLock


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def claim_fingerprint(session: Session, fingerprint: str, now: datetime, ttl_seconds: int) -> bool:
    """Insert the fingerprint before any model call. The loser of the race does not call."""
    existing = session.get(AIFingerprintLock, fingerprint)
    if existing is not None:
        age = (now - _aware(existing.claimed_at)).total_seconds()
        if 0 <= age <= ttl_seconds:
            return False
        session.delete(existing)
        session.flush()
    session.add(AIFingerprintLock(fingerprint=fingerprint, claimed_at=now))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True
