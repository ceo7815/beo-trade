"""Weekend put-credit rules: calendar, delta, size, Monday guard."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest

from app.avi.broker import AviBroker, BrokerError
from app.avi.config import AviConfig
from app.avi.engine import Engine
from app.avi.store import Store
from app.avi.weekend import (
    credit_put_order,
    hold_action,
    long_put_strike,
    pick_short_put,
    session_facts,
    weekend_contracts,
    weekend_entry_reason,
    weekend_limit,
)
from app.spy.clock import NY

FRIDAY = date(2026, 11, 13)
MONDAY = date(2026, 11, 16)
TUESDAY = date(2026, 11, 17)


def _cfg(tmp_path) -> AviConfig:
    return AviConfig(
        enabled=True,
        key_id="kid",
        secret="sec",
        account_last4="PAQTG",
        trading_url="",
        data_url="",
        risk_pct=Decimal("0.04"),
        bankroll=None,
        max_contracts=10,
        max_debit_pct=Decimal("0.08"),
        db=tmp_path / "avi.sqlite",
        weekend_enabled=True,
        weekend_max_loss_pct=Decimal("0.16"),
    )


def _occ(root: str, strike: int) -> str:
    return f"{root}261116P{strike * 1000:08d}"


class Desk:
    def __init__(self) -> None:
        self.submitted: list[dict] = []

    def get_order(self, client_order_id: str):
        return None

    def cancel(self, broker_order_id: str) -> None:
        return None

    def contracts(self, right: str, expiration_gte: str, underlying: str = "QQQ", expiration_lte: str | None = None):
        rows = []
        for strike in range(470, 511, 5):
            rows.append(
                {
                    "expiration_date": expiration_gte,
                    "strike_price": str(strike),
                    "symbol": _occ(underlying, strike),
                    "underlying_symbol": underlying,
                    "type": right,
                }
            )
        return rows

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        found = {}
        for symbol in symbols:
            strike = int(symbol[-8:]) // 1000
            delta = Decimal("-0.80") + (Decimal(strike) - Decimal("470")) * Decimal("0.03")
            premium = Decimal(max(strike - 460, 1)) / Decimal("10")
            found[symbol] = {"bid": str(premium), "ask": str(premium + Decimal("0.10")), "delta": str(delta)}
        return found

    def submit(self, body: dict) -> dict:
        self.submitted.append(body)
        return {"id": "1", "status": "accepted"}


def _cycle(tmp_path, broker, when: datetime, calendar: list[dict], spot: str = "500", equity: str = "10000"):
    store = Store(tmp_path / "avi.sqlite")
    Engine(_cfg(tmp_path), store, broker).cycle(
        when,
        [],
        None,
        {"is_open": True},
        {"account_number": "PA32PAQTG", "equity": equity},
        spots={"SPY": Decimal(spot), "QQQ": Decimal(spot)},
        calendar=calendar,
    )
    return store


def test_friday_is_the_entry_day_and_a_long_gap_or_half_day_is_not():
    nxt, half, listed = session_facts(FRIDAY, [{"date": "2026-11-13", "close": "16:00"}, {"date": "2026-11-16", "close": "16:00"}])
    assert nxt == MONDAY and half is False and listed is True
    assert weekend_entry_reason(FRIDAY, nxt, half, listed) is None
    assert weekend_entry_reason(FRIDAY, TUESDAY, False, True) == "gap"
    assert weekend_entry_reason(date(2026, 11, 12), FRIDAY, False, True) == "not_last_day"
    assert weekend_entry_reason(FRIDAY, MONDAY, True, True) == "half_day"
    assert weekend_entry_reason(FRIDAY, MONDAY, False, False) == "closed"


def test_delta_pick_and_five_dollar_wing():
    short = pick_short_put([(Decimal("500"), Decimal("-0.10")), (Decimal("490"), Decimal("-0.22")), (Decimal("480"), Decimal("-0.40"))])
    assert short == Decimal("490")
    strikes = [Decimal(value) for value in range(470, 511, 5)]
    assert long_put_strike(short, strikes) == Decimal("485")
    assert long_put_strike(Decimal("470"), strikes) is None


def test_size_splits_the_book_and_skips_a_contract_over_its_half():
    assert weekend_contracts(Decimal("10000"), Decimal("1.60")) == 2
    assert weekend_contracts(Decimal("2000"), Decimal("1.00")) == 0
    assert contract_fits_note()


def contract_fits_note():
    loss = (Decimal("5") - Decimal("1")) * 100
    half = Decimal("2000") * Decimal("0.16") / 2
    assert loss > half
    return True


def test_monday_closes_a_deep_itm_short_at_10_and_everything_at_1545():
    expiry = MONDAY
    credit = Decimal("1.00")
    assert hold_action(MONDAY, expiry, 10 * 60 + 5, Decimal("490"), Decimal("500"), credit) == "ASSIGNMENT"
    assert hold_action(MONDAY, expiry, 10 * 60 + 5, Decimal("499.5"), Decimal("500"), credit) is None
    assert hold_action(MONDAY, expiry, 9 * 60 + 45, Decimal("490"), Decimal("500"), credit) is None
    assert hold_action(MONDAY, expiry, 15 * 60 + 46, Decimal("520"), Decimal("500"), credit) == "FLAT"
    assert hold_action(FRIDAY, expiry, 15 * 60 + 50, Decimal("490"), Decimal("500"), credit) is None
    assert weekend_limit(Decimal("1.20"), Decimal("1.00"), 0) == Decimal("1.20")
    assert weekend_limit(Decimal("1.20"), Decimal("1.00"), 60) == Decimal("1.00")
    assert weekend_limit(Decimal("1.20"), Decimal("1.00"), 600) is None


def test_friday_sends_two_credit_spreads_and_a_holiday_sends_none(tmp_path):
    broker = Desk()
    when = datetime(2026, 11, 13, 15, 46, tzinfo=NY)
    store = _cycle(
        tmp_path,
        broker,
        when,
        [{"date": "2026-11-13", "close": "16:00"}, {"date": "2026-11-16", "close": "16:00"}],
    )
    assert len(broker.submitted) == 2
    roots = set()
    for body in broker.submitted:
        assert body["order_class"] == "mleg"
        assert str(body["limit_price"]).startswith("-")
        sell = next(leg for leg in body["legs"] if leg["position_intent"] == "sell_to_open")
        buy = next(leg for leg in body["legs"] if leg["position_intent"] == "buy_to_open")
        assert int(sell["symbol"][-8:]) - int(buy["symbol"][-8:]) == 5000
        roots.add(sell["symbol"][:3])
    assert roots == {"SPY", "QQQ"}
    store.close()

    quiet = Desk()
    skipped = _cycle(
        tmp_path,
        quiet,
        when,
        [{"date": "2026-11-13", "close": "16:00"}, {"date": "2026-11-17", "close": "16:00"}],
    )
    assert quiet.submitted == []
    assert skipped.meta("weekend_state:2026-11-13") == "gap"
    skipped.close()

    half = Desk()
    _cycle(tmp_path, half, when, [{"date": "2026-11-13", "close": "13:00"}, {"date": "2026-11-16", "close": "16:00"}]).close()
    assert half.submitted == []


def test_monday_engine_guards_assignment_and_the_close(tmp_path):
    def _open(path, name: str):
        store = Store(path)
        store.insert_trade(
            {
                "session_date": "2026-11-13",
                "side": "credit",
                "qty": 1,
                "long_symbol": "SPY261116P00495000",
                "short_symbol": "SPY261116P00500000",
                "long_strike": "495",
                "short_strike": "500",
                "expiry": "2026-11-16",
                "width": "5",
                "debit": "1.00",
                "mode": "weekend",
            }
        )
        return store

    deep = Desk()
    store = _open(tmp_path / "deep.sqlite", "deep")
    Engine(_cfg(tmp_path), store, deep).cycle(
        datetime(2026, 11, 16, 10, 5, tzinfo=NY),
        [],
        None,
        {"is_open": True},
        {"account_number": "PA32PAQTG", "equity": "10000"},
        spots={"SPY": Decimal("490")},
        calendar=[{"date": "2026-11-16", "close": "16:00"}],
    )
    assert deep.submitted and deep.submitted[0]["legs"][0]["position_intent"] == "sell_to_close"
    store.close()

    mild = Desk()
    store = _open(tmp_path / "mild.sqlite", "mild")
    Engine(_cfg(tmp_path), store, mild).cycle(
        datetime(2026, 11, 16, 10, 5, tzinfo=NY),
        [],
        None,
        {"is_open": True},
        {"account_number": "PA32PAQTG", "equity": "10000"},
        spots={"SPY": Decimal("499.5")},
        calendar=[{"date": "2026-11-16", "close": "16:00"}],
    )
    assert mild.submitted == []
    store.close()

    flat = Desk()
    store = _open(tmp_path / "flat.sqlite", "flat")
    Engine(_cfg(tmp_path), store, flat).cycle(
        datetime(2026, 11, 16, 15, 46, tzinfo=NY),
        [],
        None,
        {"is_open": True},
        {"account_number": "PA32PAQTG", "equity": "10000"},
        spots={"SPY": Decimal("520")},
        calendar=[{"date": "2026-11-16", "close": "16:00"}],
    )
    assert len(flat.submitted) == 1
    store.close()


def test_credit_spread_is_accepted_and_a_naked_put_is_not(tmp_path):
    class Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "1", "status": "new"})

    broker = AviBroker(_cfg(tmp_path), transport=Transport())
    body = credit_put_order("SPY261116P00500000", "SPY261116P00495000", 1, Decimal("1.20"), "avi-w-1")
    assert body["limit_price"].startswith("-")
    try:
        assert broker.submit(body)["status"] == "new"
        with pytest.raises(BrokerError):
            broker.submit({"order_class": "mleg", "qty": "1", "type": "limit", "limit_price": "-1.00", "legs": body["legs"][:1]})
    finally:
        broker.close()
