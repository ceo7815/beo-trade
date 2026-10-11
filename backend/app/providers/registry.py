from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config.settings import Settings
from app.integrations.secrets import resolve_secret
from app.providers.base import MarketDataProvider, NewsDataProvider, OptionsDataProvider
from app.providers.benzinga import BenzingaNewsProvider
from app.providers.fred import FredFeed
from app.providers.sec import SecFeed
from app.providers.thetadata import ThetaFeed, ThetaMarketProvider, ThetaOptionsProvider
from app.providers.unconfigured import UnconfiguredProvider


@dataclass
class ProviderSet:
    market: MarketDataProvider
    options: OptionsDataProvider
    news: NewsDataProvider
    macro: FredFeed | None = None
    research: SecFeed | None = None

    def status(self) -> dict[str, str]:
        return {
            "market": self.market.name,
            "options": self.options.name,
            "news": self.news.name,
            "macro": self.macro.name if self.macro is not None else "unconfigured",
            "research": self.research.name if self.research is not None else "unconfigured",
        }

    def market_status(self) -> dict:
        detail = getattr(self.market, "status_detail", None)
        if callable(detail):
            return detail()
        return _empty_status(self.market.name)

    def options_status(self) -> dict:
        detail = getattr(self.options, "status_detail", None)
        if callable(detail):
            return detail()
        return _empty_status(self.options.name)

    def news_status(self) -> dict:
        detail = getattr(self.news, "status_detail", None)
        if callable(detail):
            return detail()
        return _empty_status(self.news.name)

    def macro_status(self) -> dict:
        if self.macro is None:
            return {
                "provider": "unconfigured",
                "authenticated": None,
                "last_success": None,
                "last_fetch": None,
                "series_count": 0,
                "last_error": None,
            }
        return self.macro.status_detail()

    def research_status(self) -> dict:
        if self.research is None:
            return {
                "provider": "unconfigured",
                "authenticated": None,
                "last_success": None,
                "last_fetch": None,
                "filing_count": 0,
                "fact_count": 0,
                "submissions_status": None,
                "facts_status": None,
                "last_error": None,
            }
        return self.research.status_detail()

    def feed_statuses(self) -> dict[str, dict]:
        return {
            "market_status": self.market_status(),
            "options_status": self.options_status(),
            "news_status": self.news_status(),
            "macro_status": self.macro_status(),
            "research_status": self.research_status(),
        }


def write_feed_status(provider_set: ProviderSet) -> None:
    """Feed counters live in the worker process. The API process reads them from this file."""
    from app.ops.paths import FEED_STATUS

    try:
        FEED_STATUS.parent.mkdir(parents=True, exist_ok=True)
        temporary = FEED_STATUS.with_suffix(".tmp")
        temporary.write_text(json.dumps(provider_set.feed_statuses(), ensure_ascii=False, default=str), encoding="utf-8")
        temporary.replace(FEED_STATUS)
    except (OSError, TypeError, ValueError):
        pass


def merged_feed_statuses(provider_set: ProviderSet) -> dict[str, dict]:
    """The API's own counters, replaced by the worker's when the worker fetched more recently."""
    from app.ops.paths import FEED_STATUS

    local = provider_set.feed_statuses()
    try:
        worker = json.loads(FEED_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return local
    if not isinstance(worker, dict):
        return local
    merged = dict(local)
    for key, mine in local.items():
        theirs = worker.get(key)
        if not isinstance(theirs, dict) or theirs.get("provider") != mine.get("provider"):
            continue
        if _fetched_at(theirs) >= _fetched_at(mine):
            merged[key] = theirs
    return merged


def _fetched_at(status: dict) -> datetime:
    try:
        moment = datetime.fromisoformat(str(status.get("last_fetch") or ""))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _empty_status(provider: str) -> dict:
    return {
        "provider": provider,
        "authenticated": None,
        "last_success": None,
        "last_fetch": None,
        "quote_count": 0,
        "option_count": 0,
        "article_count": 0,
        "newest_quote": None,
        "last_error": None,
    }


def _use_thetadata(settings: Settings) -> bool:
    names = {settings.market_data_provider, settings.options_data_provider}
    if "thetadata" in names:
        return True
    if names != {"unconfigured"}:
        return False
    key, _key_origin = resolve_secret(settings, "thetadata_api_key")
    base, _base_origin = resolve_secret(settings, "thetadata_base_url")
    return bool(key or base)


def _macro_provider(settings: Settings) -> FredFeed | None:
    key, _origin = resolve_secret(settings, "fred_api_key")
    if not key:
        return None
    return FredFeed(settings)


def _news_provider(settings: Settings):
    key, _origin = resolve_secret(settings, "benzinga_api_key")
    selected = settings.news_provider
    if selected == "benzinga" or (selected == "unconfigured" and key):
        if not key:
            return UnconfiguredProvider("BENZINGA_API_KEY")
        return BenzingaNewsProvider(settings)
    return UnconfiguredProvider("NEWS_PROVIDER")


def build_providers(settings: Settings) -> ProviderSet:
    known = {"unconfigured", "replay", "benzinga", "thetadata"}
    selected = {
        "MARKET_DATA_PROVIDER": settings.market_data_provider,
        "OPTIONS_DATA_PROVIDER": settings.options_data_provider,
        "NEWS_PROVIDER": settings.news_provider,
    }
    for variable, name in selected.items():
        if name not in known:
            raise ValueError(
                f"{variable}={name} has no adapter yet. Known providers: {', '.join(sorted(known))}."
            )
    if "replay" in selected.values():
        raise ValueError("replay is available to the backtest engine, not as a live provider name")
    if _use_thetadata(settings):
        feed = ThetaFeed(settings)
        market = ThetaMarketProvider(feed)
        options = ThetaOptionsProvider(feed)
    else:
        market = UnconfiguredProvider("MARKET_DATA_PROVIDER")
        options = UnconfiguredProvider("OPTIONS_DATA_PROVIDER")
    return ProviderSet(
        market=market,
        options=options,
        news=_news_provider(settings),
        macro=_macro_provider(settings),
        research=SecFeed(settings),
    )
