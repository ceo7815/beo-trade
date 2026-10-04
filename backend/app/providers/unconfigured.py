from __future__ import annotations

from datetime import datetime

from app.providers.base import ProviderNotConfigured
from app.schemas.domain import NewsItem, OptionSnapshot, UnderlyingSnapshot


class UnconfiguredProvider:
    def __init__(self, variable: str) -> None:
        self.name = "unconfigured"
        self.variable = variable

    def load_underlyings(self, as_of: datetime) -> list[UnderlyingSnapshot]:
        raise ProviderNotConfigured(self.variable)

    def load_options(self, as_of: datetime) -> list[OptionSnapshot]:
        raise ProviderNotConfigured(self.variable)

    def load_news(self, as_of: datetime, symbols: tuple[str, ...] | None = None) -> list[NewsItem]:
        raise ProviderNotConfigured(self.variable)
