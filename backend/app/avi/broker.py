"""Alpaca paper client used only by the Avi process.

Long debit spreads on QQQ only. A single-leg order is refused. The trading
host must be paper-api. Keys are never logged.
"""

from __future__ import annotations

from decimal import Decimal

import time

import httpx

from app.avi.config import SYMBOL, AviConfig, NotConfigured, redact
from app.spy.config import DATA_HTTP, PAPER_HTTP, PaperRefused, assert_data_url, assert_paper_url

_OPEN_INTENTS = {"buy_to_open", "sell_to_open"}
_CLOSE_INTENTS = {"sell_to_close", "buy_to_close"}
_WIDTHS = {Decimal("2"), Decimal("3"), Decimal("5")}


class BrokerError(RuntimeError):
    pass


class AviBroker:
    def __init__(self, cfg: AviConfig, transport: httpx.BaseTransport | None = None, sleep=time.sleep) -> None:
        self.cfg = cfg
        self._sleep = sleep
        trading = assert_paper_url(cfg.trading_url)
        data = assert_data_url(cfg.data_url)
        if trading != PAPER_HTTP or data != DATA_HTTP:
            raise PaperRefused("paper host check failed")
        if not cfg.key_id or not cfg.secret or not cfg.account_last4:
            raise NotConfigured("avi keys and account pin are required")
        headers = {"APCA-API-KEY-ID": cfg.key_id, "APCA-API-SECRET-KEY": cfg.secret}
        timeout = httpx.Timeout(8.0)
        self._trade = httpx.Client(base_url=trading, headers=headers, timeout=timeout, transport=transport)
        self._data = httpx.Client(base_url=data, headers=headers, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._trade.close()
        self._data.close()

    def _note(self, response: httpx.Response, path: str) -> None:
        if response.status_code >= 400:
            _ = (response.status_code, path.split("?", 1)[0])

    def account(self) -> dict:
        response = self._trade.get("/v2/account")
        self._note(response, "/v2/account")
        if response.status_code >= 400:
            raise BrokerError(f"account {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def clock(self) -> dict:
        response = self._trade.get("/v2/clock")
        self._note(response, "/v2/clock")
        if response.status_code >= 400:
            raise BrokerError(f"clock {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else {}

    def get_order(self, client_order_id: str) -> dict | None:
        response = self._trade.get("/v2/orders:by_client_order_id", params={"client_order_id": client_order_id})
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise BrokerError(f"order lookup {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else None

    def cancel(self, broker_order_id: str) -> None:
        if not broker_order_id:
            return
        response = self._trade.delete(f"/v2/orders/{broker_order_id}")
        if response.status_code not in {200, 204, 404}:
            self._note(response, f"/v2/orders/{broker_order_id}")

    def submit(self, body: dict) -> dict:
        self._require_spread(body)
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
        raise BrokerError(redact(str(payload.get("message") or response.status_code), self.cfg))

    def minute_bars(self, symbol: str, start: str, end: str) -> list[dict]:
        if symbol != SYMBOL:
            raise BrokerError("bars are QQQ only")
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
                if response.status_code >= 400:
                    self._note(response, f"/v2/stocks/{symbol}/bars")
                    failed = True
                    break
                body = response.json()
                rows.extend(row for row in (body.get("bars") or []) if isinstance(row, dict))
                token = str(body.get("next_page_token") or "")
                if not token:
                    break
            if rows or not failed:
                return rows
        return []

    def contracts(self, right: str, expiration_gte: str) -> list[dict]:
        if right not in {"call", "put"}:
            raise BrokerError("right refused")
        rows: list[dict] = []
        token = ""
        for _ in range(6):
            params = {
                "underlying_symbols": SYMBOL,
                "status": "active",
                "expiration_date_gte": expiration_gte,
                "expiration_date_lte": _window_end(expiration_gte),
                "type": right,
                "limit": 1000,
            }
            if token:
                params["page_token"] = token
            response = self._trade.get("/v2/options/contracts", params=params)
            self._note(response, "/v2/options/contracts")
            if response.status_code >= 400:
                raise BrokerError(f"contracts {response.status_code}")
            body = response.json()
            for row in body.get("option_contracts") or []:
                if isinstance(row, dict) and str(row.get("underlying_symbol") or SYMBOL) == SYMBOL:
                    rows.append(row)
            token = str(body.get("next_page_token") or "")
            if not token:
                break
        return rows

    def quotes(self, symbols: list[str]) -> dict[str, dict]:
        clean = [symbol for symbol in symbols if symbol]
        if not clean:
            return {}
        response = self._data_get("/v1beta1/options/snapshots", {"symbols": ",".join(clean), "feed": "indicative"})
        self._note(response, "/v1beta1/options/snapshots")
        if response.status_code >= 400:
            raise BrokerError(f"quotes {response.status_code}")
        body = response.json()
        snaps = body.get("snapshots") if isinstance(body, dict) else None
        if not isinstance(snaps, dict):
            return {}
        found: dict[str, dict] = {}
        for symbol, snap in snaps.items():
            quote = _option_quote(snap)
            if quote is not None:
                found[str(symbol)] = quote
        return found

    def _require_spread(self, body: dict) -> None:
        if body.get("order_class") != "mleg":
            raise BrokerError("naked order refused")
        legs = body.get("legs") or []
        if len(legs) != 2:
            raise BrokerError("spread must have two legs")
        intents = {str(leg.get("position_intent") or "") for leg in legs}
        if intents == _OPEN_INTENTS:
            self._require_otm_short(legs, "buy_to_open", "sell_to_open")
        elif intents == _CLOSE_INTENTS:
            return
        else:
            raise BrokerError("intent refused")
        try:
            qty = int(body.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty < 1:
            raise BrokerError("qty refused")
        if not str(body.get("limit_price") or ""):
            raise BrokerError("limit refused")

    def _data_get(self, path: str, params: dict) -> httpx.Response:
        """Indicative option quotes. A 429 waits a few seconds, at most three tries."""
        waits = (2.0, 4.0)
        response = self._data.get(path, params=params)
        for attempt, pause in enumerate(waits):
            if response.status_code != 429:
                return response
            self._sleep(pause)
            response = self._data.get(path, params=params)
            del attempt
        return response

    def _require_otm_short(self, legs: list[dict], buy_intent: str, sell_intent: str) -> None:
        buy = next(leg for leg in legs if leg.get("position_intent") == buy_intent)
        sell = next(leg for leg in legs if leg.get("position_intent") == sell_intent)
        long = _parse_occ(str(buy.get("symbol") or ""))
        short = _parse_occ(str(sell.get("symbol") or ""))
        if long is None or short is None or long[0] != short[0]:
            raise BrokerError("leg symbol refused")
        width = abs(short[1] - long[1])
        if width not in _WIDTHS:
            raise BrokerError("width refused")
        if long[0] == "C" and not (short[1] > long[1]):
            raise BrokerError("call wing refused")
        if long[0] == "P" and not (short[1] < long[1]):
            raise BrokerError("put wing refused")


def _parse_occ(symbol: str) -> tuple[str, Decimal] | None:
    if not symbol.startswith(SYMBOL) or len(symbol) != 18:
        return None
    right = symbol[9]
    if right not in {"C", "P"}:
        return None
    try:
        strike = Decimal(symbol[10:]) / Decimal(1000)
    except Exception:
        return None
    return right, strike


def _option_quote(snap: object) -> dict | None:
    if not isinstance(snap, dict):
        return None
    quote = snap.get("latestQuote") or snap.get("latest_quote") or {}
    if not isinstance(quote, dict):
        return None
    bid = quote.get("bp", quote.get("bid"))
    ask = quote.get("ap", quote.get("ask"))
    if bid in {None, ""} or ask in {None, ""}:
        return None
    return {"bid": str(bid), "ask": str(ask)}


def _window_end(expiration_gte: str) -> str:
    """Keep the chain request to the next two weeks. One signal, not a full book."""
    from datetime import date, timedelta

    try:
        start = date.fromisoformat(expiration_gte[:10])
    except ValueError:
        return expiration_gte[:10]
    return (start + timedelta(days=14)).isoformat()
