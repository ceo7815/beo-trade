"""Read-only day-floor diagnostics: auth, payload shape, and worker diag write."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"
DIAG = "/api/v1/diag/day-floor"
NOW = datetime(2026, 10, 30, 14, 50, tzinfo=timezone.utc)


def _client(tmp_path, monkeypatch, admin: str = ADMIN, read: str = READ) -> TestClient:
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "diag.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=admin,
        api_read_key=read,
    )
    return TestClient(create_app(settings))


def _point_data_dir(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr("app.ops.paths.DATA_DIR", data)
    monkeypatch.setattr("app.ops.paths.HEARTBEAT", data / "worker_heartbeat.txt")
    monkeypatch.setattr("app.ops.paths.MONITOR_HEARTBEAT", data / "monitor_heartbeat.txt")
    monkeypatch.setattr("app.ops.paths.MONITOR_CYCLE", data / "monitor_cycle.json")
    monkeypatch.setattr("app.ops.paths.DAY_FLOOR_DIAG", data / "day_floor_diag.json")
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: data / "day_floor.json")
    monkeypatch.setattr(
        "app.options.day_floor._release_marker_path",
        lambda: data / "day_floor_false_hs_released_2026-10-30_v2",
    )
    monkeypatch.setattr("app.options.account_read.store_path", lambda: data / "account_read.json")
    return data


def test_diag_endpoint_requires_key(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get(DIAG).status_code == 401
        assert client.get(DIAG, headers={"X-Beo-Key": "wrong"}).status_code == 401


def test_diag_endpoint_allows_read_key_and_returns_shape(tmp_path, monkeypatch):
    data = _point_data_dir(tmp_path, monkeypatch)
    (data / "day_floor.json").write_text(
        json.dumps(
            {
                "session_date": "2026-10-30",
                "armed": False,
                "closed": True,
                "peak": None,
                "reason": "DAILY_HARD_STOP",
                "cleared": True,
            }
        ),
        encoding="utf-8",
    )
    (data / "day_floor_false_hs_released_2026-10-30_v2").write_text("marker\n", encoding="utf-8")
    (data / "monitor_cycle.json").write_text(
        json.dumps({"last_attempt": NOW.isoformat(), "last_success": NOW.isoformat(), "ok": True}),
        encoding="utf-8",
    )
    (data / "monitor_heartbeat.txt").write_text(f"{NOW.isoformat()} RTH\n", encoding="utf-8")
    (data / "worker_heartbeat.txt").write_text(f"{NOW.isoformat()} RTH\n", encoding="utf-8")
    (data / "account_read.json").write_text(
        json.dumps(
            {
                "consecutive_failures": 0,
                "last_success_at": NOW.isoformat(),
                "last_failure_at": None,
                "blocked": False,
                "alert_open": False,
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )
    (data / "day_floor_diag.json").write_text(
        json.dumps({"pnl": "-2.52", "already_closed": True, "maybe_release_called": True}),
        encoding="utf-8",
    )

    with _client(tmp_path, monkeypatch) as client:
        response = client.get(DIAG, headers={"X-Beo-Key": READ})
        assert response.status_code == 200
        body = response.json()
        for key in (
            "as_of",
            "session_date_target",
            "day_floor_store",
            "release_marker",
            "monitor_cycle",
            "monitor_heartbeat",
            "worker_heartbeat",
            "account_read",
            "day_latch_released_audit",
            "day_floor_diag",
        ):
            assert key in body, key
        assert body["day_floor_store"]["reason"] == "DAILY_HARD_STOP"
        assert body["release_marker"]["exists"] is True
        assert body["release_marker"]["path_name"] == "day_floor_false_hs_released_2026-10-30_v2"
        assert body["monitor_cycle"]["ok"] is True
        assert body["account_read"]["consecutive_failures"] == 0
        assert body["day_floor_diag"]["pnl"] == "-2.52"
        assert body["day_latch_released_audit"]["exists"] is False
        # No secrets in the payload.
        blob = json.dumps(body).lower()
        assert "api_key" not in blob
        assert "secret" not in blob
        assert "password" not in blob


def test_diag_endpoint_rejects_read_key_post(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post(DIAG, headers={"X-Beo-Key": READ}).status_code == 403


def test_worker_writes_day_floor_diag_each_open_cycle(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop, public_from_store
    from app.workers import loops

    data = _point_data_dir(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ops.day_floor_diag.diag_path", lambda: data / "day_floor_diag.json")

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "diag-write.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 10, 30, 14, 50, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)
    assert public_from_store(settings, moment)["closed"] is True

    class Adapter:
        def get_account(self):
            return {"equity": "97984.19", "last_equity": "102136.71"}

        def get_activities(self, *a, **k):
            return []

    monkeypatch.setattr(
        loops,
        "_load_tracked_day",
        lambda s, a, m, p=None: ({"equity": "97984.19"}, Decimal("-2.52"), True),
    )
    view = loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert view["closed"] is False

    diag_file = data / "day_floor_diag.json"
    assert diag_file.exists()
    payload = json.loads(diag_file.read_text(encoding="utf-8"))
    assert payload["session_open"] is True
    assert payload["read_ok"] is True
    assert payload["pnl"] == "-2.52"
    assert payload["maybe_release_called"] is True
    assert payload["maybe_release_returned"] is True
    assert payload["release_exception"] is None
    assert payload["day_flatten_reason"] in {None, ""}


def test_worker_records_release_exception_without_raising(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop
    from app.workers import loops

    data = _point_data_dir(tmp_path, monkeypatch)
    monkeypatch.setattr("app.ops.day_floor_diag.diag_path", lambda: data / "day_floor_diag.json")

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "diag-exc.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 10, 30, 14, 50, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)

    def boom(*_a, **_k):
        raise RuntimeError("diag-test-boom")

    monkeypatch.setattr("app.options.day_floor.maybe_release_false_hard_stop", boom)
    monkeypatch.setattr(
        loops,
        "_load_tracked_day",
        lambda s, a, m, p=None: ({"equity": "97984.19"}, Decimal("-2.52"), True),
    )

    class Adapter:
        def get_account(self):
            return {"equity": "97984.19"}

        def get_activities(self, *a, **k):
            return []

    view = loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert view["closed"] is True
    payload = json.loads((data / "day_floor_diag.json").read_text(encoding="utf-8"))
    assert payload["maybe_release_called"] is True
    assert payload["maybe_release_returned"] is None
    assert "RuntimeError" in (payload["release_exception"] or "")
