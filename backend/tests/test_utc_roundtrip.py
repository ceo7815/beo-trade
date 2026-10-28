from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.main import create_app
from app.models.db import configure_database, init_db, session_scope
from app.models.tables import Alert, AuditLog, BrokerTrade, BrokerTradeEvent
from app.models.utc_time import as_utc
from app.config.settings import Settings

DUBAI = ZoneInfo("Asia/Dubai")
NEW_YORK = ZoneInfo("America/New_York")
INSTANT = datetime(2026, 10, 28, 6, 49, tzinfo=timezone.utc)


def _settings(tmp_path) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "clock.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )


def test_new_audit_and_alert_rows_round_trip_as_utc(tmp_path):
    configure_database(_settings(tmp_path))
    init_db(_settings(tmp_path))
    with session_scope() as session:
        session.add(
            AuditLog(
                id="audit-1",
                actor="desk",
                action="NOTE",
                entity="audit",
                entity_id="audit-1",
                payload={"kind": "audit"},
                created_at=INSTANT,
            )
        )
        session.add(Alert(id="alert-1", kind="BUY", message="BUY NVDA", created_at=INSTANT))
        session.commit()
        stored_audit = session.execute(text("SELECT created_at FROM audit_logs WHERE id = 'audit-1'")).scalar_one()
        stored_alert = session.execute(text("SELECT created_at FROM alerts WHERE id = 'alert-1'")).scalar_one()
    assert stored_audit.endswith("+00:00")
    assert stored_alert.endswith("+00:00")
    assert "06:49:00" in stored_audit

    with session_scope() as session:
        audit = session.get(AuditLog, "audit-1")
        alert = session.get(Alert, "alert-1")
        assert audit is not None and alert is not None
        assert audit.created_at == INSTANT
        assert alert.created_at == INSTANT
        assert audit.created_at.tzinfo is not None
        dubai = audit.created_at.astimezone(DUBAI)
        new_york = alert.created_at.astimezone(NEW_YORK)
    assert dubai.hour == 10 and dubai.minute == 49
    assert new_york.hour == 2 and new_york.minute == 49


def test_legacy_naive_rows_are_read_as_utc(tmp_path):
    configure_database(_settings(tmp_path))
    init_db(_settings(tmp_path))
    legacy = "2026-10-28 06:49:00.000000"
    with session_scope() as session:
        session.execute(
            text(
                "INSERT INTO audit_logs (id, actor, action, entity, entity_id, payload, created_at) "
                "VALUES ('legacy-audit', 'legacy', 'NOTE', 'audit', 'legacy-audit', '{}', :created)"
            ),
            {"created": legacy},
        )
        session.execute(
            text(
                "INSERT INTO alerts (id, kind, message, created_at) "
                "VALUES ('legacy-alert', 'BUY', 'old', :created)"
            ),
            {"created": legacy},
        )
        session.commit()
        session.expire_all()
        audit = session.get(AuditLog, "legacy-audit")
        alert = session.get(Alert, "legacy-alert")
        assert audit is not None and alert is not None
        assert as_utc(audit.created_at) == INSTANT
        assert alert.created_at.tzinfo is not None
        # Reading the naive wall clock as Dubai would be four hours early.
        assert audit.created_at.astimezone(DUBAI).hour == 10
        assert alert.created_at.isoformat().endswith("+00:00")


def test_trade_event_default_is_aware_utc(tmp_path):
    configure_database(_settings(tmp_path))
    init_db(_settings(tmp_path))
    before = datetime.now(timezone.utc)
    with session_scope() as session:
        session.add(BrokerTrade(id="trade-1", state="BLOCKED", source="beo-trade"))
        session.add(BrokerTradeEvent(trade_id="trade-1", state="BLOCKED", source="beo-trade", reason="test"))
        session.commit()
        event = session.execute(text("SELECT created_at FROM broker_trade_events")).scalar_one()
        session.expire_all()
        loaded = session.scalars(select(BrokerTradeEvent)).one()
    after = datetime.now(timezone.utc)
    assert str(event).endswith("+00:00")
    assert loaded.created_at.tzinfo is not None
    assert before <= loaded.created_at <= after


def test_audit_and_alert_api_emit_utc_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    settings = _settings(tmp_path)
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        with session_scope() as session:
            session.add(BrokerTrade(id="trade-api", state="BLOCKED", source="beo-trade"))
            session.add(
                BrokerTradeEvent(
                    trade_id="trade-api",
                    state="BLOCKED",
                    source="beo-trade",
                    reason="stamp",
                    created_at=INSTANT,
                )
            )
            session.add(Alert(id="alert-api", kind="BUY", message="BUY NVDA", created_at=INSTANT))
            session.execute(
                text(
                    "INSERT INTO broker_trade_events (id, trade_id, state, source, reason, created_at) "
                    "VALUES ('legacy-event', 'trade-api', 'NOTE', 'legacy', 'old', :created)"
                ),
                {"created": "2026-10-28 06:49:00.000000"},
            )
            session.commit()
        audit = client.get("/api/v1/audit")
        alerts = client.get("/api/v1/alerts")
    assert audit.status_code == 200
    stamps = [item["created_at"] for item in audit.json()["items"]]
    assert stamps
    assert all(item.endswith("+00:00") for item in stamps)
    assert alerts.status_code == 200
    assert alerts.json()["items"][0]["created_at"].endswith("+00:00")
    assert alerts.json()["items"][0]["created_at"].startswith("2026-10-28T06:49:00")
