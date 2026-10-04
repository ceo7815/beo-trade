from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import AIAnalysis
from app.schemas.domain import ModelOutput


def load_cached_model(session: Session, digest: str, as_of: datetime, ttl_seconds: int) -> ModelOutput | None:
    row = session.scalar(
        select(AIAnalysis).where(AIAnalysis.input_hash == digest).order_by(AIAnalysis.created_at.desc())
    )
    if row is None:
        return None
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (as_of - created).total_seconds()
    if age < 0 or age > ttl_seconds:
        return None
    output = row.output if isinstance(row.output, dict) else {}
    snapshot = row.snapshot if isinstance(row.snapshot, dict) else {}
    decision = str(output.get("model_decision") or row.decision or "SUPPRESS")
    if decision not in {"BUY", "SUPPRESS"}:
        decision = "SUPPRESS"
    return ModelOutput(
        decision=decision,
        option_symbol=str(snapshot.get("option_symbol") or ""),
        call_put=str(snapshot.get("call_put") or "CALL"),
        thesis=str(output.get("thesis") or ""),
        catalyst=str(output.get("catalyst") or ""),
        risk=str(output.get("risk") or ""),
        invalidation=str(output.get("invalidation") or ""),
        reason_codes=list(output.get("reason_codes") or []),
        raw={"cached": True, "data_quality": output.get("data_quality", "PASS")},
        model=row.model,
        prompt_version=row.prompt_version,
        input_hash=digest,
        input_tokens=row.input_tokens,
        cached_tokens=row.cached_tokens,
        output_tokens=row.output_tokens,
        cost=0,
    )
