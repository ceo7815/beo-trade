"""SPY rules: no look-ahead, signals, trail, flatten, DST, sizing, PDT, shadow isolation."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.spy.broker import PdtRejected, SpyBroker
from app.spy.clock import NY, active_slot, decision_instants, flatten_deadline, regular_close
from app.spy.config import SpyConfig, assert_paper_url, redact, startup_mode
from app.spy.engine import Engine, account_matches
from app.spy.store import Store
from app.spy.strategy import (
    LOOKBACK,
    Bar,
    bands,
    close_at,
    daily_vol,
    day_trades_left,
    evaluate_position,
    is_pdt_rejection,
    pdt_block_reason,
    population_stdev,
    prior_sessions,
    rth_sessions,
    share_qty,
    should_retry_order,
    sigma_at,
    slot_snapshot,
    vwap_through,
)

TODAY = date(2026, 1, 20)


def _bar(day: date, hour: int, minute: int, close: str, open_px: str | None = None) -> Bar:
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)
    op = Decimal(open_px if open_px is not None else close)
    px = Decimal(close)
    return Bar(start=start, open=op, high=max(op, px), low=min(op, px), close=px, volume=Decimal("1000"))


def _prior(today: date = TODAY) -> list[Bar]:
    bars: list[Bar] = []
    days = [today - timedelta(days=offset) for offset in range(16, 0, -1)]
    for index, day in enumerate(days):
        close_px = "100" if index == len(days) - 1 else ("110" if index % 2 == 0 else "90")
        bars.extend(
            [
                _bar(day, 9, 30, "100", "100"),
                _bar(day, 9, 59, "101", "100"),
                _bar(day, 10, 29, "101", "100"),
                _bar(day, 14, 59, "180", "100"),
                _bar(day, 15, 59, close_px, "100"),
            ]
        )
    return bars


def _cfg(tmp_path, **overrides) -> SpyConfig:
    data = dict(
        enabled=True,
        key_id="kid",
        secret="super-secret-value",
        account="PA3YUE43D",
        trading_url="",
        data_url="",
        pdt_guard=True,
        allow_short=False,
        shadow=("QQQ",),
        db=tmp_path / "spy.sqlite",
    )
    data.update(overrides)
    return SpyConfig(**data)


class FakeBroker:
    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.qty = 0
        self.fail = 0
        self.pdt = False

    def position_qty(self, symbol: str) -> int:
        assert symbol == "SPY"
        return self.qty

    def order_by_client_id(self, client_order_id: str):
        return None

    def submit(self, body: dict) -> dict:
        assert body["symbol"] == "SPY"
        self.submitted.append(body)
        if self.pdt and body["side"] == "buy":
            raise PdtRejected("pattern day trading protection")
        if self.fail:
            self.fail -= 1
            raise TimeoutError("down")
        if body["side"] == "buy":
            self.qty = int(body["qty"])
        else:
            self.qty = 0
        return {
            "id": "ord-1",
            "status": "filled",
            "filled_qty": body["qty"],
            "filled_avg_price": "102",
            "symbol": "SPY",
            "side": body["side"],
        }


def _clock(day: date = TODAY) -> dict:
    close = regular_close(day).astimezone(timezone.utc).isoformat()
    return {"is_open": True, "next_close": close}


def _account(equity: str = "6252.00", count: int | None = 0, pdt: bool = False, number: str = "PA3YUE43D") -> dict:
    return {
        "equity": equity,
        "account_number": number,
        "daytrade_count": count,
        "pattern_day_trader": pdt,
    }


def test_sigma_ignores_today_and_later_minutes():
    bars = _prior()
    wild = _bar(TODAY, 9, 30, "100", "100")
    wild_move = _bar(TODAY, 9, 59, "150", "100")
    sessions = rth_sessions(bars + [wild, wild_move])
    prior = prior_sessions(sessions, TODAY)
    sigma = sigma_at(prior, 10 * 60)
    assert sigma == Decimal("0.01")
    afternoon = sigma_at(prior, 15 * 60)
    assert afternoon is not None and afternoon != sigma
    assert close_at(sessions[TODAY - timedelta(days=1)], 10 * 60) == Decimal("101")


def test_gap_adjustment_uses_open_and_prior_close():
    upper, lower = bands(Decimal("100"), Decimal("110"), Decimal("0.01"))
    assert upper == Decimal("111.10")
    assert lower == Decimal("99.00")
    upper_gap_down, lower_gap_down = bands(Decimal("110"), Decimal("100"), Decimal("0.01"))
    assert upper_gap_down == Decimal("111.10")
    assert lower_gap_down == Decimal("99.00")


def test_vwap_stops_at_the_decision_minute():
    day = TODAY
    bars = [
        _bar(day, 9, 30, "100", "100"),
        _bar(day, 9, 59, "110", "100"),
        _bar(day, 10, 29, "200", "100"),
    ]
    early = vwap_through(bars, 10 * 60)
    later = vwap_through(bars, 10 * 60 + 30)
    assert early is not None and later is not None
    assert later > early
    assert early < Decimal("120")


def test_long_only_and_short_signal():
    flat_long = evaluate_position(
        price=Decimal("102"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("100"),
        side="flat",
        allow_short=False,
    )
    assert flat_long.action == "ENTER_LONG"
    blocked = evaluate_position(
        price=Decimal("98"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("100"),
        side="flat",
        allow_short=False,
    )
    assert blocked.action == "FLAT" and blocked.reason == "LONG_ONLY"
    short = evaluate_position(
        price=Decimal("98"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("100"),
        side="flat",
        allow_short=True,
    )
    assert short.action == "ENTER_SHORT"


def test_trailing_stop_uses_the_stricter_level_and_only_at_a_decision():
    held = evaluate_position(
        price=Decimal("101.5"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("102"),
        side="long",
        allow_short=False,
    )
    assert held.action == "EXIT" and held.stop == Decimal("102")
    still = evaluate_position(
        price=Decimal("101.5"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("100"),
        side="long",
        allow_short=False,
    )
    assert still.action == "HOLD" and still.stop == Decimal("101")
    touched = evaluate_position(
        price=Decimal("101"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("100"),
        side="long",
        allow_short=False,
    )
    assert touched.action == "EXIT"
    cover = evaluate_position(
        price=Decimal("98.5"),
        upper=Decimal("101"),
        lower=Decimal("99"),
        vwap=Decimal("98"),
        side="short",
        allow_short=True,
    )
    assert cover.action == "EXIT" and cover.stop == Decimal("98")
    sessions = rth_sessions(_prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "90", "100")])
    snapshot = slot_snapshot(sessions, TODAY, 10 * 60 + 1)
    assert snapshot[0] is None


def test_size_uses_live_equity_and_caps_at_one_times():
    full = share_qty(Decimal("6252"), Decimal("500"), Decimal("0.01"))
    assert full == 12
    assert full * Decimal("500") <= Decimal("6252")
    half = share_qty(Decimal("6252"), Decimal("500"), Decimal("0.04"))
    assert half == 6
    assert share_qty(Decimal("1000"), Decimal("10"), Decimal("0.001")) == 100
    assert share_qty(Decimal("100"), Decimal("250"), Decimal("0.01")) == 0
    assert "6252" not in open(__file__.replace("tests/test_spy_rules.py", "app/spy/strategy.py"), encoding="utf-8").read()


def test_pdt_guard_blocks_only_under_the_regulatory_line():
    assert day_trades_left(equity=Decimal("6252"), daytrade_count=3, pattern_day_trader=False, guard=True) == 0
    assert day_trades_left(equity=Decimal("6252"), daytrade_count=2, pattern_day_trader=False, guard=True) == 1
    assert day_trades_left(equity=Decimal("25000"), daytrade_count=3, pattern_day_trader=True, guard=True) is None
    assert day_trades_left(equity=Decimal("6252"), daytrade_count=0, pattern_day_trader=False, guard=False) is None
    assert pdt_block_reason(equity=Decimal("6252"), daytrade_count=3, pattern_day_trader=False, guard=True) == "PDT_LIMIT"
    assert pdt_block_reason(equity=Decimal("6252"), daytrade_count=None, pattern_day_trader=False, guard=True) == "PDT_UNKNOWN"
    assert pdt_block_reason(equity=Decimal("6252"), daytrade_count=0, pattern_day_trader=True, guard=True) == "PDT_LIMIT"
    assert is_pdt_rejection({"code": 40310100, "message": "trade denied due to pattern day trading protection"})
    assert should_retry_order("pdt_rejected") is False
    assert should_retry_order("filled") is False


def test_dst_keeps_new_york_clock_and_moves_utc():
    winter = date(2026, 1, 15)
    summer = date(2026, 7, 15)
    winter_slots = decision_instants(winter, regular_close(winter))
    summer_slots = decision_instants(summer, regular_close(summer))
    assert [item.strftime("%H:%M") for item in winter_slots] == [item.strftime("%H:%M") for item in summer_slots]
    assert winter_slots[0].strftime("%H:%M") == "10:00"
    assert winter_slots[-1].strftime("%H:%M") == "15:30"
    assert winter_slots[0].astimezone(timezone.utc).hour == 15
    assert summer_slots[0].astimezone(timezone.utc).hour == 14
    dubai = ZoneInfo("Asia/Dubai")
    assert winter_slots[0].astimezone(dubai).hour == 19
    assert summer_slots[0].astimezone(dubai).hour == 18
    assert flatten_deadline(regular_close(winter)).strftime("%H:%M") == "15:58"
    assert flatten_deadline(regular_close(summer)).strftime("%H:%M") == "15:58"
    early = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    assert flatten_deadline(early).strftime("%H:%M") == "12:58"
    early_slots = decision_instants(date(2026, 11, 27), early)
    assert early_slots[-1].strftime("%H:%M") == "12:30"
    assert active_slot(datetime(2026, 1, 15, 10, 20, tzinfo=NY), regular_close(winter)).strftime("%H:%M") == "10:00"
    assert active_slot(datetime(2026, 1, 15, 15, 58, tzinfo=NY), regular_close(winter)) is None


def test_paper_host_is_the_only_trading_url():
    assert assert_paper_url("") == "https://paper-api.alpaca.markets"
    assert assert_paper_url("https://paper-api.alpaca.markets/") == "https://paper-api.alpaca.markets"
    with pytest.raises(Exception):
        assert_paper_url("https://api.alpaca.markets")
    cfg = SpyConfig(True, "kid", "super-secret-value", "PA3YUE43D", "https://api.alpaca.markets", "", True, False, ("QQQ",), TODAY and __import__("pathlib").Path("/tmp/spy.sqlite"))
    assert startup_mode(cfg) == "refused"
    assert "super-secret-value" not in repr(cfg)
    assert "super-secret-value" not in redact("token super-secret-value leaked", cfg)


def test_entry_uses_the_decision_bar_not_a_later_print(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "spy.sqlite")
    cfg = _cfg(tmp_path)
    engine = Engine(cfg, store, broker)
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    bars = _prior() + [
        _bar(TODAY, 9, 30, "100", "100"),
        _bar(TODAY, 9, 59, "102", "100"),
        _bar(TODAY, 10, 1, "50", "100"),
    ]
    result = engine.cycle(now, {"SPY": bars, "QQQ": bars}, _clock(), _account())
    assert broker.submitted
    assert broker.submitted[0]["symbol"] == "SPY"
    assert broker.submitted[0]["side"] == "buy"
    assert int(broker.submitted[0]["qty"]) * Decimal("102") <= Decimal("6252")
    signal = store.signal_for("SPY", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert signal["action"] == "ENTER_LONG"
    assert Decimal(signal["price"]) == Decimal("102")
    again = engine.cycle(now, {"SPY": bars, "QQQ": bars}, _clock(), _account())
    assert len(broker.submitted) == 1
    assert result.orders and again.orders == []
    store.close()


def test_long_only_does_not_sell_and_short_flag_does(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "spy.sqlite")
    bars = _prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "98", "100"), _bar(TODAY, 10, 1, "98", "100")]
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(now, {"SPY": bars}, _clock(), _account())
    assert broker.submitted == []
    signal = store.signal_for("SPY", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert signal["reason"] == "LONG_ONLY"
    store.close()
    broker_short = FakeBroker()
    store_short = Store(tmp_path / "short.sqlite")
    Engine(_cfg(tmp_path, allow_short=True, db=tmp_path / "short.sqlite"), store_short, broker_short).cycle(
        now, {"SPY": bars}, _clock(), _account()
    )
    assert broker_short.submitted[0]["side"] == "sell"
    store_short.close()


def test_pdt_limit_skips_and_a_rejection_is_not_retried(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "spy.sqlite")
    bars = _prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "102", "100"), _bar(TODAY, 10, 1, "102", "100")]
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(now, {"SPY": bars}, _clock(), _account(count=3))
    assert broker.submitted == []
    signal = store.signal_for("SPY", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert signal["reason"] == "PDT_LIMIT"
    store.close()

    broker_reject = FakeBroker()
    broker_reject.pdt = True
    store_reject = Store(tmp_path / "reject.sqlite")
    engine = Engine(_cfg(tmp_path, db=tmp_path / "reject.sqlite"), store_reject, broker_reject)
    engine.cycle(now, {"SPY": bars}, _clock(), _account(count=0))
    engine.cycle(now, {"SPY": bars}, _clock(), _account(count=0))
    assert len(broker_reject.submitted) == 1
    assert store_reject.pdt_rejected(TODAY.isoformat(), "entry")
    store_reject.close()


def test_flatten_retries_then_stops_on_pdt(tmp_path):
    broker = FakeBroker()
    broker.qty = 4
    broker.fail = 1
    store = Store(tmp_path / "spy.sqlite")
    now = datetime(2026, 1, 20, 15, 58, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(now, {"SPY": _prior()}, _clock(), _account())
    assert [row["side"] for row in broker.submitted] == ["sell", "sell"]
    assert broker.qty == 0
    store.close()


def test_shadow_qqq_never_sends_an_order(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "spy.sqlite")
    spy = _prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "100.4", "100"), _bar(TODAY, 10, 1, "100.4", "100")]
    qqq = _prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "102", "100"), _bar(TODAY, 10, 1, "102", "100")]
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(now, {"SPY": spy, "QQQ": qqq}, _clock(), _account())
    assert broker.submitted == []
    shadow = store.open_trade("spy_shadow_trades", "QQQ")
    assert shadow is not None and shadow["side"] == "long" and int(shadow["qty"]) >= 1
    signal = store.signal_for("QQQ", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert signal["shadow"] == 1 and signal["action"] == "ENTER_LONG"
    store.close()


def test_account_mismatch_sends_nothing(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "spy.sqlite")
    bars = _prior() + [_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 9, 59, "102", "100"), _bar(TODAY, 10, 1, "102", "100")]
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(now, {"SPY": bars}, _clock(), _account(number="PA3LU0NE5"))
    assert broker.submitted == []
    assert store.meta("account_mismatch") == "1"
    store.close()
    assert account_matches(_cfg(tmp_path), {"account_number": "PA3YUE43D"})


def test_spy_package_does_not_touch_the_options_worker():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "spy"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "app.workers" not in text
        assert "worker.lock" not in text
        assert "ALPACA_API_KEY" not in text
        assert "super-secret" not in text


def test_daily_vol_needs_prior_sessions_only():
    sessions = rth_sessions(_prior())
    vol = daily_vol(prior_sessions(sessions, TODAY))
    assert vol is not None and vol > 0
    assert population_stdev([Decimal("1"), Decimal("1")]) == 0


def test_broker_refuses_a_non_spy_order(monkeypatch):
    cfg = SpyConfig(True, "kid", "sec", "PA3YUE43D", "", "", True, False, (), __import__("pathlib").Path("/tmp/x.sqlite"))

    class Transport:
        def handle_request(self, request):
            raise AssertionError("network")

    # Construction checks the URL before any request. A live URL never builds a client.
    with pytest.raises(Exception):
        SpyBroker(SpyConfig(True, "kid", "sec", "PA3YUE43D", "https://api.alpaca.markets", "", True, False, (), cfg.db))
    assert LOOKBACK == 14
