from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx
import structlog

from app.broker import metrics

log = structlog.get_logger()

# Reads retry a short burst. The cap keeps a monitor cycle from sleeping on a long Retry-After.
READ_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 0.2
BACKOFF_CAP_SECONDS = 2.0
RETRY_AFTER_CAP_SECONDS = 8.0
_UNKNOWN = object()

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


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _parse_retry_after(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, moment.timestamp() - time.time())


def _retry_delay(response: httpx.Response | None, attempt: int, *, limited: bool) -> float:
    """Honor Retry-After when Alpaca sent one. Otherwise exponential backoff with jitter, capped."""
    if limited and response is not None:
        parsed = _parse_retry_after(response.headers.get("Retry-After", ""))
        if parsed is not None:
            return min(parsed, RETRY_AFTER_CAP_SECONDS)
    base = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2**attempt))
    return min(BACKOFF_CAP_SECONDS, base + random.uniform(0, base * 0.25))


def _submission_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or 500 <= status <= 599
    return False


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
    def __init__(
        self,
        key: str,
        secret: str,
        trading_mode: str = "PAPER",
        transport: httpx.BaseTransport | None = None,
        shared_cache: bool | None = None,
    ) -> None:
        self.base = paper_base(trading_mode)
        if not key or not secret:
            raise AlpacaNotConfigured("נדרש API Key")
        # Injected transports are tests and one-off probes. They stay off the shared snapshot.
        self.shared_cache = (transport is None) if shared_cache is None else shared_cache
        self._client = httpx.Client(
            base_url=self.base,
            headers=auth_headers(key, secret),
            timeout=8.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, url: str, *, retry: bool, **kwargs) -> httpx.Response:
        """Retry reads on 429, 5xx, and transport errors. Mutations pass retry=False."""
        attempts = READ_ATTEMPTS if retry else 1
        for attempt in range(attempts):
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                metrics.note_call()
                if (not retry) or attempt >= attempts - 1:
                    raise
                delay = _retry_delay(None, attempt, limited=False)
                log.warning("alpaca.transient", method=method, path=url, error=type(exc).__name__, attempt=attempt + 1, sleep_seconds=round(delay, 3))
                _pause(delay)
                continue
            metrics.note_call()
            status = response.status_code
            if status == 429:
                metrics.note_429()
                if retry and attempt < attempts - 1:
                    delay = _retry_delay(response, attempt, limited=True)
                    log.warning("alpaca.rate_limited", method=method, path=url, attempt=attempt + 1, sleep_seconds=round(delay, 3))
                    _pause(delay)
                    continue
                return response
            if 500 <= status <= 599:
                metrics.note_5xx()
                if retry and attempt < attempts - 1:
                    delay = _retry_delay(response, attempt, limited=False)
                    log.warning("alpaca.server_error", method=method, path=url, status=status, attempt=attempt + 1, sleep_seconds=round(delay, 3))
                    _pause(delay)
                    continue
                return response
            return response
        raise RuntimeError("alpaca request ended without a response")

    def _invalidate_reads(self) -> None:
        if not self.shared_cache:
            return
        try:
            from app.broker.snapshot import invalidate

            invalidate("account", "positions", "open_orders")
        except Exception:
            return

    def account(self) -> dict:
        response = self._send("GET", "/v2/account", retry=True)
        response.raise_for_status()
        return response.json()

    def positions(self) -> list[dict]:
        response = self._send("GET", "/v2/positions", retry=True)
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
        response = self._send("GET", "/v2/orders", params=params, retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def order(self, order_id: str) -> dict:
        response = self._send("GET", f"/v2/orders/{order_id}", retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        response = self._send("GET", "/v2/orders:by_client_order_id", params={"client_order_id": client_order_id}, retry=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else None

    def configurations(self) -> dict:
        response = self._send("GET", "/v2/account/configurations", retry=True)
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
        response = self._send("GET", "/v2/account/activities", params=params, retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def clock(self) -> dict:
        response = self._send("GET", "/v2/clock", retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def portfolio_history(self, period: str = "1M", timeframe: str = "1D") -> dict:
        response = self._send("GET", "/v2/account/portfolio/history", params={"period": period, "timeframe": timeframe}, retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def assets(self) -> list[dict]:
        response = self._send(
            "GET",
            "/v2/assets",
            params={"status": "active", "asset_class": "us_equity", "attributes": "has_options"},
            timeout=30.0,
            retry=True,
        )
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    def asset(self, symbol: str) -> dict:
        response = self._send("GET", f"/v2/assets/{symbol}", retry=True)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    def submit(self, body: dict) -> dict:
        """POST /v2/orders. Not retried unless client_order_id is set and the order is confirmed absent."""
        payload = self._post_order(body)
        self._invalidate_reads()
        return payload

    def _post_order(self, body: dict) -> dict:
        try:
            response = self._send("POST", "/v2/orders", json=body, retry=False)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            if not _submission_retryable(exc):
                raise
            client_order_id = str(body.get("client_order_id") or "").strip()
            if not client_order_id:
                raise
            existing = self._order_already_submitted(client_order_id)
            if existing is _UNKNOWN:
                raise exc
            if existing is not None:
                return existing
            response = self._send("POST", "/v2/orders", json=body, retry=False)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}

    def _order_already_submitted(self, client_order_id: str):
        try:
            return self.order_by_client_id(client_order_id)
        except Exception:
            return _UNKNOWN

    def cancel(self, order_id: str) -> None:
        response = self._send("DELETE", f"/v2/orders/{order_id}", retry=False)
        if response.status_code not in {200, 204}:
            response.raise_for_status()
        self._invalidate_reads()

    def replace(self, order_id: str, body: dict) -> dict:
        response = self._send("PATCH", f"/v2/orders/{order_id}", json=body, retry=False)
        response.raise_for_status()
        self._invalidate_reads()
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
