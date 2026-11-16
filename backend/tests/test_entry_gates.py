"""LOSS_STREAK and CHASE_ATR. New buys only. The kill, the floor, and exits stay put."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import structlog
from fastapi.testclient import TestClient

from app.config.settings import Settings, load_trading_config
from app.positions.entry_gates import (
    CHASE_ATR,
    CHASE_ATR_LIMIT,
    LOSS_STREAK,
    LOSS_STREAK_LENGTH,
    atr14,
    chase_reading,
    directional_move,
    loss_streak_reason,
    loss_streak_state,
    public_chase,
    public_loss_streak,
    session_closes_from_adapter,
    session_closes_from_fills,
)
from app.quant.bars import atr
from app.schemas.domain import Bar
from app.workers.loops import _submit_autonomous_buys, close_all_positions, monitor_once

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NY_OPEN = datetime(2026, 10, 1, 13, 30, tzinfo=UTC)
SESSION = NY_OPEN.date()


def _bars(count: int, *, high: str = "101", low: str = "99", close: str = "100", start: datetime | None = None) -> list[Bar]:
    stamp = start or datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    rows = []
    for index in range(count):
        moment = stamp + timedelta(days=index)
        rows.append(Bar(moment, Decimal(close), Decimal(high), Decimal(low), Decimal(close), 1_000))
    return rows


def _close(minute: int, pnl: str, *, day: datetime = NY_OPEN) -> dict:
    stamp = day + timedelta(minutes=minute)
    return {"symbol": f"QQQ{minute:04d}", "pnl": pnl, "closed_at": stamp.isoformat()}


NOW = NY_OPEN + timedelta(hours=3)


def _blocked(*pnls: str) -> bool:
    closes = [_close(index, pnl) for index, pnl in enumerate(pnls)]
    return loss_streak_reason(closes, NOW) is not None


def test_two_losing_closes_in_a_row_block_and_the_text_is_clear():
    assert LOSS_STREAK_LENGTH == 2
    assert _blocked("-10", "-5") is True
    reason = loss_streak_reason([_close(1, "-10"), _close(2, "-5.50")], NOW)
    assert reason is not None and reason.startswith(LOSS_STREAK)
    assert "consecutive_losses=2" in reason and "threshold=2" in reason and "last_closes=-10,-5.5" in reason
    assert _blocked("-10") is False
    assert _blocked() is False
    assert _blocked("-1", "-1", "-1") is True


def test_loss_win_loss_does_not_block_and_a_win_releases_and_resets():
    assert _blocked("-10", "20", "-5") is False
    assert _blocked("-10", "-5", "20") is False
    # After the release two NEW losses are needed.
    assert _blocked("-10", "-5", "20", "-3") is False
    assert _blocked("-10", "-5", "20", "-3", "-4") is True
    state = loss_streak_state([_close(1, "-10"), _close(2, "-5"), _close(3, "20")], NOW)
    assert state["streak"] == 0 and state["blocking"] is False


def test_breakeven_close_is_neutral_it_is_not_a_loss_and_does_not_reset():
    assert _blocked("-10", "0", "-5") is True
    assert _blocked("-10", "0") is False
    assert _blocked("0", "0", "0") is False
    # A blocked streak stays blocked through a flat close; only a win releases it.
    assert _blocked("-10", "-5", "0") is True
    assert _blocked("-10", "-5", "0", "7") is False


def test_closes_are_ordered_by_close_time_not_by_list_order():
    closes = [_close(3, "20"), _close(1, "-10"), _close(2, "-5")]
    assert loss_streak_reason(closes, NOW) is None
    closes = [_close(3, "-5"), _close(1, "20"), _close(2, "-10")]
    assert loss_streak_reason(closes, NOW) is not None


def test_a_new_session_day_starts_at_zero():
    yesterday = NY_OPEN - timedelta(days=1)
    old = [_close(1, "-10", day=yesterday), _close(2, "-5", day=yesterday)]
    assert loss_streak_reason(old, NOW) is None
    assert loss_streak_reason(old, yesterday + timedelta(hours=3)) is not None
    mixed = old + [_close(5, "-1")]
    state = loss_streak_state(mixed, NOW)
    assert state["streak"] == 1 and state["closes_today"] == 1 and state["blocking"] is False
    # The New York day is the boundary, not UTC midnight: 23:30 New York is already the next UTC date.
    late = datetime(2026, 10, 1, 23, 30, tzinfo=UTC)
    late_closes = [{"symbol": "A", "pnl": "-1", "closed_at": late.isoformat()}, {"symbol": "B", "pnl": "-1", "closed_at": (late + timedelta(minutes=5)).isoformat()}]
    assert loss_streak_reason(late_closes, late + timedelta(minutes=10)) is not None


def test_unread_or_broken_closes_do_not_block():
    assert loss_streak_reason(None, NOW) is None
    view = public_loss_streak(None, NOW)
    assert view["data_missing"] is True and view["blocking"] is False and view["enabled"] is True
    assert loss_streak_reason([{"pnl": "-1"}, {"closed_at": NY_OPEN.isoformat(), "pnl": "x"}, "junk"], NOW) is None


def test_public_view_has_the_risk_shape():
    closes = [_close(1, "10"), _close(2, "-4"), _close(3, "-6")]
    view = public_loss_streak(closes, NOW)
    assert view["rule"] == LOSS_STREAK and view["enabled"] is True and view["threshold"] == 2
    assert view["current_streak"] == 2 and view["blocking"] is True
    assert view["closes_today"] == 3 and view["data_missing"] is False
    assert [item["result"] for item in view["recent_closes"]] == ["win", "loss", "loss"]
    assert view["reason"].startswith(LOSS_STREAK)
    assert public_loss_streak([_close(1, "10")], NOW)["blocking"] is False
    many = [_close(index, "1") for index in range(12)]
    assert len(public_loss_streak(many, NOW)["recent_closes"]) == 5


def _fill(execution: str, symbol: str, side: str, price: str, at: datetime, qty: str = "1") -> dict:
    return {"execution_id": execution, "symbol": symbol, "side": side, "qty": qty, "price": price, "timestamp": at.isoformat(), "broker_order_id": execution}


def test_closed_trades_come_from_fills_open_losers_are_ignored_and_old_days_dropped():
    call_a = "QQQ261001C00500000"
    call_b = "QQQ261001C00501000"
    call_c = "QQQ261001P00490000"
    fills = [
        _fill("1", call_a, "buy", "2.00", NY_OPEN),
        _fill("2", call_a, "sell", "1.00", NY_OPEN + timedelta(minutes=5)),  # closed loser -100
        _fill("3", call_b, "buy", "2.00", NY_OPEN + timedelta(minutes=6)),
        _fill("4", call_b, "sell", "1.50", NY_OPEN + timedelta(minutes=9)),  # closed loser -50
        # Open position deep in the red: no sell, never a close.
        _fill("5", call_c, "buy", "3.00", NY_OPEN + timedelta(minutes=10)),
        # Yesterday's closed winner is not today's.
        _fill("6", "SPY261001C00600000", "buy", "1.00", NY_OPEN - timedelta(days=1)),
        _fill("7", "SPY261001C00600000", "sell", "2.00", NY_OPEN - timedelta(days=1) + timedelta(minutes=3)),
    ]
    closes = session_closes_from_fills(fills, NOW)
    assert [row["pnl"] for row in closes] == ["-100.00", "-50.00"]
    assert loss_streak_reason(closes, NOW) is not None
    # A winning close at the end releases. The open loser stays out of it.
    fills += [_fill("8", call_c, "sell", "3.50", NY_OPEN + timedelta(minutes=30))]
    assert loss_streak_reason(session_closes_from_fills(fills, NOW), NOW) is None
    only_open = [fills[0], fills[2], fills[4]]
    assert session_closes_from_fills(only_open, NOW) == []
    assert loss_streak_reason(session_closes_from_fills(only_open, NOW), NOW) is None


def test_an_excluded_bug_lot_is_not_a_close_and_cannot_start_a_streak():
    bug = "QQQ261001C00500000"
    other = "QQQ261001C00501000"
    fills = [
        _fill("1", bug, "buy", "2.00", NY_OPEN),
        _fill("2", bug, "sell", "1.00", NY_OPEN + timedelta(minutes=5)),
        _fill("3", other, "buy", "2.00", NY_OPEN + timedelta(minutes=6)),
        _fill("4", other, "sell", "1.00", NY_OPEN + timedelta(minutes=9)),
    ]
    assert loss_streak_reason(session_closes_from_fills(fills, NOW, exclusions=[]), NOW) is not None
    rule = {"symbol": bug, "active": True, "quantity": 1, "entry_price": "2.00", "session_day": "2026-10-01"}
    closes = session_closes_from_fills(fills, NOW, exclusions=[rule])
    assert [row["symbol"] for row in closes] == [other]
    assert loss_streak_reason(closes, NOW) is None


def test_stored_and_alpaca_fills_are_one_book_so_a_loss_is_not_counted_twice(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerFillRecord
    from tests.test_entry_pace import _database

    _database(tmp_path)
    settings = Settings(app_env="local", trading_mode="PAPER", auth_required=False)
    symbol = "QQQ261001C00500000"
    buy = _fill("e-1", symbol, "buy", "2.00", NY_OPEN)
    sell = _fill("e-2", symbol, "sell", "1.00", NY_OPEN + timedelta(minutes=5))
    with session_scope() as session:
        for row in (buy, sell):
            session.add(BrokerFillRecord(execution_id=row["execution_id"], symbol=row["symbol"], payload=row, observed_at=datetime.fromisoformat(row["timestamp"])))
        session.commit()

    class Adapter:
        def __init__(self, fills):
            self.fills = fills
            self.asked = {}

        def get_all_fills(self, max_pages=20, after=""):
            self.asked = {"max_pages": max_pages, "after": after}
            return list(self.fills)

    both = Adapter([buy, sell])
    closes = session_closes_from_adapter(settings, both, NOW)
    assert [row["pnl"] for row in closes] == ["-100.00"]
    assert both.asked["after"] == "2026-09-30"
    # Alpaca alone, stored alone, and an adapter with no fill reader all give the same close.
    assert [row["pnl"] for row in session_closes_from_adapter(settings, Adapter([]), NOW)] == ["-100.00"]
    assert [row["pnl"] for row in session_closes_from_adapter(settings, object(), NOW)] == ["-100.00"]

    class Broken:
        def get_all_fills(self, **_kw):
            raise RuntimeError("alpaca down")

    assert [row["pnl"] for row in session_closes_from_adapter(settings, Broken(), NOW)] == ["-100.00"]


def test_chase_is_symmetric_and_the_limit_is_exclusive():
    width = Decimal("10")
    opened = Decimal("100")
    # Exactly 0.65 stays eligible. One cent of price past it does not.
    assert directional_move("CALL", Decimal("106.5"), opened, width) == Decimal("0.65")
    assert directional_move("PUT", Decimal("93.5"), opened, width) == Decimal("0.65")
    assert chase_reading("CALL", Decimal("106.5"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("PUT", Decimal("93.5"), opened, width, symbol="NVDA")["reason"] is None
    call = chase_reading("CALL", Decimal("106.51"), opened, width, symbol="NVDA")
    put = chase_reading("PUT", Decimal("93.49"), opened, width, symbol="MSTR")
    assert call["reason"].startswith(CHASE_ATR)
    assert put["reason"].startswith(CHASE_ATR)
    assert call["move_atr"] == put["move_atr"]
    assert "move_atr=" in call["reason"]
    # The other direction is not a chase.
    assert chase_reading("CALL", Decimal("90"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("PUT", Decimal("110"), opened, width, symbol="NVDA")["chase_blocked"] is False
    assert chase_reading("call", Decimal("110"), opened, width, symbol="nvda")["chase_blocked"] is True
    assert CHASE_ATR_LIMIT == Decimal("0.65")


def test_missing_atr_open_or_price_does_not_block_and_is_logged():
    bars = _bars(10)
    assert atr14(bars, SESSION) is None
    with structlog.testing.capture_logs() as logs:
        no_atr = chase_reading("CALL", "110", "100", None, symbol="AMD")
        no_open = chase_reading("PUT", "90", None, "2", symbol="AMD")
        no_price = chase_reading("CALL", 0, "100", "2", symbol="AMD")
        short = chase_reading("PUT", "90", "100", atr14(bars, SESSION), symbol="AMD")
    assert no_atr["reason"] is None and no_atr["missing"] is True
    assert no_open["reason"] is None and no_price["reason"] is None and short["reason"] is None
    assert [row.get("event") for row in logs].count("entry.chase_data_missing") == 4
    assert all(row.get("symbol") == "AMD" for row in logs)


def test_atr14_uses_completed_days_and_ignores_todays_bar():
    prior = _bars(15)
    today = Bar(datetime(2026, 10, 1, 15, 0, tzinfo=UTC), Decimal("100"), Decimal("130"), Decimal("70"), Decimal("120"), 1)
    assert atr14(prior, SESSION) == Decimal("2")
    assert atr14([*prior, today], SESSION) == Decimal("2")
    assert atr14(prior, None) == Decimal("2")
    highs = [101.0] * 15
    lows = [99.0] * 15
    closes = [100.0] * 15
    assert atr(highs, lows, closes) == 2
    huge_today = _bars(14) + [Bar(datetime(2026, 10, 1, 18, 0, tzinfo=UTC), Decimal("100"), Decimal("180"), Decimal("20"), Decimal("100"), 1)]
    assert atr14(huge_today, SESSION) is None


def test_both_profiles_feed_the_same_rule_and_eli_and_avi_do_not():
    main = load_trading_config()
    options2 = load_trading_config(ROOT / "app" / "config" / "trading_v2610_40k.toml")
    closes = [_close(1, "-3"), _close(2, "-4")]
    # The rule has no per-profile number: both desks get the same streak of 2, whatever their kill.
    assert public_loss_streak(closes, NOW)["blocking"] is True
    assert public_loss_streak(closes[:1], NOW)["blocking"] is False
    assert not hasattr(main, "loss_streak") and not hasattr(options2, "loss_streak")
    assert public_chase()["limit"] == "0.65"
    assert public_chase()["blocks_on_missing_data"] is False
    for name in ("spy/engine.py", "spy/strategy.py", "avi/engine.py", "avi/strategy.py"):
        text = (ROOT / "app" / name).read_text(encoding="utf-8")
        assert "entry_gates" not in text
        assert "LOSS_STREAK" not in text
        assert "ADD_BRAKE" not in text
        assert "CHASE_ATR" not in text


def test_the_old_day_pnl_brake_is_gone():
    import app.positions.entry_gates as gates

    for name in ("ADD_BRAKE", "ADD_BRAKE_FRACTION", "add_brake_reason", "brake_threshold", "public_add_brake", "live_brake_inputs"):
        assert not hasattr(gates, name)
    for path in ("app/main.py", "app/workers/loops.py", "app/broker/execution.py", "app/recommendations/pipeline.py", "app/analytics/routes.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "add_brake" not in text and "ADD_BRAKE" not in text


def test_exits_do_not_read_the_new_gates():
    exits = inspect.getsource(monitor_once) + inspect.getsource(close_all_positions)
    assert "loss_streak_reason" not in exits
    assert "chase_reading" not in exits
    assert "LOSS_STREAK" not in exits
    assert "CHASE_ATR" not in exits
    assert "loss_streak_reason" in inspect.getsource(_submit_autonomous_buys)
    assert "chase_reading" in inspect.getsource(_submit_autonomous_buys)


def test_scan_records_move_atr_and_both_gates_can_suppress_a_buy():
    from app.recommendations.pipeline import run_scan
    from app.schemas.domain import DecisionKind
    from tests.test_engine import AS_OF, StubAI, ledger, loose_config, sample_market

    underlying, option, news = sample_market()
    config = loose_config()
    moment = AS_OF
    quiet = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts={},
    )
    assert quiet[0].decision is DecisionKind.BUY
    assert quiet[0].policy_trace["move_atr"] is None
    counted = {}
    quiet = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts=counted,
    )
    assert counted["move_atr"][0]["missing"] is True
    assert counted["move_atr"][0]["chase_blocked"] is False

    chased = replace(underlying, bars=tuple(_bars(15)))
    counts_chase: dict = {}
    blocked = run_scan(
        [chased],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        counts=counts_chase,
    )
    assert blocked[0].decision is DecisionKind.SUPPRESS
    assert blocked[0].suppress_reason.startswith(CHASE_ATR)
    assert blocked[0].policy_trace["move_atr"] == "1"
    assert counts_chase["move_atr"][0]["chase_blocked"] is True
    assert counts_chase["move_atr"][0]["symbol"] == "TEST"

    wide = _bars(15, high="110", low="90", close="100")
    allowed = run_scan(
        [replace(underlying, bars=tuple(wide))],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
    )
    assert allowed[0].decision is DecisionKind.BUY
    assert allowed[0].policy_trace["atr14"] == "20"

    stopped = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        session_closes=[_close(1, "-10", day=moment - timedelta(hours=1)), _close(2, "-5", day=moment - timedelta(hours=1))],
    )
    assert stopped[0].decision is DecisionKind.SUPPRESS
    assert stopped[0].suppress_reason.startswith(LOSS_STREAK)
    assert stopped[0].policy_trace["loss_streak"] == stopped[0].suppress_reason
    resumed = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        session_closes=[_close(1, "-10", day=moment - timedelta(hours=1)), _close(2, "5", day=moment - timedelta(hours=1)), _close(3, "-5", day=moment - timedelta(hours=1))],
    )
    assert resumed[0].decision is DecisionKind.BUY
    unread = run_scan(
        [underlying],
        [option],
        news,
        config,
        StubAI(),
        ledger(),
        moment,
        moment.date(),
        Decimal("100000"),
        set(),
        session_closes=[],
    )
    assert unread[0].decision is DecisionKind.BUY


def test_rules_book_and_risk_and_system_show_both_gates(tmp_path):
    from app.analytics.trading_rules import build_trading_rules
    from app.main import create_app

    entry = build_trading_rules(Settings(app_env="local", trading_mode="PAPER", auth_required=False))["active"]["sections"]["entry"]
    assert not any(item["id"] == "add_brake" for item in entry)
    brake = next(item for item in entry if item["id"] == "loss_streak")
    chase = next(item for item in entry if item["id"] == "chase_atr")
    assert LOSS_STREAK in brake["explain"] and "2" in brake["value"]
    assert CHASE_ATR in chase["explain"] and "0.65" in chase["value"]
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    with TestClient(create_app(settings, allow_open_api=True)) as client:
        risk = client.get("/api/v1/risk")
        assert risk.status_code == 200
        body = risk.json()
        assert "add_brake" not in body
        assert body["loss_streak"]["enabled"] is True
        assert body["loss_streak"]["threshold"] == 2
        assert body["loss_streak"]["current_streak"] == 0
        assert body["loss_streak"]["blocking"] is False
        assert body["loss_streak"]["recent_closes"] == []
        assert body["chase_atr"]["limit"] == "0.65"
        assert body["chase_atr"]["period"] == 14
        assert body["chase_atr"]["blocks_on_missing_data"] is False
        system = client.get("/api/v1/system")
        assert system.status_code == 200
        assert "add_brake" not in system.json()
        assert system.json()["loss_streak"]["threshold"] == 2
        assert system.json()["chase_atr"]["limit"] == "0.65"


def test_submit_blocks_on_two_losses_and_resumes_when_a_winner_closes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.schemas.domain import DecisionKind, OptionRight, Recommendation
    from app.workers import loops
    from tests.test_entry_pace import _database

    settings = _database(tmp_path)
    placed: list[str] = []
    monkeypatch.setattr(loops, "_cash", lambda _settings: Decimal("100000"))
    monkeypatch.setattr(loops, "_entry_pnl", lambda _settings: Decimal("0"))
    monkeypatch.setattr(loops, "_measured_sectors", lambda _settings: {})
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: {"closed": False, "reason": ""})
    monkeypatch.setattr("app.capital.session_lock.open_equity_from_account", lambda *args, **kwargs: Decimal("30000"))

    class Adapter:
        def __init__(self, equity: str):
            self.equity = equity

        def get_market_clock(self):
            return {"is_open": True, "next_close": "2026-11-13T21:00:00+00:00"}

        def get_positions(self):
            return []

        def get_orders(self, _status=None):
            return []

        def get_account(self):
            return {
                "equity": self.equity,
                "last_equity": "30000",
                "cash": self.equity,
                "buying_power": self.equity,
                "options_buying_power": self.equity,
            }

        def get_activities(self):
            return []

        def get_all_fills(self, max_pages=20, after=""):
            return list(state["fills"])

        def close(self):
            return None

    state = {"equity": "30000.00", "fills": []}
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: Adapter(state["equity"]))

    def quotes(_settings, _live, _buys):
        found = loops._SpotQuotes()
        found.spots["NVDA"] = SimpleNamespace(open=Decimal("100"), high=Decimal("100.2"), price=Decimal("100"))
        found.append(SimpleNamespace())
        return found

    monkeypatch.setattr(loops, "_revalidation_quotes", quotes)

    def place_order(*args, **kwargs):
        placed.append("placed")
        return {"duplicate": False, "order": {}}

    monkeypatch.setattr("app.broker.execution.place_order", place_order)
    moment = datetime(2026, 11, 13, 15, 30, tzinfo=UTC)
    buy = Recommendation(
        recommendation_id="rec-brake-1",
        decision=DecisionKind.BUY,
        timestamp=moment,
        underlying="NVDA",
        underlying_price=Decimal("100"),
        option_symbol="NVDA261113C00100000",
        call_put=OptionRight.CALL,
        strike=Decimal("100"),
        expiration=moment.date() + timedelta(days=2),
        option_price=Decimal("1.02"),
        bid=Decimal("1.00"),
        ask=Decimal("1.05"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.02"),
        theta=Decimal("-0.05"),
        vega=Decimal("0.10"),
        iv=Decimal("0.35"),
        volume=500,
        open_interest=500,
        max_entry_price=Decimal("1.05"),
        quantity=1,
        holding_window_min=5,
        holding_window_max=90,
        thesis="",
        catalyst="",
        risk="",
        invalidation="",
        reason_codes=["TEST"],
        gate_results={"DATA_OK": True},
        policy_trace={"atr14": "10", "chase_open": "100", "chase_price": "100"},
    )
    # The day P&L is deeply negative on purpose: only closed trades count now.
    state["equity"] = "28000.00"
    first = "NVDA261113C00099000"
    second = "NVDA261113C00098000"
    third = "AMD261113C00098000"
    day = moment - timedelta(hours=2)
    state["fills"] = [
        _fill("a1", first, "buy", "2.00", day),
        _fill("a2", first, "sell", "1.00", day + timedelta(minutes=5)),
        _fill("b1", second, "buy", "2.00", day + timedelta(minutes=6)),
        _fill("b2", second, "sell", "1.50", day + timedelta(minutes=9)),
        # An open loser is not a close and does not count.
        _fill("c1", third, "buy", "3.00", day + timedelta(minutes=10)),
    ]
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [buy], moment, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(LOSS_STREAK)
    assert "consecutive_losses=2" in outcomes[0]["reason"]
    assert placed == []
    from app.options.day_floor import day_close_reason

    assert day_close_reason(settings, moment) is None

    # A winner closes: the block lifts at once, even with the day still negative.
    state["fills"].append(_fill("c2", third, "sell", "3.50", day + timedelta(minutes=30)))
    outcomes = []
    recovered = replace(buy, recommendation_id="rec-brake-2")
    _submit_autonomous_buys(settings, [recovered], moment, outcomes)
    assert outcomes[0]["result"] == "submitted"
    assert placed == ["placed"]

    # Two fresh losing closes after that win block again.
    state["fills"] += [
        _fill("d1", "TSLA261113C00100000", "buy", "2.00", day + timedelta(minutes=40)),
        _fill("d2", "TSLA261113C00100000", "sell", "1.00", day + timedelta(minutes=45)),
        _fill("e1", "MSFT261113C00100000", "buy", "2.00", day + timedelta(minutes=46)),
        _fill("e2", "MSFT261113C00100000", "sell", "1.00", day + timedelta(minutes=50)),
    ]
    outcomes = []
    _submit_autonomous_buys(settings, [replace(buy, recommendation_id="rec-brake-3")], moment, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(LOSS_STREAK)
    assert placed == ["placed"]
    state["fills"] = []

    state["equity"] = "30000.00"
    chased = replace(
        buy,
        recommendation_id="rec-chase-1",
        policy_trace={"atr14": "10", "chase_open": "100", "chase_price": "100"},
    )
    monkeypatch.setattr(
        loops,
        "_revalidation_quotes",
        lambda _settings, _live, _buys: _spots(Decimal("106.51")),
    )
    outcomes = []
    _submit_autonomous_buys(settings, [chased], moment, outcomes)
    assert outcomes[0]["result"] == "blocked"
    assert outcomes[0]["reason"].startswith(CHASE_ATR)
    assert outcomes[0]["move_atr"] is not None
    assert placed == ["placed"]

    monkeypatch.setattr(
        loops,
        "_revalidation_quotes",
        lambda _settings, _live, _buys: _spots(Decimal("100")),
    )
    missing = replace(buy, recommendation_id="rec-chase-2", policy_trace={})
    outcomes = []
    _submit_autonomous_buys(settings, [missing], moment, outcomes)
    assert outcomes[0]["result"] == "submitted"


def _spots(price: Decimal):
    from types import SimpleNamespace

    from app.workers import loops

    found = loops._SpotQuotes()
    found.spots["NVDA"] = SimpleNamespace(open=Decimal("100"), high=Decimal("200"), price=price)
    found.append(SimpleNamespace())
    return found
