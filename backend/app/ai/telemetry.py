from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.tables import AIRequestLog


def _optional_int(payload: dict, key: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    try:
        return int(payload[key])
    except (TypeError, ValueError):
        return None


def telemetry_from_response(payload: dict, latency_ms: int) -> dict:
    """Copy only fields the response actually contains. Missing fields stay None."""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    cached = _optional_int(input_details, "cached_tokens")
    request_id = payload.get("id")
    return {
        "request_id": request_id if isinstance(request_id, str) and request_id else None,
        "input_tokens": _optional_int(usage, "input_tokens"),
        "cached_tokens": cached,
        "output_tokens": _optional_int(usage, "output_tokens"),
        "reasoning_tokens": _optional_int(output_details, "reasoning_tokens"),
        "total_tokens": _optional_int(usage, "total_tokens"),
        "latency_ms": latency_ms,
        "cache_hit": None if cached is None else cached > 0,
        "retry_count": None,
    }


def store_ai_request(session: Session, row: dict, created_at: datetime) -> None:
    present = {key: value for key, value in row.items() if key in AIRequestLog.__table__.columns}
    present["created_at"] = created_at
    if present.get("estimated_cost") is not None:
        present["estimated_cost"] = Decimal(str(present["estimated_cost"]))
    session.add(AIRequestLog(**present))
