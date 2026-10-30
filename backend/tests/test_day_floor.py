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


def test_writing_the_day_latch_drops_the_read_cache(tmp_path, monkeypatch):
    from app.analytics.read_cache import read_cache
    from app.options.day_floor import latch_hard_stop

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "floor.db").as_posix(), auth_required=False, trading_mode="PAPER")
    read_cache.set(("risk", "now"), {"available": True, "loss": "ok"}, 30)
    read_cache.set(("trades", "all"), {"available": True, "closed": []}, 45)
    quiet = observe_day_floor(settings, Decimal("100"), NOW, True)
    assert quiet["closed"] is False
    assert read_cache.get(("risk", "now"))["loss"] == "ok"
    closed = latch_hard_stop(settings, NOW)
    assert closed["reason"] == "DAILY_HARD_STOP"
    assert read_cache.get(("risk", "now")) is None
    assert read_cache.get(("trades", "all")) is None
    read_cache.clear()


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
    assert armed == {"armed": True, "closed": False, "arm": "4000.00", "floor": "2000.00", "reason": "", "cleared": False}
    raised = observe_day_floor(settings, Decimal("9000"), NOW + timedelta(minutes=10), True)
    assert raised == {"armed": True, "closed": False, "arm": "9000.00", "floor": "5000.00", "reason": "", "cleared": False}
    closed = observe_day_floor(settings, Decimal("1500"), NOW + timedelta(minutes=30), True)
    assert closed["armed"] is True
    assert closed["closed"] is True
    assert closed["reason"] == "DAY_FLOOR"
    assert closed["cleared"] is False
    assert TradingConfig().day_floor_arm == 4000
    assert TradingConfig().day_floor_lock == 2000
    assert TradingConfig().day_floor_steps[-1] == (15000, 11000)


def test_the_dollar_kill_is_configured_at_3500():
    from app.config.settings import load_trading_config
    from app.options.risk_limits import day_flatten_reason

    config = TradingConfig()
    assert config.daily_kill_loss_usd == 3500
    assert load_trading_config().daily_kill_loss_usd == 3500
    assert day_flatten_reason(Decimal("100000"), Decimal("-3500"), config) == "DAILY_HARD_STOP"
    assert day_flatten_reason(Decimal("100000"), Decimal("-3499"), config) is None
    assert day_flatten_reason(Decimal("38000"), Decimal("-2000"), config) == "DAILY_HARD_STOP"
    assert day_flatten_reason(Decimal("100000"), None, config) is None
    assert exit_blocks_reentry("DAILY_HARD_STOP 0.70 hard-stop") is False


class _Book:
    def __init__(self, equity: str, last_equity: str = "100000", open_clock: bool = True):
        self.equity = equity
        self.last_equity = last_equity
        self.open_clock = open_clock
        self.canceled: list[str] = []
        self.submitted: list[dict] = []
        self.entries: list[dict] = []
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
        return {"is_open": self.open_clock}

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

    def submit_entry_order(self, body):
        self.entries.append(body)
        return {"broker_order_id": "buy-new", "internal_state": "ACCEPTED"}

    def close(self):
        return None


def _desk(tmp_path, monkeypatch, name: str):
    from app.models.db import configure_database, init_db

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    return settings


