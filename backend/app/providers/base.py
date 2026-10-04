from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.schemas.domain import NewsItem, OptionSnapshot, UnderlyingSnapshot


class ProviderNotConfigured(RuntimeError):
    def __init__(self, variable: str) -> None:
        super().__init__(variable)
        self.variable = variable


class ProviderUnavailable(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class MarketDataProvider(Protocol):
    name: str

    def load_underlyings(self, as_of: datetime) -> list[UnderlyingSnapshot]:
        """Return the latest snapshot per symbol with observed_at <= as_of."""


class OptionsDataProvider(Protocol):
    name: str

    def load_options(self, as_of: datetime) -> list[OptionSnapshot]:
        """Return the latest quote per contract with observed_at <= as_of."""


class NewsDataProvider(Protocol):
    name: str

    def load_news(self, as_of: datetime, symbols: tuple[str, ...] | None = None) -> list[NewsItem]:
        """Return items published at or before as_of. symbols filters when the provider supports it."""
