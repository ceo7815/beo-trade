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
        built.research = None
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
        with TestClient(create_app(settings, allow_open_api=True)) as client:
            started = time.perf_counter()
            for _ in range(3):
                assert client.get("/api/v1/system").status_code == 200
            assert time.perf_counter() - started < 3
            assert len(calls) == 1
    finally:
        release.set()


def _feed_status():
    return {
        "provider": "fred",
        "authenticated": None,
        "last_success": None,
        "last_fetch": None,
        "series_count": 0,
        "last_error": None,
    }


def _local_settings(tmp_path, name: str) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )


def test_a_stuck_refresh_in_one_app_does_not_block_another(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    from app.providers.registry import build_providers

    release = threading.Event()
    calls: list[str] = []

    class Blocking:
        name = "fred"

        def latest(self, as_of):
            calls.append("blocking")
            release.wait(5)

        def status_detail(self):
            return _feed_status()

    class Other:
        name = "fred"

        def latest(self, as_of):
            calls.append("other")

        def status_detail(self):
            return _feed_status()

    holder = {"feed": Blocking}

    def slow_set(settings):
        built = build_providers(settings)
        built.macro = holder["feed"]()
        built.research = None
        return built

    monkeypatch.setattr("app.main.build_providers", slow_set)
    try:
        with TestClient(create_app(_local_settings(tmp_path, "blocked.db"), allow_open_api=True)) as blocked:
            assert blocked.get("/api/v1/system").status_code == 200
            holder["feed"] = Other
            with TestClient(create_app(_local_settings(tmp_path, "other.db"), allow_open_api=True)) as other:
                assert other.get("/api/v1/system").status_code == 200
                deadline = time.perf_counter() + 2
                while calls.count("other") < 1 and time.perf_counter() < deadline:
                    time.sleep(0.02)
                assert calls.count("other") == 1
    finally:
        release.set()


def test_a_stuck_feed_gives_the_refresh_gate_back(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main._REFRESH_LIMIT_SECONDS", 0.2)
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
            return _feed_status()

    def slow_set(settings):
        built = build_providers(settings)
        built.macro = SlowFeed()
        built.research = None
        return built

    monkeypatch.setattr("app.main.build_providers", slow_set)
    try:
        with TestClient(create_app(_local_settings(tmp_path, "stuck.db"), allow_open_api=True)) as client:
            assert client.get("/api/v1/system").status_code == 200
            deadline = time.perf_counter() + 2
            while len(calls) < 2 and time.perf_counter() < deadline:
                assert client.get("/api/v1/system").status_code == 200
                time.sleep(0.05)
            assert len(calls) >= 2
    finally:
        release.set()


def test_feed_card_shows_the_worker_counters_when_they_are_newer(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from app.providers.registry import merged_feed_statuses

    path = tmp_path / "feed_status.json"
    monkeypatch.setattr("app.ops.paths.FEED_STATUS", path)
    empty = {"provider": "thetadata", "last_fetch": None, "quote_count": 0}
    local = SimpleNamespace(
        feed_statuses=lambda: {
            "market_status": empty,
            "options_status": empty,
            "news_status": {"provider": "benzinga", "last_fetch": "2026-10-09T15:00:00-04:00", "article_count": 3},
            "macro_status": {"provider": "fred", "last_fetch": None},
            "research_status": {"provider": "sec", "last_fetch": None},
        }
    )
    assert merged_feed_statuses(local)["market_status"] == empty
    path.write_text(
        json.dumps(
            {
                "market_status": {"provider": "thetadata", "last_fetch": "2026-10-09T15:58:06-04:00", "quote_count": 40},
                "news_status": {"provider": "benzinga", "last_fetch": "2026-10-09T14:00:00-04:00", "article_count": 1},
                "macro_status": {"provider": "unconfigured", "last_fetch": "2026-10-09T15:00:00-04:00"},
            }
        ),
        encoding="utf-8",
    )
    merged = merged_feed_statuses(local)
    assert merged["market_status"]["quote_count"] == 40
    assert merged["news_status"]["article_count"] == 3
    assert merged["macro_status"]["provider"] == "fred"
    assert merged["options_status"] == empty


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
    with TestClient(create_app(settings, allow_open_api=True)) as client:
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
        assert recommendations.json()["items"] == []
        assert recommendations.json()["next_cursor"] is None
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
