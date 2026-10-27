from decimal import Decimal

from fastapi.testclient import TestClient

from app.analytics.read_cache import read_cache
from app.config.settings import Settings
from app.live import stream as live
from app.main import create_app

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


def _client(tmp_path, monkeypatch, admin: str = ADMIN, read: str = READ) -> TestClient:
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "stream.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=admin,
        api_read_key=read,
    )
    return TestClient(create_app(settings))


def _frame(pnl: str = "-100.00") -> dict:
    return {
        "ts": "2026-10-27T12:00:00+00:00",
        "positions": {"items": [{"symbol": "NVDA261030C00185000", "underlying": "NVDA", "current_price": "3.85", "unrealized_pl": "260"}]},
        "account": {"equity": "70000.00", "cash": "50000.00", "last_equity": "70100.00"},
        "day": {
            "pnl": pnl,
            "mark_pnl": "-100.00",
            "pnl_source": "risk",
            "daily_loss_used": "100.00",
            "daily_loss_limit": "2800.00",
            "daily_kill_loss_usd": "3500.00",
            "daily_loss_state": "ok",
            "open_positions": 1,
            "day_floor": {"armed": False, "closed": False},
        },
        "status": {"market_phase": "CLOSED", "heartbeat_age_seconds": 4, "trading_halted": False, "paper_only": True, "feeds": []},
        "quotes": [{"symbol": "SPY", "price": "582.14", "change_percent": "0.42"}],
        "scan": {"stage": "done", "discovered": 12, "filtered": 4, "recommendations": 1, "buys": 0, "names": ["NVDA"]},
    }


def test_stream_rejects_a_missing_key(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/stream").status_code == 401
        assert client.get("/api/v1/stream", headers={"X-Beo-Key": "wrong"}).status_code == 401


def test_read_key_streams_and_cannot_post(tmp_path, monkeypatch):
    live.MAX_EVENTS = 1
    live.INTERVAL = 0
    monkeypatch.setattr(live, "build_snapshot", lambda settings: _frame())
    try:
        with _client(tmp_path, monkeypatch) as client:
            assert client.post("/api/v1/stream", headers={"X-Beo-Key": READ}).status_code == 403
            with client.stream("GET", "/api/v1/stream", headers={"X-Beo-Key": READ}) as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                assert response.headers["x-accel-buffering"] == "no"
                assert "no-transform" in response.headers["cache-control"]
                body = "".join(response.iter_text())
    finally:
        live.MAX_EVENTS = 0
        live.INTERVAL = 1.5
    assert "event: snapshot" in body
    assert '"daily_kill_loss_usd": "3500.00"' in body
    assert '"underlying": "NVDA"' in body
    assert "event: heartbeat" not in body or "snapshot" in body


def test_admin_key_streams_a_diff_when_the_frame_changes(tmp_path, monkeypatch):
    frames = [_frame("-100.00"), _frame("-120.00")]

    def sequenced(settings):
        return frames.pop(0) if frames else _frame("-120.00")

    live.MAX_EVENTS = 2
    live.INTERVAL = 0
    monkeypatch.setattr(live, "build_snapshot", sequenced)
    try:
        with _client(tmp_path, monkeypatch) as client:
            with client.stream("GET", "/api/v1/stream", headers={"X-Beo-Key": ADMIN}) as response:
                body = "".join(response.iter_text())
    finally:
        live.MAX_EVENTS = 0
        live.INTERVAL = 1.5
    assert "event: snapshot" in body
    assert "event: diff" in body
    assert '"pnl": "-120.00"' in body


def test_snapshot_reads_cache_sets_kill_dollars_and_does_not_write_the_floor(tmp_path, monkeypatch):
    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))

    def forbid_write(*args, **kwargs):
        raise AssertionError("stream must not write the day floor")

    monkeypatch.setattr("app.options.day_floor.observe_day_floor", forbid_write)
    monkeypatch.setattr(live, "_fetch_positions", lambda settings: {"items": [{"symbol": "X", "underlying": "AMD", "current_price": "1.10", "unrealized_pl": "12"}]})
    monkeypatch.setattr(live, "_fetch_account", lambda settings: {"equity": "70000", "cash": "1000", "last_equity": "69000", "buying_power": "1000"})
    read_cache.clear()
    live._mem["positions"] = (0.0, None)
    live._mem["account"] = (0.0, None)
    read_cache.set(
        ("risk",),
        {
            "today_pnl": "-250.50",
            "daily_loss_state": "ok",
            "daily_loss_used": "250.50",
            "daily_loss_limit": "2800.00",
            "open_positions": 1,
            "available": True,
        },
        30,
    )
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "snap.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    snap = live.build_snapshot(settings)
    assert snap["positions"]["items"][0]["underlying"] == "AMD"
    assert snap["day"]["pnl"] == "-250.50"
    assert snap["day"]["pnl_source"] == "risk"
    assert snap["day"]["daily_kill_loss_usd"] == "3500.00"
    assert snap["day"]["mark_pnl"] == "1000.00"
    assert "status" in snap and snap["status"]["market_phase"]
    assert "positions" in snap and "day" in snap and "quotes" in snap
