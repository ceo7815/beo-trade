"""Alpaca client that can only GET. The archive job is the only caller."""

from __future__ import annotations

import httpx

from app.integrations.alpaca import PAPER_HTTP, AlpacaNotConfigured, auth_headers, paper_base


class ArchiveNotConfigured(AlpacaNotConfigured):
    """ALPACA_100K_API_KEY / ALPACA_100K_API_SECRET are missing."""


class ReadOnlyAlpaca:
    """Paper reads for the legacy account. submit and cancel do not exist."""

    def __init__(self, key: str, secret: str, trading_mode: str = "PAPER", transport: httpx.BaseTransport | None = None) -> None:
        if not key or not secret:
            raise ArchiveNotConfigured("נדרש API Key")
        self.base = paper_base(trading_mode)
        if self.base != PAPER_HTTP:
            raise ArchiveNotConfigured("יעד שאינו paper נחסם")
        self._client = httpx.Client(
            base_url=self.base,
            headers=auth_headers(key, secret),
            timeout=httpx.Timeout(20.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _send(self, method: str, url: str, **kwargs):
        if method.upper() != "GET":
            raise RuntimeError("archive client is read-only")
        return self._client.request("GET", url, **kwargs)

    def _get(self, url: str, params: dict | None = None):
        response = self._send("GET", url, params=params)
        response.raise_for_status()
        return response.json()

    def account(self) -> dict:
        body = self._get("/v2/account")
        return body if isinstance(body, dict) else {}

    def positions(self) -> list[dict]:
        body = self._get("/v2/positions")
        return body if isinstance(body, list) else []

    def orders_page(self, *, until: str | None, limit: int) -> list[dict]:
        params: dict[str, str | int] = {
            "status": "all",
            "limit": max(1, min(int(limit), 500)),
            "direction": "desc",
        }
        if until:
            params["until"] = until
        body = self._get("/v2/orders", params)
        return body if isinstance(body, list) else []

    def activities_page(self, *, page_token: str | None, limit: int) -> list[dict]:
        params: dict[str, str | int] = {
            "page_size": max(1, min(int(limit), 100)),
            "direction": "desc",
        }
        if page_token:
            params["page_token"] = page_token
        body = self._get("/v2/account/activities", params)
        return body if isinstance(body, list) else []

    def portfolio_history(self, period: str, timeframe: str) -> dict:
        body = self._get("/v2/account/portfolio/history", {"period": period, "timeframe": timeframe})
        return body if isinstance(body, dict) else {}
