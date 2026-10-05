from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.integrations.catalog import ACTIVE, ALL, FUTURE, INTERNAL
from app.integrations.probes import PROBES, probe_internal, probe_redis, probe_xcloud
from app.integrations.secrets import mask, resolve_secret
from app.models.tables import IntegrationCheck, IntegrationEvent

LABELS = {
    "connected": "מחובר",
    "missing_key": "נדרש API Key",
    "disconnected": "לא מחובר",
    "blocked_by_entitlement": "חסום עד הפעלת הספק",
    "planned": "מתוכנן",
    "error": "תקלה",
    "no_data": "מחובר — אין נתונים",
    "internal": "זמין",
    "not_built": "לא מיושם",
    "unknown": "לא נבדק",
}


def record_event(session: Session, integration_id: str, event: str, detail: str) -> None:
    session.add(IntegrationEvent(integration_id=integration_id, event=event, detail=detail[:400]))


def latest_check(session: Session, integration_id: str) -> IntegrationCheck | None:
    return session.scalar(
        select(IntegrationCheck)
        .where(IntegrationCheck.integration_id == integration_id)
        .order_by(IntegrationCheck.checked_at.desc())
        .limit(1)
    )


def key_view(settings: Settings, names: tuple[str, ...]) -> dict:
    if not names:
        return {"status": "none", "masked": ""}
    present = []
    origins = []
    for name in names:
        value, origin = resolve_secret(settings, name)
        if value:
            present.append(mask(value))
            origins.append(origin)
    if len(present) != len(names):
        return {"status": "missing", "masked": ""}
    return {"status": origins[0], "masked": present[0]}


def view(settings: Settings, session: Session, spec) -> dict:
    check = latest_check(session, spec.id)
    keys = key_view(settings, spec.secret_fields)
    if spec.group == "future":
        status = "planned"
        detail = spec.future_reason
        latency = None
        checked_at = None
    elif spec.id == "redis":
        status, latency, detail = probe_redis(settings)
        checked_at = datetime.now(timezone.utc).isoformat()
    elif spec.id == "xcloud":
        status, latency, detail = probe_xcloud()
        checked_at = datetime.now(timezone.utc).isoformat()
    elif spec.id == "regime":
        status, latency, detail = probe_internal("regime")
        checked_at = datetime.now(timezone.utc).isoformat()
    elif check is None and spec.id in INTERNAL:
        status, latency, detail = probe_internal(spec.id)
        checked_at = None
    elif check is None and spec.secret_fields and keys["status"] == "missing":
        status, detail, latency, checked_at = "missing_key", "נדרש API Key", None, None
    elif check is None:
        status, detail, latency, checked_at = "unknown", "לא נבדק", None, None
    else:
        status, detail, latency = check.status, check.detail, check.latency_ms
        checked_at = check.checked_at.isoformat()
    return {
        "id": spec.id,
        "name": spec.name,
        "category": spec.category,
        "role": spec.role,
        "group": spec.group,
        "primary": spec.primary,
        "environment": spec.environment,
        "status": status,
        "status_label": LABELS.get(status, status),
        "detail": detail,
        "latency_ms": latency,
        "last_check": checked_at,
        "key": keys,
        "future_reason": spec.future_reason,
    }


def summary(items: list[dict]) -> dict:
    active = [item for item in items if item["group"] == "active" and item["id"] not in INTERNAL]
    return {
        "connected": sum(1 for item in active if item["status"] == "connected"),
        "warnings": sum(1 for item in active if item["status"] in {"missing_key", "no_data", "unknown", "disconnected"}),
        "errors": sum(1 for item in active if item["status"] == "error"),
        "last_sync": max((item["last_check"] for item in items if item["last_check"]), default=None),
    }


def list_integrations(settings: Settings, session: Session) -> dict:
    items = [view(settings, session, spec) for spec in (*ACTIVE, *FUTURE)]
    return {"items": items, "summary": summary(items), "trading_mode": settings.trading_mode}


def run_check(settings: Settings, session: Session, integration_id: str) -> dict:
    spec = ALL.get(integration_id)
    if spec is None:
        raise KeyError(integration_id)
    if spec.group == "future":
        result = ("planned", None, spec.future_reason)
    elif integration_id in INTERNAL:
        result = probe_internal(integration_id)
    else:
        result = PROBES[integration_id](settings)
    status, latency, detail = result
    check = IntegrationCheck(
        integration_id=integration_id,
        status=status,
        latency_ms=latency,
        detail=detail,
        environment=spec.environment,
        checked_at=datetime.now(timezone.utc),
    )
    session.add(check)
    event = "connection_success" if status in {"connected", "no_data", "internal"} else "connection_failure"
    if status == "missing_key":
        event = "connection_attempt"
    record_event(session, integration_id, event, detail)
    session.commit()
    return view(settings, session, spec)