def test_monitor_sells_at_a_3500_loss_including_unrealized_and_does_not_sell_twice(tmp_path, monkeypatch):
    from app.models.db import session_scope
    from app.models.tables import BrokerTradeEvent
    from app.options.day_floor import day_close_reason
    from app.workers.loops import monitor_once, scan_once
    from sqlalchemy import select

    settings = _desk(tmp_path, monkeypatch, "kill.db")
    quotes = {"NVDA261020C00100000": {"option": type("Quote", (), {"bid": Decimal("0.70")})()}}
    shy = _Book("96501")
    assert monitor_once(settings, quotes, shy, NOW, submit=True)["exits"] == []
    assert shy.submitted == []
    assert shy.canceled == []
    assert day_close_reason(settings, NOW) is None

    # Equity is 3,500 under last_equity. No fills today: the loss is the open book.
    book = _Book("96500")
    first = monitor_once(settings, quotes, book, NOW + timedelta(minutes=5), submit=True)
    reasons = {row["symbol"]: row["reason"] for row in first["exits"]}
    assert reasons == {"NVDA261020C00100000": "DAILY_HARD_STOP", "QQQ261020P00450000": "DAILY_HARD_STOP"}
    prices = {row["symbol"]: row.get("limit_price") for row in book.submitted}
    assert prices == {"NVDA261020C00100000": "0.70", "QQQ261020P00450000": "2.10"}
    assert book.canceled == ["buy-1", "old-sell"]
    assert day_close_reason(settings, NOW + timedelta(minutes=6)) == "DAILY_HARD_STOP"

    again = monitor_once(settings, quotes, book, NOW + timedelta(minutes=10), submit=True)
    assert [row["reason"] for row in again["exits"]] == ["DAILY_HARD_STOP", "DAILY_HARD_STOP"]
    assert len(book.submitted) == 2
    assert book.canceled == ["buy-1", "old-sell"]
    with session_scope() as session:
        triggered = [row.reason for row in session.scalars(select(BrokerTradeEvent).where(BrokerTradeEvent.state == "EXIT_TRIGGERED"))]
    assert triggered == ["DAILY_HARD_STOP 0.70 hard-stop", "DAILY_HARD_STOP 2.10 hard-stop"]

    book.orders = [row for row in book.orders if row.get("position_intent") != "sell_to_close"]
    monitor_once(settings, quotes, book, NOW + timedelta(minutes=12), submit=True)
    assert len(book.submitted) == 2

    bounced = _Book("99000")
    bounced.orders = []
    bounced.by_client = {}
    bounce = monitor_once(settings, quotes, bounced, NOW + timedelta(minutes=20), submit=False)
    assert bounce["exits"][0]["reason"] == "DAILY_HARD_STOP"
    assert bounced.submitted == []

    from app.broker.execution import place_order
    from app.integrations.alpaca import PaperOnlyError

    with session_scope() as session:
        try:
            place_order(
                settings,
                session,
                book,
                {"symbol": "AMD261020C00150000", "qty": 1, "limit_price": "1.00", "position_intent": "buy_to_open", "approved": False, "system_validated": True},
                NOW + timedelta(minutes=21),
            )
        except PaperOnlyError as exc:
            assert "DAILY_HARD_STOP" in str(exc)
        else:
            raise AssertionError("buy was accepted after the kill")
    assert book.entries == []
    assert scan_once(settings, NOW + timedelta(minutes=22))["blocked"] == "DAILY_HARD_STOP"

    nxt = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
    flat = _Book("100000", "100000")
    flat.orders = []
    reset = monitor_once(settings, {}, flat, nxt, submit=True)
    assert reset["exits"] == []
    assert flat.submitted == []
    assert day_close_reason(settings, nxt) is None
    with session_scope() as session:
        try:
            place_order(
                settings,
                session,
                flat,
                {"symbol": "AMD261020C00150000", "qty": 1, "limit_price": "1.00", "position_intent": "buy_to_open", "approved": False, "system_validated": True},
                nxt,
            )
        except PaperOnlyError as exc:
            assert "DAILY_HARD_STOP" not in str(exc)
        else:
            raise AssertionError("the next session still needs a real entry check")
    assert scan_once(settings, nxt)["blocked"] != "DAILY_HARD_STOP"


def test_the_percent_hard_stop_flattens_before_the_dollar_kill(tmp_path, monkeypatch):
    from app.workers.loops import monitor_once

    settings = _desk(tmp_path, monkeypatch, "percent.db")
    book = _Book("38000", "40000")
    result = monitor_once(settings, {}, book, NOW, submit=False)
    assert {row["reason"] for row in result["exits"]} == {"DAILY_HARD_STOP"}
    assert len(result["exits"]) == 2


def test_a_closed_session_does_not_latch_the_kill(tmp_path, monkeypatch):
    from app.options.day_floor import day_is_closed
    from app.workers.loops import monitor_once

    settings = _desk(tmp_path, monkeypatch, "closed.db")
    book = _Book("90000", open_clock=False)
    assert monitor_once(settings, {}, book, NOW, submit=False)["exits"] == []
    assert day_is_closed(settings, NOW) is False
    assert book.canceled == []


def test_a_normal_stop_still_fires_when_the_kill_is_not_hit(tmp_path, monkeypatch):
    from app.schemas.domain import OptionRight, OptionSnapshot, UnderlyingSnapshot
    from app.workers.loops import monitor_once

    settings = _desk(tmp_path, monkeypatch, "stop.db")
    symbol = "NVDA261022C00100000"
    option = OptionSnapshot(
        "NVDA",
        symbol,
        OptionRight.CALL,
        Decimal("100"),
        NOW.date() + timedelta(days=2),
        Decimal("0.50"),
        Decimal("0.52"),
        Decimal("0.51"),
        800,
        500,
        NOW,
        Decimal("0.40"),
        Decimal("0.40"),
        Decimal("0.02"),
        Decimal("-0.02"),
        Decimal("0.10"),
    )
    underlying = UnderlyingSnapshot("NVDA", Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), 2_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal("100"), NOW, True)

    class Adapter(_Book):
        def sync_positions(self):
            return [{"symbol": symbol, "qty": "1", "avg_entry_price": "1.00", "current_price": "0.50", "trade_id": "t-stop"}]

    book = Adapter("99000")
    result = monitor_once(settings, {symbol: {"option": option, "underlying": underlying}}, book, NOW, submit=False)
    assert result["exits"][0]["reason"] == "STOP"
    assert book.submitted == []


