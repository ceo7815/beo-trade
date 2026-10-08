from __future__ import annotations

from dataclasses import dataclass

import httpx

PAPER_HTTP = "https://paper-api.alpaca.markets"
PAPER_WS = "wss://paper-api.alpaca.markets/stream"
LIVE_HTTP = "https://api.alpaca.markets"
ALLOWED_INTENTS = ("buy_to_open", "sell_to_close")
OPTION_ORDER_TYPES = ("market", "limit", "stop", "stop_limit")


class PaperOnlyError(RuntimeError):
    pass


class AlpacaNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    status: str
    latency_ms: int | None
    detail: str
    environment: str = ""
    payload: dict | None = None


def paper_base(trading_mode: str, requested_base: str = "") -> str:
    if trading_mode.upper() != "PAPER":
        raise PaperOnlyError("TRADING_MODE אינו PAPER. לא נשלחת פקודה.")
    if requested_base and requested_base.rstrip("/") != PAPER_HTTP:
        raise PaperOnlyError("יעד שאינו paper-api.alpaca.markets נחסם.")
    return PAPER_HTTP


def auth_headers(key: str, secret: str) -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


def stream_messages(key: str, secret: str) -> tuple[dict, dict]:
    return (
        {"action": "auth", "key": key, "secret": secret},
        {"action": "listen", "data": {"streams": ["trade_updates"]}},
    )


def option_order(symbol: str, qty: int, limit_price: str, intent: str, client_order_id: str) -> dict:
    if intent not in ALLOWED_INTENTS:
        raise PaperOnlyError("בגרסה הזו מותרים רק buy_to_open ו-sell_to_close.")
    if qty < 1:
        raise PaperOnlyError("כמות האופציה חייבת להיות מספר שלם חיובי.")
    return {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy" if intent == "buy_to_open" else "sell",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": limit_price,
        "position_intent": intent,
        "client_order_id": client_order_id,
    }


class AlpacaPaperClient:
    def __init__(self, key: str, secret: str, trading_mode: str = "PAPER", transport: httpx.BaseTransport | None = None) -> None:
        self.base = paper_base(trading_mode)
        if not key or not secret:
            raise AlpacaNotConfigured("נדרש API Key")
        self._client = httpx.Client(
            base_url=self.base,
            headers=auth_headers(key, secret),
            timeout=8.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def account(self) -> dict:
        response = self._client.get("/v2/account")
        response.raise_for_status()
        return response.json()

    def positions(self) -> list[dict]:
        response = self._client.get("/v2/positions")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def orders(self, status: str = "open", symbol: str = "", after: str = "", until: str = "") -> list[dict]:
        params: dict[str, str | int] = {"status": status, "limit": 100, "direction": "desc"}
        if symbol:
            params["symbols"] = symbol
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        response = self._client.get("/v2/orders", params=params)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def order(self, order_id: str) -> dict:
        response = self._client.get(f"/v2/orders/{order_id}")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        response = self._client.get("/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else None

    def configurations(self) -> dict:
        response = self._client.get("/v2/account/configurations")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def activities(self, activity_types: str = "", after: str = "", until: str = "", page_token: str = "") -> list[dict]:
        params: dict[str, str | int] = {"page_size": 100, "direction": "desc"}
        if activity_types:
            params["activity_types"] = activity_types
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        if page_token:
            params["page_token"] = page_token
        response = self._client.get("/v2/account/activities", params=params)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def clock(self) -> dict:
        response = self._client.get("/v2/clock")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        response = self._client.get("/v2/account/portfolio/history", params={"period": period, "timeframe": timeframe})
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def assets(self) -> list[dict]:
        response = self._client.get(
            "/v2/assets",
            params={"status": "active", "asset_class": "us_equity", "attributes": "has_options"},
            timeout=30.0,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def asset(self, symbol: str) -> dict:
        response = self._client.get(f"/v2/assets/{symbol}")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def submit(self, body: dict) -> dict:
        response = self._client.post("/v2/orders", json=body)
        response.raise_for_status()
        return response.json()

    def cancel(self, order_id: str) -> None:
        response = self._client.delete(f"/v2/orders/{order_id}")
        if response.status_code not in {200, 204}:
            response.raise_for_status()

    def replace(self, order_id: str, body: dict) -> dict:
        response = self._client.patch(f"/v2/orders/{order_id}", json=body)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


def public_account(raw: dict) -> dict:
    from app.broker.normalize import normalize_account

    return normalize_account(raw)


def public_position(raw: dict) -> dict:
    from app.broker.normalize import normalize_position

    return normalize_position(raw)


def public_order(raw: dict) -> dict:
    from app.broker.normalize import normalize_order

    return normalize_order(raw)


def apply_trade_update(message: dict) -> dict:
    from app.broker.normalize import EVENT_TO_INTERNAL

    data = message.get("data") if isinstance(message.get("data"), dict) else message
    event = str(data.get("event") or "")
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    status = {
        "new": "new",
        "accepted": "accepted",
        "pending_new": "pending_new",
        "partial_fill": "partially_filled",
        "fill": "filled",
        "canceled": "canceled",
        "rejected": "rejected",
        "expired": "expired",
        "done_for_day": "done_for_day",
        "replaced": "replaced",
    }.get(event, event or "unknown")
    update = {
        "event": event,
        "status": status,
        "internal_state": EVENT_TO_INTERNAL.get(event, "ERROR"),
        "broker_order_id": str(order.get("id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "symbol": str(order.get("symbol") or ""),
        "filled_qty": order.get("filled_qty"),
        "filled_avg_price": order.get("filled_avg_price"),
        "source": "alpaca-paper",
    }
    if data.get("execution_id"):
        update["execution_id"] = str(data["execution_id"])
    if data.get("price") is not None:
        update["price"] = data.get("price")
    if data.get("qty") is not None:
        update["qty"] = data.get("qty")
    if data.get("timestamp"):
        update["timestamp"] = data.get("timestamp")
    if data.get("position_qty") is not None:
        update["position_qty"] = data.get("position_qty")
    return update
