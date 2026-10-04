from __future__ import annotations

from dataclasses import dataclass

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
