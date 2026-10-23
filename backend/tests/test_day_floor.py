from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import Settings, TradingConfig
from app.options.day_floor import advance, floor_sell_price, observe_day_floor
from app.positions.admission import exit_blocks_reentry

DAY = "2026-10-20"
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)


def test_the_floor_steps_up_with_the_peak_and_does_not_fall():
    armed = advance(None, DAY, Decimal("4000"))
    assert armed["armed"] is True
    held = advance(armed, DAY, Decimal("6900"))
    assert advance(held, DAY, Decimal("2500"))["closed"] is False
    seven = advance(held, DAY, Decimal("7000"))
    assert advance(seven, DAY, Decimal("3001"))["closed"] is False
    assert advance(seven, DAY, Decimal("3000"))["closed"] is True
    eight = advance(held, DAY, Decimal("8000"))
    assert eight["peak"] == "8000.00"
    assert advance(eight, DAY, Decimal("4001"))["closed"] is False
    assert advance(eight, DAY, Decimal("4000"))["closed"] is True
    top = advance(None, DAY, Decimal("16000"))
    assert advance(top, DAY, Decimal("11001"))["closed"] is False
    assert advance(top, DAY, Decimal("11000"))["closed"] is True


def test_touching_2000_closes_the_day_and_a_bounce_does_not_reopen_it():
    armed = advance(None, DAY, Decimal("4000"))
    closed = advance(armed, DAY, Decimal("2000"))
    assert closed["closed"] is True
    bounced = advance(closed, DAY, Decimal("9000"))
    assert bounced["closed"] is True
    assert bounced["armed"] is True


def test_a_day_that_never_reached_4000_does_not_close():
    state = advance(None, DAY, Decimal("2000"))
    assert state["armed"] is False
    assert state["closed"] is False


def test_a_missing_mark_does_not_release_the_latch():
    armed = advance(None, DAY, Decimal("4500"))
    held = advance(armed, DAY, None)
    assert held["armed"] is True
    assert held["closed"] is False


def test_a_new_session_starts_clear():
    monday = advance(None, DAY, Decimal("6000"))
    tuesday = advance(monday, "2026-10-21", Decimal("100"))
    assert tuesday["armed"] is False
    assert tuesday["closed"] is False


def test_floor_exit_does_not_block_the_name_the_next_day():
    assert exit_blocks_reentry("DAY_FLOOR 2.10 day-floor") is False


def test_sell_uses_the_bid_and_otherwise_the_discounted_mark():
    assert floor_sell_price(Decimal("1.20"), Decimal("1.40"), Decimal("0.10")) == Decimal("1.20")
    assert floor_sell_price(None, Decimal("2.34"), Decimal("0.10")) == Decimal("2.10")
    assert floor_sell_price(None, None, Decimal("0.10")) is None


def test_a_closed_session_does_not_arm_from_a_stale_mark(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "floor.db").as_posix(), auth_required=False, trading_mode="PAPER")
    view = observe_day_floor(settings, Decimal("8000"), NOW, False)
    assert view["armed"] is False
    assert not (tmp_path / "day_floor.json").exists()


def test_monitor_sells_when_the_floor_is_touched(tmp_path, monkeypatch):
    from app.models.db import configure_database, init_db
    from app.workers.loops import monitor_once

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "monitor.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    symbol = "NVDA261020C00100000"

    class Adapter:
        def __init__(self, pnl: str):
            self.pnl = pnl
            self.canceled = []

        def sync_positions(self):
            return [{"symbol": symbol, "qty": "1", "avg_entry_price": "1.00", "current_price": "1.40", "trade_id": "t-floor"}]

        def get_market_clock(self):
            return {"is_open": True}

        def get_account(self):
            return {"equity": self.pnl, "last_equity": "0"}

        def get_activities(self):
            return []

        def get_orders(self, status="open"):
            return [{"position_intent": "buy_to_open", "broker_order_id": "buy-1", "symbol": symbol}]

        def cancel_order(self, order_id):
            self.canceled.append(order_id)

        def close(self):
            return None

    armed = Adapter("5000")
    first = monitor_once(settings, {}, armed, NOW, submit=False)
    assert first["exits"] == []
    assert armed.canceled == []
    closed = Adapter("2000")
    second = monitor_once(settings, {}, closed, NOW + timedelta(minutes=20), submit=True)
    assert second["exits"][0]["reason"] == "DAY_FLOOR"
    assert closed.canceled == ["buy-1"]


def test_an_open_session_arms_and_then_closes(tmp_path, monkeypatch):
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "floor.db").as_posix(), auth_required=False, trading_mode="PAPER")
    armed = observe_day_floor(settings, Decimal("4000"), NOW, True)
    assert armed == {"armed": True, "closed": False, "arm": "4000.00", "floor": "2000.00"}
    raised = observe_day_floor(settings, Decimal("9000"), NOW + timedelta(minutes=10), True)
    assert raised == {"armed": True, "closed": False, "arm": "9000.00", "floor": "5000.00"}
    closed = observe_day_floor(settings, Decimal("1500"), NOW + timedelta(minutes=30), True)
    assert closed["armed"] is True
    assert closed["closed"] is True
    assert TradingConfig().day_floor_arm == 4000
    assert TradingConfig().day_floor_lock == 2000
    assert TradingConfig().day_floor_steps[-1] == (15000, 11000)
