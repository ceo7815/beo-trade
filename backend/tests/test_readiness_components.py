import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import Settings
from app.models.db import configure_database, init_db
from app.ops.readiness import build_ready


NOW = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def _settings(tmp_path) -> Settings:
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "ready.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _paths(monkeypatch, tmp_path):
    monkeypatch.setattr("app.ops.readiness.HEARTBEAT", tmp_path / "worker_heartbeat.txt")
    monkeypatch.setattr("app.ops.readiness.LAST_SCAN", tmp_path / "last_scan.json")
    monkeypatch.setattr("app.ops.readiness.FEED_STATUS", tmp_path / "feed_status.json")


def _ready(monkeypatch, tmp_path, broker):
    _paths(monkeypatch, tmp_path)
    code, body = build_ready(_settings(tmp_path), now=NOW, broker_probe=lambda _settings: broker)
    return code, body


def test_ready_is_not_ready_without_the_live_components(tmp_path, monkeypatch):
    code, body = _ready(monkeypatch, tmp_path, {"status": "FAILED", "reachable": False, "detail": "missing_key", "latency_ms": None})
    assert code == 200
    assert body["status"] == "not_ready"
    assert body["ready"] is False
    assert body["components"]["worker"]["alive"] is False
    assert body["components"]["last_cycle"]["succeeded"] is False
    assert body["components"]["last_cycle"]["last_success"] is None
    assert body["components"]["market_data"]["fresh"] is False
    assert body["components"]["broker"]["reachable"] is False
    assert "worker" in body["blocked_reasons"]
    assert "last_cycle" in body["blocked_reasons"]
    assert "market_data" in body["blocked_reasons"]
    assert "broker" in body["blocked_reasons"]


def test_ready_follows_heartbeat_last_success_quotes_and_broker(tmp_path, monkeypatch):
    _paths(monkeypatch, tmp_path)
    (tmp_path / "worker_heartbeat.txt").write_text(NOW.isoformat() + " INTRADAY_SCAN\n", encoding="utf-8")
    (tmp_path / "last_scan.json").write_text(
        json.dumps({"last_success": NOW.isoformat(), "observed_at": NOW.isoformat(), "blocked": ""}),
        encoding="utf-8",
    )
    (tmp_path / "feed_status.json").write_text(
        json.dumps({"market_status": {"last_success": NOW.isoformat(), "newest_quote": NOW.isoformat()}}),
        encoding="utf-8",
    )
    code, body = build_ready(
        _settings(tmp_path),
        now=NOW,
        broker_probe=lambda _settings: {"status": "HEALTHY", "reachable": True, "detail": "account", "latency_ms": 12},
    )
    assert code == 200
    assert body["ready"] is True
    assert body["status"] == "ready"
    assert body["components"]["worker"]["alive"] is True
    assert body["components"]["last_cycle"]["succeeded"] is True
    assert body["components"]["last_cycle"]["source"] == "last_success"
    assert body["components"]["market_data"]["fresh"] is True
    assert body["components"]["broker"]["reachable"] is True
    assert body["autonomous"] == "READY"


def test_a_fresh_heartbeat_does_not_count_as_a_successful_cycle(tmp_path, monkeypatch):
    _paths(monkeypatch, tmp_path)
    (tmp_path / "worker_heartbeat.txt").write_text(NOW.isoformat() + " OPEN\n", encoding="utf-8")
    stale = (NOW - timedelta(minutes=30)).isoformat()
    (tmp_path / "last_scan.json").write_text(
        json.dumps({"observed_at": NOW.isoformat(), "blocked": "אין נתוני חשבון", "finished_at": stale}),
        encoding="utf-8",
    )
    _code, body = build_ready(
        _settings(tmp_path),
        now=NOW,
        broker_probe=lambda _settings: {"status": "HEALTHY", "reachable": True, "detail": "account", "latency_ms": 1},
    )
    assert body["components"]["worker"]["alive"] is True
    assert body["components"]["last_cycle"]["succeeded"] is False
    assert body["ready"] is False


def test_finished_scan_counts_when_last_success_is_absent(tmp_path, monkeypatch):
    _paths(monkeypatch, tmp_path)
    (tmp_path / "worker_heartbeat.txt").write_text(NOW.isoformat() + " OPEN\n", encoding="utf-8")
    (tmp_path / "last_scan.json").write_text(
        json.dumps({"finished_at": NOW.isoformat(), "observed_at": NOW.isoformat(), "blocked": ""}),
        encoding="utf-8",
    )
    (tmp_path / "feed_status.json").write_text(
        json.dumps({"options_status": {"newest_quote": NOW.isoformat()}}),
        encoding="utf-8",
    )
    _code, body = build_ready(
        _settings(tmp_path),
        now=NOW,
        broker_probe=lambda _settings: {"status": "HEALTHY", "reachable": True, "detail": "account", "latency_ms": 1},
    )
    assert body["components"]["last_cycle"]["source"] == "finished_at"
    assert body["components"]["last_cycle"]["succeeded"] is True
    assert body["components"]["market_data"]["fresh"] is True
    assert body["components"]["market_data"]["basis"] == "newest_quote"


def test_stale_quote_is_not_fresh_market_data(tmp_path, monkeypatch):
    _paths(monkeypatch, tmp_path)
    old = (NOW - timedelta(minutes=20)).isoformat()
    (tmp_path / "feed_status.json").write_text(
        json.dumps({"market_status": {"last_success": NOW.isoformat(), "newest_quote": old}}),
        encoding="utf-8",
    )
    _code, body = build_ready(
        _settings(tmp_path),
        now=NOW,
        broker_probe=lambda _settings: {"status": "FAILED", "reachable": False, "detail": "unreachable", "latency_ms": 1},
    )
    assert body["components"]["market_data"]["fresh"] is False
    assert body["components"]["market_data"]["basis"] == "newest_quote"
