import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.broker.adapter import AlpacaAdapter
from app.broker.execution import client_order_id_for, place_order
from app.broker.normalize import execution_pnl, internal_state, normalize_account, normalize_position, parse_option_symbol
from app.broker.stream import ingest_update, probe_trade_stream
from app.config.settings import Settings
from app.integrations.alpaca import LIVE_HTTP, AlpacaPaperClient, PaperOnlyError
from app.main import create_app
from app.models.db import configure_database, init_db, session_scope
from app.models.tables import ApprovalRecord, BrokerFillRecord, RecommendationRow
from app.schemas.domain import OptionRight, OptionSnapshot


NOW = datetime(2025, 10, 3, 18, 0, tzinfo=timezone.utc)
SYMBOL = "NVDA251003C00100000"


def _settings(tmp_path) -> Settings:
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "broker.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
        alpaca_api_key="PKPAPERKEY1234",
        alpaca_api_secret="secret-value-9999",
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _recommendation(observed_at: datetime | None = None, **overrides) -> RecommendationRow:
    payload = dict(
        id="rec-1",
        decision="BUY",
        underlying="NVDA",
        underlying_price=100,
        option_symbol=SYMBOL,
        call_put="CALL",
        strike=100,
        expiration="2025-10-03",
        option_price=1.2,
        bid=1.1,
        ask=1.2,
        delta=0.4,
        gamma=0.01,
        theta=-0.02,
        vega=0.1,
        iv=0.5,
        volume=100,
        open_interest=80,
        max_entry_price=1.3,
        quantity=1,
        holding_window_min=5,
        holding_window_max=30,
        reason_codes=["TEST"],
        gate_results={"DATA": True, "RISK": True},
        scenarios=[],
        quote_observed_at=observed_at or NOW,
    )
    payload.update(overrides)
    return RecommendationRow(**payload)


