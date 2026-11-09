"""Alpaca paper client used only by the SPY process.

It does not import the options broker, does not share the options snapshot,
and refuses any trading host other than paper-api.alpaca.markets.
"""

from __future__ import annotations

import httpx

from app.spy.config import DATA_HTTP, PAPER_HTTP, SpyConfig, assert_data_url, assert_paper_url, redact
from app.spy.strategy import is_pdt_rejection

_OPEN = {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}


class BrokerError(RuntimeError):
    pass


class PdtRejected(BrokerError):
    pass


class DuplicateOrder(BrokerError):
    pass


class SpyBroker:
    def __init__(self, cfg: SpyConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.cfg = cfg
        trading = assert_paper_url(cfg.trading_url)
        data = assert_data_url(cfg.data_url)
        if trading != PAPER_HTTP or data != DATA_HTTP:
            raise BrokerError("paper host check failed")
        if not cfg.key_id or not cfg.secret:
            raise BrokerError("missing spy keys")
        headers = {"APCA-API-KEY-ID": cfg.key_id, "APCA-API-SECRET-KEY": cfg.secret}
        timeout = httpx.Timeout(8.0)
        self._trade = httpx.Client(base_url=trading, headers=headers, timeout=timeout, transport=transport)
        self._data = httpx.Client(base_url=data, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._trade.close()
        self._data.close()

    def _log_status(self, response: httpx.Response, path: str) -> None:
        if response.status_code >= 400:
            # Path and status only. Headers and the key are not logged.
            _ = (response.status_code, path.split("?", 1)[0])

    def account(self) -> dict:
        response = self._trade.get("/v2/account")
        self._log_status(response, "/v2/account")
        if response.status_code >= 400:
            raise BrokerError(f"account {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def clock(self) -> dict:
        response = self._trade.get("/v2/clock")
        self._log_status(response, "/v2/clock")
        if response.status_code >= 400:
            raise BrokerError(f"clock {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def position(self, symbol: str) -> dict | None:
        response = self._trade.get(f"/v2/positions/{symbol}")
        if response.status_code == 404:
            return None
        self._log_status(response, f"/v2/positions/{symbol}")
        if response.status_code >= 400:
            raise BrokerError(f"position {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else None

    def open_orders(self, symbol: str) -> list[dict]:
        response = self._trade.get("/v2/orders", params={"status": "open", "symbols": symbol, "limit": 50})
        self._log_status(response, "/v2/orders")
        if response.status_code >= 400:
            raise BrokerError(f"orders {response.status_code}")
        body = response.json()
        rows = body if isinstance(body, list) else []
        return [row for row in rows if str(row.get("symbol") or "") == symbol]

    def order_by_client_id(self, client_order_id: str) -> dict | None:
        response = self._trade.get("/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise BrokerError(f"order lookup {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else None

    def position_qty(self, symbol: str) -> int:
        return signed_qty(self.position(symbol))

    def cancel_symbol(self, symbol: str) -> None:
        """Cancel resting orders for this symbol only. Never cancel the whole account."""
        for row in self.open_orders(symbol):
            order_id = str(row.get("id") or "")
            if not order_id or str(row.get("symbol") or "") != symbol:
                continue
            response = self._trade.delete(f"/v2/orders/{order_id}")
            if response.status_code not in {200, 204, 404}:
                self._log_status(response, f"/v2/orders/{order_id}")

    def submit(self, body: dict) -> dict:
        symbol = str(body.get("symbol") or "")
        if symbol != "SPY":
            raise BrokerError("spy broker accepts SPY only")
        response = self._trade.post("/v2/orders", json=body)
        if response.status_code < 400:
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
        try:
            payload = response.json()
        except Exception:
            payload = {"message": redact(response.text, self.cfg)}
        if not isinstance(payload, dict):
            payload = {"message": "rejected"}
        if is_pdt_rejection(payload, response.status_code):
            raise PdtRejected(redact(str(payload.get("message") or "pdt"), self.cfg))
        message = redact(str(payload.get("message") or ""), self.cfg).lower()
        if response.status_code in {400, 422} and "client_order_id" in message:
            raise DuplicateOrder(client_order_id_of(body))
        raise BrokerError(redact(str(payload.get("message") or response.status_code), self.cfg))

    def minute_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        """1-minute bars. IEX first; SIP only if IEX is empty and the key allows it."""
        for feed in ("iex", "sip"):
            rows: list[dict] = []
            token = ""
            failed = False
            while True:
                params = {
                    "timeframe": "1Min",
                    "start": start,
                    "end": end,
                    "limit": 10000,
                    "adjustment": "raw",
                    "feed": feed,
                }
                if token:
                    params["page_token"] = token
                response = self._data.get(f"/v2/stocks/{symbol}/bars", params=params)
                if response.status_code == 403:
                    failed = True
                    break
                if response.status_code >= 400:
                    self._log_status(response, f"/v2/stocks/{symbol}/bars")
                    failed = True
                    break
                body = response.json()
                rows.extend(row for row in (body.get("bars") or []) if isinstance(row, dict))
                token = str(body.get("next_page_token") or "")
                if not token:
                    break
            if rows:
                return rows
            if failed and feed == "iex":
                continue
            if not failed:
                return rows
        return []


def client_order_id_of(body: dict) -> str:
    return str(body.get("client_order_id") or "")


def signed_qty(row: dict | None) -> int:
    if not row:
        return 0
    try:
        qty = int(float(row.get("qty") or 0))
    except (TypeError, ValueError):
        return 0
    side = str(row.get("side") or "long").lower()
    if side == "short":
        return -abs(qty)
    return abs(qty)


def is_open_status(status: str) -> bool:
    return status in _OPEN
