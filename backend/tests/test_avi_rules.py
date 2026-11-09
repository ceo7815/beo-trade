"""Avi options guards: signal, strikes, size, exits, account pin, paper-only."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.avi.broker import AviBroker, BrokerError
from app.avi.config import AviConfig, PaperRefused, account_matches, load_config, startup_mode
from app.avi.engine import Engine
from app.avi.store import Store
from app.avi.strategy import (
    Bar,
    chase_price,
    choose_expiry,
    choose_size,
    entry_order,
    evaluate_trend,
    exit_reason,
    occ_symbol,
    wing_strikes,
)
from app.spy.clock import NY

TODAY = date(2026, 1, 20)
PIN = "PAQTG"
ACCOUNT = "PA32PAQTG"


def _cfg(tmp_path, **overrides) -> AviConfig:
    data = dict(
        enabled=True,
        key_id="kid",
        secret="super-secret-value",
        account_last4=PIN,
        trading_url="",
        data_url="",
        risk_pct=Decimal("0.04"),
        bankroll=Decimal("6274"),
        max_contracts=10,
        max_debit_pct=Decimal("0.08"),
        db=tmp_path / "avi.sqlite",
    )
    data.update(overrides)
    return AviConfig(**data)


def _bar(day: date, hour: int, minute: int, close: str, open_px: str | None = None) -> Bar:
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)
    op = Decimal(open_px if open_px is not None else close)
    px = Decimal(close)
    return Bar(start=start, open=op, high=max(op, px), low=min(op, px), close=px, volume=Decimal("1000"))


def _up_bars() -> list[Bar]:
    bars: list[Bar] = []
    for offset in range(14, 0, -1):
        day = TODAY - timedelta(days=offset)
        bars.extend([_bar(day, 9, 30, "100", "100"), _bar(day, 10, 29, "101", "100")])
    bars.extend([_bar(TODAY, 9, 30, "100", "100"), _bar(TODAY, 10, 29, "104", "100")])
    return bars


class FakeBroker:
    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.contracts_called = 0
        self.quotes_called = 0

    def get_order(self, client_order_id: str):
        return None

    def cancel(self, broker_order_id: str) -> None:
        return None

    def contracts(self, right: str, expiration_gte: str) -> list[dict]:
        self.contracts_called += 1
        rows = []
        letter = "C" if right == "call" else "P"
        for expiry, stamp in ((TODAY, "260120"), (TODAY + timedelta(days=1), "260121")):
            for strike in range(90, 121):
                rows.append(
                    {
                        "expiration_date": expiry.isoformat(),
                        "strike_price": str(strike),
                        "symbol": f"QQQ{stamp}{letter}{strike * 1000:08d}",
                        "underlying_symbol": "QQQ",
                        "type": right,
                    }
                )
        return rows

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        self.quotes_called += 1
        found = {}
        for symbol in symbols:
            strike = Decimal(symbol[10:]) / Decimal(1000)
            bid = Decimal("8") - (strike - Decimal("100")) * Decimal("0.3")
            found[symbol] = {"bid": str(bid), "ask": str(bid + Decimal("0.10"))}
        return found

    def submit(self, body: dict) -> dict:
        self.submitted.append(body)
        return {"id": "broker-1", "status": "accepted"}


def test_signal_up_down_and_none():
    up, reason, _upper, _lower = evaluate_trend(
        price=Decimal("102"), open_px=Decimal("100"), sigma=Decimal("0.01"), vwap=Decimal("100.4"), vix=Decimal("18")
    )
    assert (up, reason) == ("up", "trend")
    down, reason, _, _ = evaluate_trend(
        price=Decimal("98"), open_px=Decimal("100"), sigma=Decimal("0.01"), vwap=Decimal("99.5"), vix=Decimal("18")
    )
    assert (down, reason) == ("down", "trend")
    flat, reason, _, _ = evaluate_trend(
        price=Decimal("100.4"), open_px=Decimal("100"), sigma=Decimal("0.01"), vwap=Decimal("100.2"), vix=Decimal("18")
    )
    assert (flat, reason) == ("none", "inside")
    skipped, reason, _, _ = evaluate_trend(
        price=Decimal("102"), open_px=Decimal("100"), sigma=Decimal("0.01"), vwap=Decimal("100"), vix=Decimal("30")
    )
    assert (skipped, reason) == ("skipped", "vix")
    missing, reason, _, _ = evaluate_trend(
        price=Decimal("102"), open_px=Decimal("100"), sigma=Decimal("0.01"), vwap=Decimal("100"), vix=None
    )
    assert (missing, reason) == ("skipped", "vix_missing")


def test_strikes_skip_zero_dte_and_sell_the_wing():
    strikes = [Decimal(value) for value in range(470, 491)]
    assert wing_strikes(Decimal("480.4"), strikes, "up", Decimal("5")) == (Decimal("480"), Decimal("485"))
    assert wing_strikes(Decimal("480.4"), strikes, "down", Decimal("5")) == (Decimal("480"), Decimal("475"))
    assert wing_strikes(Decimal("480"), strikes, "up", Decimal("2")) == (Decimal("480"), Decimal("482"))
    assert choose_expiry(TODAY, [TODAY, TODAY + timedelta(days=2), TODAY + timedelta(days=10)]) == TODAY + timedelta(days=2)
    assert choose_expiry(TODAY, [TODAY]) is None
    symbol = occ_symbol("QQQ", TODAY + timedelta(days=1), "C", Decimal("480"))
    assert symbol == "QQQ260121C00480000"
    body = entry_order(symbol, occ_symbol("QQQ", TODAY + timedelta(days=1), "C", Decimal("485")), 1, Decimal("1.20"), "avi-1")
    assert body["order_class"] == "mleg"
    assert [leg["position_intent"] for leg in body["legs"]] == ["buy_to_open", "sell_to_open"]


def test_size_uses_risk_then_cap_then_narrower_wings():
    bankroll = Decimal("6274")
    sized = choose_size({Decimal("5"): Decimal("1.00")}, bankroll, Decimal("0.04"), 10, Decimal("0.08"))
    assert sized.contracts == 2 and sized.width == Decimal("5") and sized.reason == "sized"
    one = choose_size({Decimal("5"): Decimal("3.00")}, bankroll, Decimal("0.04"), 10, Decimal("0.08"))
    assert one.contracts == 1 and one.width == Decimal("5") and one.reason == "one_lot"
    narrow = choose_size(
        {Decimal("5"): Decimal("6.00"), Decimal("3"): Decimal("2.00"), Decimal("2"): Decimal("1.50")},
        bankroll,
        Decimal("0.04"),
        10,
        Decimal("0.08"),
    )
    assert narrow.contracts == 1 and narrow.width == Decimal("3") and narrow.reason == "narrowed"
    blocked = choose_size(
        {Decimal("5"): Decimal("6"), Decimal("3"): Decimal("6"), Decimal("2"): Decimal("6")},
        bankroll,
        Decimal("0.04"),
        10,
        Decimal("0.08"),
    )
    assert blocked.contracts == 0 and blocked.reason == "SKIP_DEBIT_CAP"
    capped = choose_size({Decimal("5"): Decimal("0.20")}, bankroll, Decimal("0.04"), 3, Decimal("0.08"))
    assert capped.contracts == 3


def test_exit_rules():
    debit = Decimal("1.00")
    assert exit_reason(side="up", debit=debit, value=Decimal("1.70"), closes=[], minute=11 * 60) == "TAKE_PROFIT"
    assert exit_reason(side="up", debit=debit, value=Decimal("0.50"), closes=[], minute=11 * 60) == "STOP_VALUE"
    hold = [(11 * 60, Decimal("101"), Decimal("100"))]
    assert exit_reason(side="up", debit=debit, value=Decimal("1.10"), closes=hold, minute=11 * 60) is None
    crossed = [(11 * 60, Decimal("99"), Decimal("100")), (11 * 60 + 1, Decimal("98.5"), Decimal("100"))]
    assert exit_reason(side="up", debit=debit, value=Decimal("1.10"), closes=crossed, minute=11 * 60 + 1) == "VWAP"
    put_cross = [(11 * 60, Decimal("101"), Decimal("100")), (11 * 60 + 1, Decimal("102"), Decimal("100"))]
    assert exit_reason(side="down", debit=debit, value=Decimal("1.10"), closes=put_cross, minute=11 * 60 + 1) == "VWAP"
    assert exit_reason(side="up", debit=debit, value=Decimal("1.10"), closes=[], minute=15 * 60 + 45) == "TIME"
    assert chase_price(Decimal("1.20"), Decimal("1.40"), 0) == Decimal("1.20")
    assert chase_price(Decimal("1.20"), Decimal("1.40"), 30) == Decimal("1.21")
    assert chase_price(Decimal("1.20"), Decimal("1.40"), 300) is None


def test_account_pin_is_a_suffix_and_refuses_a_mismatch(tmp_path):
    assert account_matches(PIN, ACCOUNT)
    assert not account_matches("XXXX", ACCOUNT)
    assert not account_matches("", ACCOUNT)
    assert not account_matches("PA32PAQTG", ACCOUNT)
    broker = FakeBroker()
    store = Store(tmp_path / "avi.sqlite")
    now = datetime(2026, 1, 20, 10, 31, tzinfo=NY)
    Engine(_cfg(tmp_path), store, broker).cycle(
        now, _up_bars(), Decimal("18"), {"is_open": True}, {"account_number": "PA3OTHER99", "equity": "6274"}
    )
    assert broker.submitted == []
    assert broker.contracts_called == 0
    assert store.meta("account_mismatch") == "1"
    store.close()


def test_paper_host_and_required_keys_and_no_retired_account(monkeypatch, tmp_path):
    monkeypatch.setenv("AVI_DB_PATH", str(tmp_path / "avi.sqlite"))
    for name in (
        "AVI_ENGINE_ENABLED",
        "AVI_ALPACA_API_KEY",
        "AVI_ALPACA_SECRET_KEY",
        "AVI_ACCOUNT_LAST4",
        "AVI_ALPACA_BASE_URL",
        "ALPACA_100K_API_KEY",
        "ALPACA_100K_API_SECRET",
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    assert startup_mode(load_config()) == "disabled"
    monkeypatch.setenv("AVI_ENGINE_ENABLED", "true")
    monkeypatch.setenv("ALPACA_100K_API_KEY", "retired-key")
    monkeypatch.setenv("ALPACA_100K_API_SECRET", "retired-secret")
    monkeypatch.setenv("ALPACA_API_KEY", "options-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "options-secret")
    bare = load_config()
    assert startup_mode(bare) == "not_configured"
    assert bare.key_id == "" and bare.secret == ""
    assert "retired-key" not in repr(bare)
    monkeypatch.setenv("AVI_ALPACA_API_KEY", "kid")
    monkeypatch.setenv("AVI_ALPACA_SECRET_KEY", "super-secret-value")
    assert startup_mode(load_config()) == "not_configured"
    monkeypatch.setenv("AVI_ACCOUNT_LAST4", PIN)
    assert startup_mode(load_config()) == "running"
    assert load_config().key_id == "kid"
    monkeypatch.setenv("AVI_ALPACA_BASE_URL", "https://api.alpaca.markets")
    assert startup_mode(load_config()) == "refused"
    with pytest.raises(PaperRefused):
        AviBroker(_cfg(tmp_path, trading_url="https://api.alpaca.markets"))
    live = AviBroker(_cfg(tmp_path))
    with pytest.raises(BrokerError):
        live.submit({"symbol": "QQQ", "qty": "1", "side": "buy", "type": "market", "time_in_force": "day"})
    with pytest.raises(BrokerError):
        live.submit({"order_class": "mleg", "qty": "1", "type": "limit", "limit_price": "1.00", "legs": [{"symbol": "QQQ260121C00480000", "ratio_qty": "1", "side": "buy", "position_intent": "buy_to_open"}]})
    live.close()
    cfg = _cfg(tmp_path)
    assert "super-secret-value" not in repr(cfg)


def test_entry_is_a_debit_spread_and_a_wrong_pin_sends_nothing_else(tmp_path):
    broker = FakeBroker()
    store = Store(tmp_path / "avi.sqlite")
    now = datetime(2026, 1, 20, 10, 31, tzinfo=NY)
    Engine(_cfg(tmp_path, bankroll=None), store, broker).cycle(
        now, _up_bars(), Decimal("18"), {"is_open": True}, {"account_number": ACCOUNT, "equity": "6274"}
    )
    assert store.meta("equity_open:2026-01-20") == "6274.00"
    assert broker.quotes_called == 1
    assert len(broker.submitted) == 1
    body = broker.submitted[0]
    assert body["order_class"] == "mleg"
    assert body["qty"] == "1"
    intents = {leg["position_intent"] for leg in body["legs"]}
    assert intents == {"buy_to_open", "sell_to_open"}
    symbols = [leg["symbol"] for leg in body["legs"]]
    assert all("260121" in symbol for symbol in symbols)
    assert all("260120" not in symbol for symbol in symbols)
    long_leg = next(leg for leg in body["legs"] if leg["position_intent"] == "buy_to_open")
    short_leg = next(leg for leg in body["legs"] if leg["position_intent"] == "sell_to_open")
    assert long_leg["symbol"].endswith("C00104000")
    assert short_leg["symbol"].endswith("C00109000")
    store.close()


def test_systems_ready_follows_the_enabled_flag(monkeypatch, tmp_path):
    db = tmp_path / "avi.sqlite"
    monkeypatch.setenv("AVI_DB_PATH", str(db))
    monkeypatch.delenv("AVI_ENGINE_ENABLED", raising=False)
    from app.engines.registry import avi_row

    assert avi_row()["label"] == "מסחר אופציות אבי"
    assert avi_row()["label_en"] == "Avi options"
    assert avi_row()["status_path"] == "/api/v1/avi/status"
    assert avi_row()["ready"] is False
    monkeypatch.setenv("AVI_ENGINE_ENABLED", "true")
    assert avi_row()["ready"] is True
    status = db.with_name("avi_status.json")
    status.write_text(json.dumps({"enabled": False, "mode": "disabled", "paper_only": True}), encoding="utf-8")
    assert avi_row()["ready"] is False
    status.write_text(json.dumps({"enabled": True, "mode": "running", "paper_only": True, "account_match": True}), encoding="utf-8")
    assert avi_row()["ready"] is True


def test_avi_package_does_not_touch_the_other_engines():
    root = Path(__file__).resolve().parents[1] / "app" / "avi"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "app.workers" not in text
        assert "app.options" not in text
        assert "app.broker" not in text
        assert "ALPACA_100K" not in text
        assert 'get("ALPACA_API_KEY"' not in text
        assert 'get("ALPACA_API_SECRET"' not in text
        assert "PA32PAQTG" not in text
        assert '"PAQTG"' not in text
