import json

import httpx
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.integrations.alpaca import LIVE_HTTP, PAPER_HTTP, PAPER_WS, apply_trade_update, option_order, paper_base
from app.integrations.secrets import mask, scrub
from app.main import create_app


def _settings(tmp_path) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )


def test_live_host_is_blocked():
    assert paper_base("PAPER") == PAPER_HTTP
    try:
        paper_base("PAPER", LIVE_HTTP)
    except Exception as exc:
        assert "paper-api" in str(exc)
    else:
        raise AssertionError("live host was accepted")
    try:
        paper_base("LIVE")
    except Exception as exc:
        assert "PAPER" in str(exc)
    else:
        raise AssertionError("live mode was accepted")


def test_option_order_is_paper_intent_only():
    body = option_order("TSLA250117C00357500", 1, "2.40", "buy_to_open", "client-1")
    assert body["position_intent"] == "buy_to_open"
    assert body["time_in_force"] == "day"
    assert body["side"] == "buy"
    sell = option_order("TSLA250117C00357500", 1, "2.80", "sell_to_close", "client-2")
    assert sell["position_intent"] == "sell_to_close"
    assert sell["side"] == "sell"


def test_trade_update_maps_fill_without_inventing_price():
    update = apply_trade_update(
        {
            "stream": "trade_updates",
            "data": {
                "event": "fill",
                "order": {"id": "ord-1", "symbol": "AAPL250321C00380000", "filled_qty": "1", "filled_avg_price": "0.02", "status": "filled"},
            },
        }
    )
    assert update["status"] == "filled"
    assert update["broker_order_id"] == "ord-1"
    assert update["filled_avg_price"] == "0.02"


def test_secrets_are_masked_and_scrubbed():
    assert mask("sk-test-secret-ABCD") == "sk-••••••••••••ABCD"
    assert "super-secret" not in scrub("token=super-secret&x=1", ["super-secret"])


def test_hub_does_not_claim_connections_or_leak_keys(tmp_path, monkeypatch):
    monkeypatch.setattr("app.integrations.secrets._read_file", lambda settings: {})
    with TestClient(create_app(_settings(tmp_path))) as client:
        listed = client.get("/api/v1/integrations")
        assert listed.status_code == 200
        body = listed.json()
        assert body["trading_mode"] == "PAPER"
        by_id = {item["id"]: item for item in body["items"]}
        assert by_id["thetadata"]["primary"] is True
        assert by_id["alpaca"]["environment"] == "PAPER"
        assert by_id["alpaca"]["status"] == "missing_key"
        assert by_id["databento"]["status"] == "planned"
        assert by_id["regime"]["status"] == "internal"
        assert by_id["redis"]["status"] != "missing_key"
        assert all(item["status"] != "connected" for item in body["items"] if item["id"] != "redis")
        raw = json.dumps(body)
        assert "APCA-API-SECRET" not in raw
        account = client.get("/api/v1/broker/alpaca/account")
        assert account.status_code == 409
        order = client.post(
            "/api/v1/broker/alpaca/orders",
            json={"symbol": "TSLA250117C00357500", "qty": 1, "limit_price": "2.40", "position_intent": "buy_to_open", "approved": False},
        )
        assert order.status_code == 409
        stream = client.get("/api/v1/broker/alpaca/stream")
        assert stream.json()["url"] == PAPER_WS
        assert stream.json()["armed"] is False


def test_alpaca_account_uses_paper_host_only(tmp_path, monkeypatch):
    from app.integrations.alpaca import AlpacaPaperClient

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"id": "acc", "equity": "100000", "cash": "25000", "buying_power": "50000", "status": "ACTIVE", "currency": "USD"},
        )

    def factory(key, secret, trading_mode, transport=None):
        return AlpacaPaperClient(key, secret, trading_mode, httpx.MockTransport(handler))

    monkeypatch.setattr("app.integrations.probes.AlpacaPaperClient", factory)
    settings = _settings(tmp_path)
    settings.alpaca_api_key = "PKPAPERKEY1234"
    settings.alpaca_api_secret = "secret-value-9999"
    with TestClient(create_app(settings)) as client:
        checked = client.post("/api/v1/integrations/alpaca/test")
        assert checked.status_code == 200
        assert checked.json()["status"] == "connected"
        assert "secret-value" not in json.dumps(checked.json())
    assert captured["url"].startswith(PAPER_HTTP)
    assert LIVE_HTTP not in captured["url"]
