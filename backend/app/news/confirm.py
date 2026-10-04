from __future__ import annotations

from datetime import datetime

from app.config.settings import TradingConfig
from app.schemas.domain import NewsItem


def fresh_news(items: list[NewsItem], symbol: str, as_of: datetime, max_age_seconds: int) -> list[NewsItem]:
    kept: list[NewsItem] = []
    for item in items:
        if symbol not in item.symbols:
            continue
        if item.published_at > as_of or item.observed_at > as_of:
            continue
        age = (as_of - item.published_at).total_seconds()
        if age < 0 or age > max_age_seconds:
            continue
        kept.append(item)
    return kept


def news_confirmed(items: list[NewsItem], config: TradingConfig) -> bool:
    if not items:
        return False
    sources = {item.source for item in items}
    if len(sources) >= config.min_confirming_sources:
        return True
    return any(float(item.source_credibility) >= config.official_source_credibility for item in items)
