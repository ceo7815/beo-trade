import threading
import time
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_system_status_never_waits_for_a_slow_feed(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    from app.providers.registry import build_providers

    release = threading.Event()
    calls: list[int] = []

    class SlowFeed:
        name = "fred"

        def latest(self, as_of):
            calls.append(1)
            release.wait(5)

        def status_detail(self):
            return {"provider": "fred", "authenticated": None, "last_success": None, "last_fetch": None, "series_count": 0, "last_error": None}

    def slow_set(settings):
        built = build_providers(settings)
        built.macro = SlowFeed()
        return built

    monkeypatch.setattr("app.main.build_providers", slow_set)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    try:
        with TestClient(create_app(settings)) as client:
            started = time.perf_counter()
            for _ in range(3):
                assert client.get("/api/v1/system").status_code == 200
            assert time.perf_counter() - started < 3
            assert len(calls) == 1
    finally:
        release.set()


def test_dashboard_api_exposes_buys_only(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    database = tmp_path / "beo.db"
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + database.as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    with TestClient(create_app(settings)) as client:
        health = client.get("/health")
        assert health.status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200
        status = client.get("/api/v1/system")
        assert status.status_code == 200
        body = status.json()
        assert body["paper_only"] is True
        assert body["display_timezone"] == "Asia/Dubai"
        assert "suppress" not in body
        recommendations = client.get("/api/v1/recommendations")
        assert recommendations.json() == {"items": []}
        scan = client.post("/api/v1/scan")
        assert scan.status_code == 409
        missing = client.get("/api/v1/recommendations/does-not-exist")
        assert missing.status_code == 404
        added = client.post("/api/v1/watchlist", json={"symbol": "tsla"})
        assert added.status_code == 200
        listed = client.get("/api/v1/watchlist")
        assert listed.json()["items"][0]["symbol"] == "TSLA"
        account = client.get("/api/v1/paper/account")
        assert "return_percent" in account.json()
