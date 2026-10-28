"""Account-read failures must not look like a flat day, and the ladder text follows the configured steps."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings, TradingConfig
from app.options.account_read import BLOCK_REASON, apply_failure, apply_success, entry_block_reason, note_failure, public_status
from app.options.protection_view import preview_at

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
NEAR_CLOSE = datetime(2026, 10, 20, 19, 50, tzinfo=timezone.utc)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr("app.options.account_read.store_path", lambda: tmp_path / "account_read.json")
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")


def _desk(tmp_path, monkeypatch, name: str):
    from app.models.db import configure_database, init_db

    _isolate(monkeypatch, tmp_path)
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    configure_database(settings)
    init_db(settings)
    return settings


class _Blind:
    """Alpaca answers 502. The open contract is still quoted from the position mark."""

    def __init__(self):
        self.calls = 0

    def sync_positions(self):
        return [{"symbol": "NVDA261020C00100000", "qty": "1", "avg_entry_price": "2.00", "current_price": "2.34", "trade_id": "t-blind"}]

    def get_market_clock(self):
        return {"is_open": True}

    def get_account(self):
        self.calls += 1
        raise RuntimeError("HTTPStatusError 502")

    def get_activities(self):
        raise RuntimeError("HTTPStatusError 502")

    def get_orders(self, status="open"):
        return []

    def close(self):
        return None


class _Book:
    def __init__(self, equity: str, last_equity: str = "100000"):
        self.equity = equity
        self.last_equity = last_equity
        self.canceled: list[str] = []
        self.submitted: list[dict] = []
        self.orders = [
            {"position_intent": "buy_to_open", "broker_order_id": "buy-1", "symbol": "AMD261020C00100000", "internal_state": "ACCEPTED"},
            {"position_intent": "sell_to_close", "broker_order_id": "old-sell", "symbol": "NVDA261020C00100000", "internal_state": "ACCEPTED", "limit_price": "1.50"},
        ]
        self.by_client: dict[str, dict] = {}

    def sync_positions(self):
        return [
            {"symbol": "NVDA261020C00100000", "qty": "1", "avg_entry_price": "2.00", "current_price": "1.20", "trade_id": "t-nvda"},
            {"symbol": "QQQ261020P00450000", "qty": "2", "avg_entry_price": "3.00", "current_price": "2.34", "trade_id": "t-qqq"},
        ]

    def get_positions(self):
        return self.sync_positions()

    def get_market_clock(self):
        return {"is_open": True}

    def get_account(self):
        return {"equity": self.equity, "last_equity": self.last_equity, "status": "ACTIVE", "trading_blocked": False}

    def get_activities(self):
        return []

    def get_orders(self, status="open"):
        return list(self.orders)

    def cancel_order(self, order_id):
        self.canceled.append(order_id)
        self.orders = [row for row in self.orders if str(row.get("broker_order_id") or "") != str(order_id)]

    def get_order_by_client_id(self, client_order_id):
        return self.by_client.get(client_order_id)

    def get_asset(self, symbol):
        return {"tradable": True, "status": "active", "has_options": True}

    def submit_exit_order(self, body):
        order = {
            "broker_order_id": f"sell-{len(self.submitted) + 1}",
            "client_order_id": body.get("client_order_id"),
            "symbol": body.get("symbol"),
            "position_intent": "sell_to_close",
            "internal_state": "ACCEPTED",
            "limit_price": body.get("limit_price"),
            "status": "accepted",
        }
        self.submitted.append(body)
        self.orders.append(order)
        if body.get("client_order_id"):
            self.by_client[body["client_order_id"]] = order
        return order

    def close(self):
        return None


def test_two_misses_stay_unknown_and_the_third_blocks_new_buys(tmp_path, monkeypatch):
    from app.models.db import session_scope
    from app.models.tables import SystemEvent
    from app.workers.loops import monitor_once, scan_once

    settings = _desk(tmp_path, monkeypatch, "blind.db")
    blind = _Blind()
    first = monitor_once(settings, {}, blind, NOW, submit=False)
    second = monitor_once(settings, {}, blind, NOW + timedelta(minutes=1), submit=False)
    assert first["exits"] == []
    assert second["exits"] == []
    assert public_status()["status"] == "unknown"
    assert public_status()["consecutive_failures"] == 2
    assert entry_block_reason() is None

    third = monitor_once(settings, {}, blind, NEAR_CLOSE, submit=False)
    assert third["exits"][0]["reason"] == "SESSION_CLOSE"
    assert third["exits"][0]["symbol"] == "NVDA261020C00100000"
    assert entry_block_reason() == BLOCK_REASON
    assert public_status()["blocked"] is True
    assert public_status()["alert_open"] is True
    assert scan_once(settings, NEAR_CLOSE) == {"ok": False, "blocked": BLOCK_REASON}

    monitor_once(settings, {}, blind, NEAR_CLOSE + timedelta(minutes=1), submit=False)
    with session_scope() as session:
        opened = list(session.scalars(select(SystemEvent).where(SystemEvent.event_type == BLOCK_REASON)))
    assert len(opened) == 1
    assert opened[0].payload["resolved"] is False


def test_the_first_good_read_after_a_blind_streak_kills_a_3500_loss(tmp_path, monkeypatch):
    from app.models.db import session_scope
    from app.models.tables import SystemEvent
    from app.options.day_floor import day_close_reason
    from app.workers.loops import monitor_once

    settings = _desk(tmp_path, monkeypatch, "recover.db")
    blind = _Blind()
    for minute in range(3):
        monitor_once(settings, {}, blind, NOW + timedelta(minutes=minute), submit=False)
    assert entry_block_reason() == BLOCK_REASON

    quotes = {"NVDA261020C00100000": {"option": type("Quote", (), {"bid": Decimal("0.70")})()}}
    book = _Book("96500")
    result = monitor_once(settings, quotes, book, NOW + timedelta(minutes=30), submit=True)
    assert {row["reason"] for row in result["exits"]} == {"DAILY_HARD_STOP"}
    assert set(book.canceled) == {"buy-1", "old-sell"}
    assert day_close_reason(settings, NOW + timedelta(minutes=31)) == "DAILY_HARD_STOP"
    assert entry_block_reason() is None
    assert public_status()["consecutive_failures"] == 0
    assert public_status()["alert_open"] is False
    assert public_status()["last_success_at"]
    with session_scope() as session:
        kinds = [row.event_type for row in session.scalars(select(SystemEvent).order_by(SystemEvent.created_at))]
    assert kinds == [BLOCK_REASON, "ACCOUNT_DATA_RESOLVED"]
    assert TradingConfig().daily_kill_loss_usd == 3500


def test_a_failed_monitor_cycle_does_not_move_the_heartbeat(tmp_path, monkeypatch):
    from app.workers.runner import beat, run_monitor_cycle

    heart = tmp_path / "worker_heartbeat.txt"
    monitor = tmp_path / "monitor_heartbeat.txt"
    monkeypatch.setattr("app.workers.runner.HEARTBEAT", heart)
    monkeypatch.setattr("app.workers.runner.MONITOR_HEARTBEAT", monitor)
    monkeypatch.setattr("app.ops.paths.MONITOR_CYCLE", tmp_path / "monitor_cycle.json")
    beat(heart, "OPEN")
    beat(monitor, "OPEN")
    before_heart = heart.read_text(encoding="utf-8")
    before_monitor = monitor.read_text(encoding="utf-8")

    def boom(_settings):
        raise RuntimeError("502")

    monkeypatch.setattr("app.workers.runner.monitor_once", boom)
    assert run_monitor_cycle(Settings(), "OPEN") is False
    assert heart.read_text(encoding="utf-8") == before_heart
    assert monitor.read_text(encoding="utf-8") == before_monitor
    cycle = json.loads((tmp_path / "monitor_cycle.json").read_text(encoding="utf-8"))
    assert cycle["ok"] is False
    assert cycle["last_success"] is None

    monkeypatch.setattr("app.workers.runner.monitor_once", lambda _settings: {"positions": 0, "exits": []})
    assert run_monitor_cycle(Settings(), "OPEN") is True
    assert heart.read_text(encoding="utf-8") != before_heart
    assert monitor.read_text(encoding="utf-8") != before_monitor
    done = json.loads((tmp_path / "monitor_cycle.json").read_text(encoding="utf-8"))
    assert done["ok"] is True
    assert done["last_success"]


def test_risk_endpoint_reports_unknown_when_the_account_read_fails(tmp_path, monkeypatch):
    from app.analytics.read_cache import read_cache
    from app.main import create_app

    _isolate(monkeypatch, tmp_path)
    note_failure(NOW)
    note_failure(NOW + timedelta(seconds=30))
    monkeypatch.setattr("app.analytics.routes.open_paper_broker", lambda _settings: (_ for _ in ()).throw(RuntimeError("HTTPStatusError 502")))
    read_cache.clear()
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "risk.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
    )
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        response = client.get("/api/v1/risk")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_loss_state"] == "unknown"
    assert body["state"] == "לא ידוע"
    assert body["daily_loss_used"] is None
    assert body["today_pnl"] is None
    assert body["account_read_failures"] == 2
    assert body["account_read_last_success"] is None
    assert body["protection"]["status"] == "unknown"
    assert body["protection"]["line"] == "לא ידוע"
    assert body["protection"]["floor"] is None
    assert body["protection"]["distance"] is None
    assert body["protection"]["next_threshold"] is None
    read_cache.clear()


def test_the_protection_line_follows_the_configured_ladder():
    steps = TradingConfig().day_floor_steps
    assert steps[0] == (4000, 2000)
    assert steps[1] == (7000, 3000)
    assert steps[-1] == (15000, 11000)
    assert TradingConfig().daily_kill_loss_usd == 3500

    lost = preview_at(Decimal("-1000"))
    assert lost["protection_kind"] == "kill"
    assert lost["floor"] is None
    assert lost["next_threshold"] == "4000.00"
    assert lost["next_floor"] == "2000.00"
    assert lost["distance"] == "5000.00"
    assert lost["line"] == "עוד 5,000$ (ב-4,000$) נכנסת הגנה של 2,000$"

    early = preview_at(Decimal("1000"))
    assert early["protection_kind"] == "kill"
    assert early["distance"] == "3000.00"
    assert early["line"] == "עוד 3,000$ (ב-4,000$) נכנסת הגנה של 2,000$"

    locked = preview_at(Decimal("5500"))
    assert locked["floor"] == "2000.00"
    assert locked["peak"] == "5500.00"
    assert locked["next_threshold"] == "7000.00"
    assert locked["next_floor"] == "3000.00"
    assert locked["distance"] == "1500.00"
    assert locked["line"] == "עוד 1,500$ (ב-7,000$) ההגנה עולה ל-3,000$"

    seven = preview_at(Decimal("7500"))
    assert seven["floor"] == "3000.00"
    assert seven["next_threshold"] == "8000.00"
    assert seven["next_floor"] == "4000.00"
    assert seven["distance"] == "500.00"

    eight = preview_at(Decimal("8200"))
    assert eight["floor"] == "4000.00"
    assert eight["next_threshold"] == "9000.00"
    assert eight["next_floor"] == "5000.00"
    assert eight["distance"] == "800.00"
    assert eight["line"] == "עוד 800$ (ב-9,000$) ההגנה עולה ל-5,000$"

    top = preview_at(Decimal("15000"))
    assert top["floor"] == "11000.00"
    assert top["next_threshold"] is None
    assert top["next_floor"] is None
    assert top["distance"] is None
    assert top["line"] == "ההגנה נעולה על 11,000$"

    state = apply_failure(None, NOW)
    state = apply_failure(state, NOW)
    assert state["blocked"] is False
    assert apply_failure(state, NOW)["blocked"] is True
    assert apply_success(state, NOW)["blocked"] is False
    assert apply_success(state, NOW)["alert_was_open"] is False
