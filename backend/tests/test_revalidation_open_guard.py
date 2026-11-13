"""Pre-submit revalidation must survive every open-guard state.

PR #46 imported build_providers inside _submit_autonomous_buys. That assignment
makes the name local to the whole function, so the earlier revalidation call
raised UnboundLocalError and every buy was stored as REVALIDATION_DATA.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.positions.open_guard import (
    OPEN_GAP_RETRACE,
    OPEN_NO_FADE,
    GapReading,
    MinuteBar,
    commit,
    reset_open_guard,
)
from app.schemas.domain import DecisionKind, OptionRight, Recommendation
from app.workers import loops
from app.workers.loops import _submit_autonomous_buys
from tests.test_entry_pace import _database

NY = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _isolated_guard(tmp_path, monkeypatch):
    monkeypatch.setattr("app.positions.open_guard.store_path", lambda: tmp_path / "open_guard.json")
    reset_open_guard()
    yield
    reset_open_guard()


def _ny(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 13, hour, minute, second, tzinfo=NY)


def _bar(hour: int, minute: int, close: str, opened: str = "100") -> MinuteBar:
    return MinuteBar(datetime.min.time().replace(hour=hour, minute=minute), Decimal(opened), Decimal(close))


def _buy(side: str, stamp: str) -> Recommendation:
    right = OptionRight.CALL if side == "CALL" else OptionRight.PUT
    symbol = "NVDA261113C00100000" if side == "CALL" else "NVDA261113P00100000"
    moment = datetime(2026, 11, 13, 15, 0, tzinfo=NY)
    return Recommendation(
        recommendation_id=f"rec-{stamp}"[:36],
        decision=DecisionKind.BUY,
        timestamp=moment,
        underlying="NVDA",
        underlying_price=Decimal("100"),
        option_symbol=symbol,
        call_put=right,
        strike=Decimal("100"),
        expiration=moment.date() + timedelta(days=2),
        option_price=Decimal("1.02"),
        bid=Decimal("1.00"),
        ask=Decimal("1.05"),
        delta=Decimal("0.40") if side == "CALL" else Decimal("-0.40"),
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
    )


class _Adapter:
    def get_market_clock(self):
        return {"is_open": True, "next_close": "2026-11-13T21:00:00+00:00"}

    def get_positions(self):
        return []

    def get_orders(self, _status=None):
        return []

    def get_account(self):
        return {
            "equity": "100000",
            "last_equity": "100000",
            "cash": "100000",
            "buying_power": "100000",
            "options_buying_power": "100000",
        }

    def get_activities(self):
        return []

    def close(self):
        return None


def _quotes():
    quotes = loops._SpotQuotes()
    quotes.spots["NVDA"] = SimpleNamespace(open=Decimal("100"), high=Decimal("100.2"), price=Decimal("100"))
    quotes.append(SimpleNamespace())
    return quotes


@pytest.fixture
def desk(tmp_path, monkeypatch):
    settings = _database(tmp_path)
    placed: list[str] = []
    monkeypatch.setattr(loops, "_cash", lambda _settings: Decimal("100000"))
    monkeypatch.setattr(loops, "_entry_pnl", lambda _settings: Decimal("0"))
    monkeypatch.setattr(loops, "_measured_sectors", lambda _settings: {})
    monkeypatch.setattr(loops, "_revalidation_quotes", lambda _settings, _live, _buys: _quotes())
    monkeypatch.setattr("app.options.account_read.entry_block_reason", lambda: None)
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: _Adapter())
    monkeypatch.setattr("app.options.day_floor.observe_day_floor", lambda *args, **kwargs: {"closed": False, "reason": ""})
    monkeypatch.setattr("app.capital.session_lock.open_equity_from_account", lambda *args, **kwargs: Decimal("100000"))

    def place_order(*args, **kwargs):
        placed.append("placed")
        return {"duplicate": False, "order": {}}

    monkeypatch.setattr("app.broker.execution.place_order", place_order)
    return settings, placed


def _run(settings, moment: datetime, side: str, reading: GapReading | None, stamp: str) -> tuple[dict, list[str]]:
    if reading is not None:
        commit(moment, reading)
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy(side, stamp)], moment, outcomes)
    assert outcomes, "a buy must leave an outcome"
    return outcomes[0], []


CASES = [
    ("before-window", _ny(9, 0), "PUT", GapReading(Decimal("102"), Decimal("100")), None),
    ("inside-before-0945-gap-up", _ny(9, 40), "PUT", GapReading(Decimal("102"), Decimal("100")), OPEN_NO_FADE),
    ("inside-before-0945-gap-down", _ny(9, 40), "PUT", GapReading(Decimal("98"), Decimal("100")), None),
    ("inside-gap-up-put", _ny(10, 5), "PUT", GapReading(Decimal("102"), Decimal("100")), OPEN_NO_FADE),
    ("inside-gap-up-call", _ny(10, 5), "CALL", GapReading(Decimal("102"), Decimal("100")), None),
    ("inside-gap-down-call", _ny(10, 5), "CALL", GapReading(Decimal("98"), Decimal("100")), OPEN_NO_FADE),
    ("inside-gap-down-put", _ny(10, 5), "PUT", GapReading(Decimal("98"), Decimal("100")), None),
    ("after-1010-gap-up", _ny(10, 10), "PUT", GapReading(Decimal("102"), Decimal("100")), None),
    ("after-1010-gap-down", _ny(10, 10), "CALL", GapReading(Decimal("98"), Decimal("100")), None),
    ("tiny-gap-put", _ny(10, 5), "PUT", GapReading(Decimal("100.05"), Decimal("100")), OPEN_NO_FADE),
    ("tiny-gap-call", _ny(10, 5), "CALL", GapReading(Decimal("100.05"), Decimal("100")), None),
    ("flat-gap", _ny(10, 5), "PUT", GapReading(Decimal("100"), Decimal("100")), None),
    ("missing-open", _ny(10, 5), "CALL", GapReading(None, Decimal("100")), None),
    ("missing-prior", _ny(10, 5), "PUT", GapReading(Decimal("102"), None), None),
    (
        "retrace-latch-call",
        _ny(10, 5),
        "CALL",
        GapReading(Decimal("102"), Decimal("100"), (_bar(9, 45, "101"),)),
        OPEN_GAP_RETRACE,
    ),
    (
        "retrace-latch-put",
        _ny(10, 5),
        "PUT",
        GapReading(Decimal("102"), Decimal("100"), (_bar(9, 45, "101"),)),
        OPEN_GAP_RETRACE,
    ),
    (
        "retrace-latch-after-window",
        _ny(10, 10),
        "CALL",
        GapReading(Decimal("102"), Decimal("100"), (_bar(9, 45, "101"),)),
        None,
    ),
]


@pytest.mark.parametrize("stamp,moment,side,reading,guard", CASES, ids=[row[0] for row in CASES])
def test_revalidation_never_raises_for_an_open_guard_state(desk, stamp, moment, side, reading, guard):
    settings, placed = desk
    # A latched retrace is measured at 09:45 even when the buy is considered later.
    stored_at = moment if guard != OPEN_GAP_RETRACE and "retrace" not in stamp else _ny(9, 45)
    if reading is not None and stored_at != moment:
        commit(stored_at, reading)
        reading_for_run = None
    else:
        reading_for_run = reading
    outcome, _unused = _run(settings, moment, side, reading_for_run, stamp)
    reason = str(outcome["reason"])
    assert "REVALIDATION_DATA" not in reason
    assert "UnboundLocalError" not in reason
    if guard is None:
        assert not reason.startswith(OPEN_NO_FADE)
        assert not reason.startswith(OPEN_GAP_RETRACE)
        assert outcome["result"] == "submitted"
        assert placed == ["placed"]
    else:
        assert reason.startswith(guard)
        assert outcome["result"] == "blocked"
        assert placed == []


def test_build_providers_stays_a_global_in_submit():
    assert "build_providers" not in _submit_autonomous_buys.__code__.co_varnames


def test_revalidation_failure_records_the_message_and_the_traceback(desk, monkeypatch):
    settings, _placed = desk

    def explode(_settings, _live, _buys):
        raise RuntimeError("quote feed down")

    monkeypatch.setattr(loops, "_revalidation_quotes", explode)
    outcomes: list[dict] = []
    _submit_autonomous_buys(settings, [_buy("CALL", "boom")], _ny(10, 5), outcomes)
    reason = outcomes[0]["reason"]
    assert reason.startswith("REVALIDATION_DATA: RuntimeError: quote feed down")
    assert "_submit_autonomous_buys" in reason
    assert "loops.py:" in reason