def test_false_hard_stop_release_only_on_target_day(tmp_path, monkeypatch):
    from app.options.day_floor import (
        FALSE_HARD_STOP_RELEASE_DATE,
        latch_hard_stop,
        maybe_release_false_hard_stop,
        public_from_store,
    )

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "rel.db").as_posix(), auth_required=False, trading_mode="PAPER")
    # Latch on the bug day.
    moment = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)
    assert public_from_store(settings, moment)["closed"] is True
    # Healthy adjusted P&L → release.
    out = maybe_release_false_hard_stop(settings, moment, Decimal("-2.52"), Decimal("100000"))
    assert out is not None
    assert out["closed"] is False
    assert out["armed"] is False
    assert public_from_store(settings, moment)["closed"] is False
    # Idempotent: second call is no-op (marker).
    assert maybe_release_false_hard_stop(settings, moment, Decimal("-2.52"), Decimal("100000")) is None


def test_false_hard_stop_release_noop_other_date_or_real_loss(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop, maybe_release_false_hard_stop, public_from_store

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "rel2.db").as_posix(), auth_required=False, trading_mode="PAPER")
    other = datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)
    latch_hard_stop(settings, other)
    assert maybe_release_false_hard_stop(settings, other, Decimal("0"), Decimal("100000")) is None
    assert public_from_store(settings, other)["closed"] is True

    bug_day = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    # Fresh store for bug day
    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor_b.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release_b.marker")
    latch_hard_stop(settings, bug_day)
    # Real adjusted loss beyond -3500 → keep latch.
    assert maybe_release_false_hard_stop(settings, bug_day, Decimal("-4000"), Decimal("100000")) is None
    assert public_from_store(settings, bug_day)["closed"] is True


def test_after_false_release_real_loss_still_latches(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop, maybe_release_false_hard_stop, public_from_store
    from app.options.risk_limits import day_flatten_reason

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "rel3.db").as_posix(), auth_required=False, trading_mode="PAPER")
    moment = datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)
    maybe_release_false_hard_stop(settings, moment, Decimal("-2.52"), Decimal("100000"))
    assert public_from_store(settings, moment)["closed"] is False
    # Kill logic still active.
    assert day_flatten_reason(Decimal("100000"), Decimal("-3500"), TradingConfig()) == "DAILY_HARD_STOP"
    latch_hard_stop(settings, moment)
    assert public_from_store(settings, moment)["closed"] is True
    assert public_from_store(settings, moment)["reason"] == "DAILY_HARD_STOP"
    # Auto-release will not clear a post-release real latch (marker exists).
    assert maybe_release_false_hard_stop(settings, moment, Decimal("-2.52"), Decimal("100000")) is None
    assert public_from_store(settings, moment)["closed"] is True


def test_worker_floor_cycle_releases_false_hard_stop_on_live_adjusted_pnl(tmp_path, monkeypatch):
    """The release must happen in the running worker cycle, not only at boot (boot has no account data)."""
    from app.options.day_floor import latch_hard_stop, public_from_store
    from app.workers import loops

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "rel4.db").as_posix(), auth_required=False, trading_mode="PAPER")
    moment = datetime(2026, 10, 30, 14, 35, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)
    assert public_from_store(settings, moment)["closed"] is True

    class Adapter:
        def get_account(self):
            return {"equity": "97984.19", "last_equity": "102136.71"}

        def get_activities(self, *a, **k):
            return []

    monkeypatch.setattr(loops, "_load_tracked_day", lambda s, a, m, p=None: ({"equity": "97984.19"}, Decimal("-2.52"), True))
    view = loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert view["closed"] is False
    assert public_from_store(settings, moment)["closed"] is False


def test_worker_floor_cycle_keeps_latch_on_real_loss(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop, public_from_store
    from app.workers import loops

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "rel5.db").as_posix(), auth_required=False, trading_mode="PAPER")
    moment = datetime(2026, 10, 30, 14, 35, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)

    class Adapter:
        def get_account(self):
            return {}

        def get_activities(self, *a, **k):
            return []

    monkeypatch.setattr(loops, "_load_tracked_day", lambda s, a, m, p=None: ({"equity": "96000"}, Decimal("-3600"), True))
    loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert public_from_store(settings, moment)["closed"] is True
