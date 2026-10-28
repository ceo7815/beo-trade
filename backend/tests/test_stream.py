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
    from app.broker import snapshot as broker_snapshot

    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))

    def forbid_write(*args, **kwargs):
        raise AssertionError("stream must not write the day floor")

    def forbid_broker(*args, **kwargs):
        raise AssertionError("stream must not call Alpaca")

    monkeypatch.setattr("app.options.day_floor.observe_day_floor", forbid_write)
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", forbid_broker)
    broker_snapshot.isolate(tmp_path)
    broker_snapshot.prime(
        {
            "positions": [{"symbol": "X", "underlying": "AMD", "current_price": "1.10", "unrealized_pl": "12"}],
            "account": {"equity": "70000", "cash": "1000", "last_equity": "69000", "buying_power": "1000"},
        }
    )
    read_cache.clear()
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
    # 70,000 × 5% is also 3,500. A different equity must still read the dollar line.
    broker_snapshot.prime(
        {
            "positions": [{"symbol": "X", "underlying": "AMD", "current_price": "1.10", "unrealized_pl": "12"}],
            "account": {"equity": "100000", "cash": "1000", "last_equity": "90000", "buying_power": "1000"},
        }
    )
    larger = live.build_snapshot(settings)
    assert larger["day"]["mark_pnl"] == "10000.00"
    assert larger["day"]["daily_kill_loss_usd"] == "3500.00"
    assert snap["day"]["mark_pnl"] == "1000.00"
    assert "status" in snap and snap["status"]["market_phase"]
    assert "positions" in snap and "day" in snap and "quotes" in snap


def test_snapshot_reports_hard_stop_at_the_dollar_kill(tmp_path, monkeypatch):
    from app.broker import snapshot as broker_snapshot

    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stream must not write the day floor")))
    broker_snapshot.isolate(tmp_path)
    broker_snapshot.prime(
        {
            "positions": [],
            "account": {"equity": "100000", "cash": "1", "last_equity": "103500", "buying_power": "1"},
        }
    )
    read_cache.clear()
    read_cache.set(("risk",), {"today_pnl": "-3500.00", "daily_loss_state": "ok", "available": True}, 30)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "kill-stop.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    snap = live.build_snapshot(settings)
    assert snap["day"]["daily_kill_loss_usd"] == "3500.00"
    assert snap["day"]["daily_loss_state"] == "hard_stop"
    read_cache.set(("risk",), {"today_pnl": "-3499.00", "daily_loss_state": "ok", "available": True}, 30)
    short = live.build_snapshot(settings)
    assert short["day"]["daily_loss_state"] == "ok"


def test_kill_line_falls_back_to_five_percent_only_when_dollars_are_off(tmp_path):
    from app.analytics.kill_line import display_kill_usd

    path = tmp_path / "trading.toml"
    path.write_text("daily_kill_loss_usd = 0\ndaily_hard_stop_pct = 0.05\n", encoding="utf-8")
    trading = Settings(trading_config_path=str(path)).trading()
    assert display_kill_usd(trading, Decimal("80000")) == "4000.00"
    assert display_kill_usd(trading, None) is None
    configured = Settings().trading()
    assert display_kill_usd(configured, Decimal("100000")) == "3500.00"
    assert display_kill_usd(configured, None) == "3500.00"


def test_snapshot_day_return_uses_the_shown_pnl_and_capital_opened_today(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.broker import snapshot as broker_snapshot

    monkeypatch.setattr(
        "app.options.protection_view.display_moment",
        lambda: datetime(2026, 10, 28, 18, 0, tzinfo=timezone.utc),
    )

    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stream must not write the day floor")))
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stream must not call Alpaca")))
    broker_snapshot.isolate(tmp_path)
    broker_snapshot.prime(
        {
            "positions": [],
            "account": {"equity": "101840", "cash": "1", "last_equity": "100000", "buying_power": "1"},
        }
    )
    read_cache.clear()
    session = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    opened = session.replace(hour=10, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    yesterday = session.replace(hour=10, minute=0, second=0, microsecond=0)
    from datetime import timedelta

    prior = (yesterday - timedelta(days=1)).astimezone(timezone.utc).isoformat()
    read_cache.set(("risk",), {"today_pnl": "1.00", "daily_loss_state": "ok", "available": True}, 30)
    read_cache.set(
        ("trades", "today", "", ""),
        {
            "available": True,
            "closed": [{"opened_at": opened, "entry_price": "2.00", "qty": "10"}],
            "open": [{"opened_at": prior, "entry_price": "8.00", "qty": "10"}],
        },
        45,
    )
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "return.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    snap = live.build_snapshot(settings)
    assert snap["day"]["mark_pnl"] == "1840.00"
    assert snap["day"]["display_pnl"] == "1840.00"
    assert snap["day"]["day_pnl_note"] is None
    assert snap["day"]["protection"]["pnl"] == "1840.00"
    assert snap["day"]["day_invested_usd"] == "2000.00"
    assert snap["day"]["day_return_pct"] == "92.00"
    read_cache.set(("risk",), {"today_pnl": None, "daily_loss_state": "unknown", "available": True}, 30)
    blind = live.build_snapshot(settings)
    assert blind["day"]["day_return_pct"] is None
    assert blind["day"]["day_invested_usd"] is None
    assert blind["day"]["display_pnl"] is None
    read_cache.clear()


def test_preopen_snapshot_shows_a_fresh_day_without_changing_the_raw_mark(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from app.broker import snapshot as broker_snapshot

    monkeypatch.setattr("app.providers.registry.resolve_secret", lambda settings, name: ("", "missing"))
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stream must not write the day floor")))
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("stream must not call Alpaca")))
    monkeypatch.setattr(
        "app.options.protection_view.display_moment",
        lambda: datetime(2026, 10, 28, 7, 50, tzinfo=timezone.utc),
    )
    broker_snapshot.isolate(tmp_path)
    broker_snapshot.prime(
        {
            "positions": [],
            "account": {"equity": "107367.00", "cash": "1", "last_equity": "100000.00", "buying_power": "1"},
        }
    )
    read_cache.clear()
    read_cache.set(("risk",), {"today_pnl": "7367.00", "daily_loss_state": "ok", "available": True}, 30)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "fresh.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    snap = live.build_snapshot(settings)
    assert snap["day"]["pnl"] == "7367.00"
    assert snap["day"]["mark_pnl"] == "7367.00"
    assert snap["day"]["daily_loss_state"] == "ok"
    assert snap["day"]["display_pnl"] == "0.00"
    assert snap["day"]["day_pnl_note"] == "יום חדש — מתאפס בפתיחה"
    assert snap["day"]["protection"]["pnl"] == "0.00"
    assert snap["day"]["protection"]["line"] == "עוד 4,000$ (ב-4,000$) נכנסת הגנה של 2,000$"
    assert "עוד 0$" not in snap["day"]["protection"]["line"]
    assert snap["day"]["day_return_pct"] is None
    read_cache.clear()
