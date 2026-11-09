"""Several symbols share one book: sizing slice, slot order, daily loss, and PDT."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.spy.clock import NY
from app.spy.engine import Engine
from app.spy.store import Store
from app.spy.strategy import fit_order_qty, pdt_block_reason, share_qty
from tests.test_spy_rules import TODAY, _cfg, _clock, _prior

SYMBOLS = ("SPY", "QQQ", "IWM")


def _breakout() -> list:
    from tests.test_spy_rules import _bar

    return _prior() + [
        _bar(TODAY, 9, 30, "100", "100"),
        _bar(TODAY, 9, 59, "102", "100"),
        _bar(TODAY, 10, 1, "102", "100"),
    ]


def _account(count: int = 0, buying: str = "6252") -> dict:
    return {
        "equity": "6252.00",
        "buying_power": buying,
        "account_number": "PA3NEW25000",
        "daytrade_count": count,
        "pattern_day_trader": False,
    }


class BookBroker:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def position_qty(self, symbol: str) -> int:
        return 0

    def order_by_client_id(self, client_order_id: str):
        return None

    def submit(self, body: dict) -> dict:
        self.submitted.append(body)
        return {
            "id": "ord",
            "status": "filled",
            "filled_qty": body["qty"],
            "filled_avg_price": "102",
            "symbol": body["symbol"],
            "side": body["side"],
        }


def _engine(tmp_path, **overrides):
    broker = BookBroker()
    fields = dict(
        shadow=(),
        symbols=SYMBOLS,
        max_positions=3,
        max_entries_day=6,
        per_symbol_entries=2,
        db=tmp_path / "multi.sqlite",
    )
    fields.update(overrides)
    cfg = _cfg(tmp_path, **fields)
    return Engine(cfg, Store(tmp_path / "multi.sqlite"), broker), broker


def _run(engine, account):
    bars = _breakout()
    now = datetime(2026, 1, 20, 10, 2, tzinfo=NY)
    return engine.cycle(now, {symbol: bars for symbol in SYMBOLS}, _clock(), account)


def test_each_symbol_is_a_slice_of_live_equity_and_the_book_stays_under_one_times():
    one = share_qty(Decimal("6252"), Decimal("500"), Decimal("0.01"), 1)
    third = share_qty(Decimal("6252"), Decimal("500"), Decimal("0.01"), 3)
    assert one == 12
    assert third == 4
    assert third * 3 * Decimal("500") <= Decimal("6252")
    capped = fit_order_qty(10, Decimal("100"), equity=Decimal("5000"), gross=Decimal("4600"), buying_power=Decimal("250"))
    assert capped == 2
    assert capped * Decimal("100") <= Decimal("250")
    assert Decimal("4600") + capped * Decimal("100") <= Decimal("5000")


def test_same_minute_signals_fill_slots_in_list_order(tmp_path):
    engine, broker = _engine(tmp_path, max_positions=1)
    _run(engine, _account())
    assert [row["symbol"] for row in broker.submitted] == ["SPY"]
    qqq = engine.store.signal_for("QQQ", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    iwm = engine.store.signal_for("IWM", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert qqq["reason"] == "SLOT_LIMIT"
    assert iwm["reason"] == "SLOT_LIMIT"
    engine.store.close()


def test_daily_loss_blocks_every_symbol(tmp_path):
    engine, broker = _engine(tmp_path)
    day = TODAY.isoformat()
    engine.store.update_day(day, equity_start="6252.00", loss_brake=1)
    _run(engine, _account())
    assert broker.submitted == []
    engine.store.close()


def test_pdt_count_is_shared_across_symbols(tmp_path):
    engine, broker = _engine(tmp_path, max_positions=3)
    _run(engine, _account(count=2))
    assert [row["symbol"] for row in broker.submitted] == ["SPY"]
    blocked = engine.store.signal_for("QQQ", TODAY.isoformat(), datetime(2026, 1, 20, 10, 0, tzinfo=NY).isoformat())
    assert blocked["reason"] == "PDT_LIMIT"
    notionals = sum(int(row["qty"]) * Decimal("102") for row in broker.submitted)
    assert notionals <= Decimal("6252")
    engine.store.close()


def test_pdt_does_not_block_a_25k_book(tmp_path):
    assert pdt_block_reason(equity=Decimal("25000"), daytrade_count=3, pattern_day_trader=True, guard=True) is None
    engine, broker = _engine(tmp_path, max_positions=3)
    account = _account(count=3, buying="25000")
    account["equity"] = "25000.00"
    _run(engine, account)
    assert [row["symbol"] for row in broker.submitted] == ["SPY", "QQQ", "IWM"]
    engine.store.close()


def test_buying_power_shrinks_the_first_order(tmp_path):
    engine, broker = _engine(tmp_path, max_positions=3)
    _run(engine, _account(buying="250"))
    assert broker.submitted
    assert all(int(row["qty"]) * Decimal("102") <= Decimal("250") for row in broker.submitted)
    engine.store.close()
