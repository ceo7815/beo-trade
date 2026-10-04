from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ai.budget import BudgetLedger
from app.config.settings import TradingConfig
from app.providers.replay import ReplayMarket, ReplayNews, ReplayOptions
from app.recommendations.pipeline import Analyzer, run_scan
from app.schemas.domain import NewsItem, OptionSnapshot, Recommendation, UnderlyingSnapshot


def run_backtest(
    underlyings: list[UnderlyingSnapshot],
    options: list[OptionSnapshot],
    news: list[NewsItem],
    decision_times: list[datetime],
    config: TradingConfig,
    analyzer: Analyzer,
    ledger: BudgetLedger,
    cash: Decimal,
    exchange_timezone: str = "America/New_York",
) -> list[Recommendation]:
    market = ReplayMarket(underlyings)
    contracts = ReplayOptions(options)
    headlines = ReplayNews(news)
    active: set[str] = set()
    output: list[Recommendation] = []
    for moment in decision_times:
        session_date = moment.astimezone(ZoneInfo(exchange_timezone)).date()
        batch = run_scan(
            market.load_underlyings(moment),
            contracts.load_options(moment),
            headlines.load_news(moment),
            config,
            analyzer,
            ledger,
            moment,
            session_date,
            cash,
            active,
        )
        output.extend(batch)
    return output