def _transport(state: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            state["posts"] = state.get("posts", 0) + 1
            return httpx.Response(200, json={"id": "ord-1", "client_order_id": "c1", "symbol": SYMBOL, "status": "new", "qty": "1", "side": "buy", "position_intent": "buy_to_open", "type": "limit", "limit_price": "1.20", "filled_qty": "0"})
        if path.endswith("/account/configurations"):
            return httpx.Response(200, json={"max_options_trading_level": 2, "fractional_trading": True})
        if path.endswith("/account"):
            return httpx.Response(200, json={"id": "acc", "status": "ACTIVE", "currency": "USD", "cash": state.get("cash", "100000"), "equity": "100000", "portfolio_value": "100000", "buying_power": state.get("buying_power", "400000"), "options_buying_power": state.get("buying_power", "400000"), "trading_blocked": False, "long_market_value": "0"})
        if path.endswith("/positions"):
            return httpx.Response(200, json=state.get("positions", []))
        if path.endswith("/clock"):
            return httpx.Response(200, json={"timestamp": "2025-10-03T14:00:00-04:00", "is_open": state.get("open", True), "next_open": "2025-10-06T09:30:00-04:00", "next_close": "2025-10-03T16:00:00-04:00"})
        if "/assets/" in path:
            if state.get("asset_status") == 404:
                return httpx.Response(404, json={"message": "missing"})
            return httpx.Response(200, json={"symbol": "NVDA", "class": "us_equity", "status": state.get("asset_state", "active"), "tradable": state.get("tradable", True), "attributes": state.get("attributes", ["has_options"])})
        if path.endswith("/activities"):
            return httpx.Response(200, json=state.get("activities", []))
        if "by_client_order_id" in path:
            found = state.get("by_client")
            if found is None:
                return httpx.Response(404, json={"message": "missing"})
            return httpx.Response(200, json=found)
        if path.endswith("/orders"):
            return httpx.Response(200, json=state.get("orders", []))
        return httpx.Response(404, json={"message": path})

    return httpx.MockTransport(handler)


def _adapter(state: dict) -> AlpacaAdapter:
    return AlpacaAdapter(AlpacaPaperClient("PKPAPERKEY1234", "secret-value-9999", "PAPER", _transport(state)))


def test_account_maps_only_returned_fields():
    account = normalize_account({"status": "ACTIVE", "cash": "100000", "not_a_field": "x"})
    assert account["status"] == "ACTIVE"
    assert account["cash"] == "100000"
    assert "equity" not in account
    assert "not_a_field" not in account
    assert account["source"] == "alpaca-paper"


def test_option_position_normalization():
    item = normalize_position(
        {
            "symbol": SYMBOL,
            "asset_class": "us_option",
            "asset_id": "asset-1",
            "qty": "2",
            "side": "long",
            "avg_entry_price": "1.20",
            "current_price": "1.40",
            "market_value": "280",
            "unrealized_pl": "40",
            "unrealized_plpc": "0.1666",
        }
    )
    assert item["underlying"] == "NVDA"
    assert item["right"] == "CALL"
    assert item["strike"] == "100"
    assert item["expiration"] == "2025-10-03"
    assert item["source"] == "alpaca-paper"
    missing = normalize_position({"symbol": SYMBOL, "asset_class": "us_option", "qty": "1", "unrealized_pl": "0"})
    assert "unrealized_pl" not in missing


def test_order_states_do_not_treat_submitted_as_filled():
    assert internal_state("new") == "SUBMITTED"
    assert internal_state("accepted") == "ACCEPTED"
    assert internal_state("partially_filled") == "PARTIALLY_FILLED"
    assert internal_state("filled") == "FILLED"
    assert internal_state("pending_cancel") == "CANCEL_REQUESTED"
    assert internal_state("canceled") == "CANCELLED"
    assert internal_state("rejected") == "REJECTED"
    assert internal_state("expired") == "EXPIRED"
    assert internal_state("done_for_day") == "DONE_FOR_DAY"
    assert internal_state("replaced") == "REPLACED"
    assert internal_state("nope") == "ERROR"
    assert internal_state("new") != "FILLED"


def test_order_filter_and_client_id(tmp_path):
    state = {
        "orders": [
            {"id": "1", "symbol": SYMBOL, "asset_class": "us_option", "status": "new", "client_order_id": "abc"},
            {"id": "2", "symbol": "AAPL", "asset_class": "us_equity", "status": "filled", "client_order_id": "def"},
        ]
    }
    adapter = _adapter(state)
    options = adapter.get_orders(asset_class="us_option")
    assert [row["broker_order_id"] for row in options] == ["1"]
    assert client_order_id_for("trade-1", "buy_to_open") == client_order_id_for("trade-1", "buy_to_open")
    assert client_order_id_for("trade-1", "buy_to_open") != client_order_id_for("trade-1", "sell_to_close")


def _fresh_quotes() -> list[OptionSnapshot]:
    return [OptionSnapshot("NVDA", SYMBOL, OptionRight.CALL, Decimal("100"), date(2025, 10, 3), Decimal("1.10"), Decimal("1.12"), Decimal("1.11"), 1000, 500, NOW)]


def test_idempotent_entry_does_not_post_twice(tmp_path):
    settings = _settings(tmp_path)
    state = {"posts": 0}
    adapter = _adapter(state)
    with session_scope() as session:
        session.add(_recommendation())
        session.add(ApprovalRecord(trade_id="trade-1", recommendation_id="rec-1", status="APPROVED", approved_at=NOW))
        session.commit()
        first = place_order(settings, session, adapter, {"symbol": SYMBOL, "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "rec-1", "trade_id": "trade-1"}, NOW, _fresh_quotes())
        session.commit()
    state["by_client"] = {"id": "ord-1", "client_order_id": first["order"]["client_order_id"], "symbol": SYMBOL, "status": "new"}
    with session_scope() as session:
        second = place_order(settings, session, adapter, {"symbol": SYMBOL, "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "rec-1", "trade_id": "trade-1"}, NOW)
        session.commit()
    assert state["posts"] == 1
    assert second["duplicate"] is True


def test_blocks_stale_buying_power_closed_and_invalid_contract(tmp_path):
    settings = _settings(tmp_path)
    with session_scope() as session:
        session.add(_recommendation(NOW - timedelta(seconds=1000)))
        session.commit()
        with pytest.raises(PaperOnlyError, match="אינה טרייה"):
            place_order(settings, session, _adapter({}), {"symbol": SYMBOL, "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "rec-1", "trade_id": "t-stale"}, NOW, _fresh_quotes())
    with session_scope() as session:
        session.add(_recommendation(id="rec-bp"))
        session.commit()
        with pytest.raises(PaperOnlyError, match="כוח קנייה"):
            place_order(settings, session, _adapter({"buying_power": "10"}), {"symbol": SYMBOL, "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "rec-bp", "trade_id": "t-bp"}, NOW, _fresh_quotes())
    with session_scope() as session:
        session.add(_recommendation(id="rec-closed"))
        session.commit()
        with pytest.raises(PaperOnlyError, match="השוק סגור"):
            place_order(settings, session, _adapter({"open": False}), {"symbol": SYMBOL, "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "rec-closed", "trade_id": "t-closed"}, NOW, _fresh_quotes())
    with session_scope() as session:
        with pytest.raises(PaperOnlyError, match="אינו מזוהה"):
            place_order(settings, session, _adapter({}), {"symbol": "NOT-AN-OPTION", "qty": 1, "limit_price": "1.20", "position_intent": "buy_to_open", "approved": True, "recommendation_id": "missing", "trade_id": "t-bad"}, NOW, _fresh_quotes())


def test_partial_and_duplicate_fills_do_not_double_insert(tmp_path):
    _settings(tmp_path)
    message = {
        "stream": "trade_updates",
        "data": {
            "event": "partial_fill",
            "execution_id": "exec-1",
            "price": "1.15",
            "qty": "1",
            "timestamp": "2025-10-03T14:01:00Z",
            "order": {"id": "ord-1", "symbol": SYMBOL, "filled_qty": "1", "filled_avg_price": "1.15", "status": "partially_filled"},
        },
    }
    with session_scope() as session:
        first = ingest_update(session, message)
        second = ingest_update(session, message)
        session.commit()
        stored = list(session.scalars(__import__("sqlalchemy").select(BrokerFillRecord)))
    assert first["internal_state"] == "PARTIALLY_FILLED"
    assert first["fill_inserted"] is True
    assert second["duplicate_fill"] is True
    assert len(stored) == 1
    final = dict(message)
    final["data"] = dict(message["data"], event="fill", execution_id="exec-2", qty="1")
    with session_scope() as session:
        filled = ingest_update(session, final)
        session.commit()
    assert filled["internal_state"] == "FILLED"
    assert filled["status"] != "partially_filled"


def test_reconciliation_records_mismatch_without_dropping_history(tmp_path):
    _settings(tmp_path)
    first = _adapter({"cash": "100000", "positions": [], "orders": []})
    assert first.reconcile()["ok"] is True
    second = _adapter({"cash": "99000", "positions": [{"symbol": SYMBOL, "asset_class": "us_option", "qty": "1", "current_price": "1.2", "avg_entry_price": "1.1", "unrealized_pl": "10"}], "orders": []})
    report = second.reconcile()
    assert report["ok"] is False
    assert any(item["kind"] == "account" and item["field"] == "cash" for item in report["mismatches"])
    assert any(item["kind"] == "position" for item in report["mismatches"])
    third = _adapter({"cash": "99000", "positions": [{"symbol": SYMBOL, "asset_class": "us_option", "qty": "1", "current_price": "1.2", "avg_entry_price": "1.1", "unrealized_pl": "10"}], "orders": []})
    assert third.reconcile()["ok"] is True


def test_exit_requires_open_broker_quantity():
    adapter = _adapter({"positions": []})
    with pytest.raises(PaperOnlyError, match="אין פוזיציה"):
        adapter.submit_exit_order({"symbol": SYMBOL, "qty": "1", "position_intent": "sell_to_close", "type": "limit", "limit_price": "1.5", "time_in_force": "day", "side": "sell"})
    adapter = _adapter({"positions": [{"symbol": SYMBOL, "qty": "1", "asset_class": "us_option", "current_price": "1.4"}]})
    with pytest.raises(PaperOnlyError, match="גדולה"):
        adapter.submit_exit_order({"symbol": SYMBOL, "qty": "2", "position_intent": "sell_to_close"})


def test_pnl_uses_execution_prices_and_skips_missing_mark():
    result = execution_pnl("1.20", "1.50", "2", "1.00")
    assert result is not None
    assert result["gross_pnl"] == "60.00"
    assert result["fees"] == "1.00"
    assert result["net_pnl"] == "59.00"
    assert execution_pnl("", "1.50", "1") is None
    assert parse_option_symbol("AAPL250117P00100000")["right"] == "PUT"


def test_stream_handshake_and_paper_host(monkeypatch):
    class Socket:
        def __init__(self):
            self.messages = iter(
                [
                    json.dumps({"stream": "authorization", "data": {"status": "authorized"}}),
                    json.dumps({"stream": "listening", "data": {"streams": ["trade_updates"]}}),
                ]
            )

        def send(self, _raw):
            return None

        def recv(self, timeout=None):
            return next(self.messages)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("websockets.sync.client.connect", lambda *args, **kwargs: Socket())
    probed = probe_trade_stream("PKPAPERKEY1234", "secret-value-9999")
    assert probed["connected"] is True
    assert "secret-value" not in json.dumps(probed)
    with pytest.raises(PaperOnlyError):
        AlpacaPaperClient("k", "s", "LIVE")
    client = AlpacaPaperClient("k", "s", "PAPER")
    assert LIVE_HTTP not in client.base
    client.close()


def test_broker_unavailable_returns_502(tmp_path, monkeypatch):
    settings = _settings(tmp_path)

    def factory(key, secret, trading_mode, transport=None):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        return AlpacaPaperClient(key, secret, trading_mode, httpx.MockTransport(handler))

    monkeypatch.setattr("app.broker.runtime.AlpacaPaperClient", factory)
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/broker/alpaca/account")
    assert response.status_code == 502
    assert "secret-value" not in response.text


def test_capabilities_keep_strategy_exits_local(tmp_path):
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/broker/alpaca/capabilities").json()
    assert body["strategy_position_intents"] == ["buy_to_open", "sell_to_close"]
    assert "market" in body["order_types"]
    assert body["protective_orders"] == "strategy_exits_stay_in_beo_trade"
