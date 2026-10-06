from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from collections.abc import Callable

from app.ai.budget import BudgetLedger
from app.ai.fingerprint import decision_fingerprint
from app.market.regime_policy import regime_trade_policy
from app.options.risk_limits import daily_loss_reason, sector_exposure_reason
from app.config.settings import TradingConfig
from app.news.confirm import fresh_news, news_confirmed
from app.options.selector import rank_contracts, risk_plan
from app.quant.bars import atr, expected_move, vwap
from app.quant.events import detect_events
from app.quant.volatility import historical_volatility
from app.quant.filters import reject_option, reject_underlying
from app.quant.scenarios import enrich_option, scenarios_for
from app.recommendations.decision import build_recommendation, direction_matches
from app.schemas.domain import (
    DecisionKind,
    ModelOutput,
    NewsItem,
    OptionSnapshot,
    Recommendation,
    UnderlyingSnapshot,
)


class LeakageError(RuntimeError):
    pass


class Analyzer:
    def analyze(self, packet: dict, as_of: datetime) -> ModelOutput:
        raise NotImplementedError


def assert_no_future(
    underlyings: list[UnderlyingSnapshot],
    options: list[OptionSnapshot],
    news: list[NewsItem],
    as_of: datetime,
) -> None:
    for snapshot in underlyings:
        if snapshot.observed_at > as_of or any(bar.observed_at > as_of for bar in snapshot.bars):
            raise LeakageError("underlying data is newer than the decision time")
    for option in options:
        if option.observed_at > as_of:
            raise LeakageError("option data is newer than the decision time")
    for item in news:
        if item.published_at > as_of or item.observed_at > as_of:
            raise LeakageError("news is newer than the decision time")


def _underlying_quant(underlying: UnderlyingSnapshot) -> dict:
    closes = [float(bar.close) for bar in underlying.bars]
    highs = [float(bar.high) for bar in underlying.bars]
    lows = [float(bar.low) for bar in underlying.bars]
    volumes = [bar.volume for bar in underlying.bars]
    return {
        "atr": atr(highs, lows, closes),
        "vwap": vwap(closes, volumes),
        "historical_volatility": historical_volatility(closes),
        "iv_rank": None,
        "iv_percentile": None,
    }


def _packet(
    underlying: UnderlyingSnapshot,
    shortlist: list[OptionSnapshot],
    news: list[NewsItem],
    events: set[str],
    scenarios: dict,
    regime: dict | None = None,
    macro: dict | None = None,
    research: dict | None = None,
) -> dict:
    packet = {
        "underlying": {
            "symbol": underlying.symbol,
            "price": float(underlying.price),
            "change_percent": float(underlying.change_percent),
            "relative_volume": float(underlying.relative_volume),
            "volume": underlying.volume,
            "observed_at": underlying.observed_at.isoformat(),
            "quant": _underlying_quant(underlying),
        },
        "events": sorted(events),
        "news": [
            {
                "id": item.article_id,
                "source": item.source,
                "author": item.author,
                "source_host": item.source_host,
                "headline": item.headline,
                "published_at": item.published_at.isoformat(),
                "updated_at": item.observed_at.isoformat(),
                "symbols": list(item.symbols),
                "channels": list(item.channels),
                "url": item.url,
                "category": item.category,
                "event_type": item.event_type,
            }
            for item in news
        ],
        "shortlist": [
            {
                "option_symbol": option.option_symbol,
                "call_put": option.right.value,
                "strike": float(option.strike),
                "expiration": option.expiration.isoformat(),
                "bid": float(option.bid),
                "ask": float(option.ask),
                "mid": float(option.mid),
                "volume": option.volume,
                "open_interest": option.open_interest,
                "iv": float(option.implied_volatility or 0),
                "delta": float(option.delta or 0),
                "gamma": float(option.gamma or 0),
                "theta": float(option.theta or 0),
                "vega": float(option.vega or 0),
                "expected_move": expected_move(
                    float(underlying.price),
                    float(option.implied_volatility),
                    max((option.expiration - underlying.observed_at.date()).days, 0),
                ) if option.implied_volatility is not None else None,
                "scenarios": scenarios[option.option_symbol],
            }
            for option in shortlist
        ],
    }
    if regime is not None:
        packet["market_regime"] = regime
    if macro is not None:
        packet["macro"] = macro
    if research is not None:
        packet["research"] = research
    return packet


def run_scan(
    underlyings: list[UnderlyingSnapshot],
    options: list[OptionSnapshot],
    news: list[NewsItem],
    config: TradingConfig,
    analyzer: Analyzer | None,
    ledger: BudgetLedger,
    as_of: datetime,
    session_date: date,
    cash: Decimal,
    active_symbols: set[str],
    regime: dict | None = None,
    macro: dict | None = None,
    research: dict | None = None,
    reuse: Callable[[str, datetime], ModelOutput | None] | None = None,
    acquire: Callable[[str, datetime], bool] | None = None,
    daily_pnl: Decimal | None = None,
    sector_book: dict[str, Decimal] | None = None,
    sector_of: dict[str, str] | None = None,
    record_ai: Callable[[ModelOutput, str, str], None] | None = None,
    enforce_loss: bool = False,
    enforce_exposure: bool = False,
    counts: dict | None = None,
    open_underlyings: set[str] | None = None,
    open_planned_risk: Decimal = Decimal("0"),
) -> list[Recommendation]:
    assert_no_future(underlyings, options, news, as_of)
    results: list[Recommendation] = []
    buys = 0
    seen: dict[str, ModelOutput] = {}
    ai_calls = 0
    contracts_checked = 0
    contracts_passed = 0
    news_passed = 0
    risk_checked = 0
    risk_passed = 0
    for underlying in underlyings:
        if reject_underlying(underlying, config, as_of):
            continue
        events = detect_events(underlying, config)
        symbol_news = fresh_news(news, underlying.symbol, as_of, config.news_max_age_seconds)
        confirmed = news_confirmed(symbol_news, config)
        signal_count = len(events) + (1 if confirmed else 0)
        if signal_count < config.min_events:
            continue
        chain = [option for option in options if option.underlying == underlying.symbol]
        contracts_checked += len(chain)
        if confirmed:
            news_passed += 1
        enriched: list[OptionSnapshot] = []
        scenario_map: dict[str, list[dict]] = {}
        for option in chain:
            if option.option_symbol in active_symbols:
                continue
            filled = enrich_option(option, underlying, config, as_of)
            if reject_option(filled, underlying, config, as_of, session_date):
                continue
            priced = scenarios_for(filled, underlying, config, as_of)
            enriched.append(filled)
            scenario_map[filled.option_symbol] = [
                {
                    "move_percent": item.move_percent,
                    "option_price": float(item.option_price),
                    "change_percent": float(item.change_percent),
                }
                for item in priced
            ]
        contracts_passed += len(enriched)
        shortlist = [
            item
            for item in rank_contracts(enriched, underlying, config, as_of)
            if direction_matches(underlying, item, events)
        ]
        if not shortlist:
            continue
        risk_checked += 1
        company = None if research is None else (research.get("companies") or {}).get(underlying.symbol)
        symbol_research = None if not company else {"source": "sec", "company": company}
        packet = _packet(underlying, shortlist, symbol_news, events, scenario_map, regime, macro, symbol_research)
        digest = decision_fingerprint(packet)
        model = seen.get(digest)
        allowed = ledger.allow("decision", as_of)
        scan_capped = ai_calls >= config.ai_max_calls_per_scan
        if model is None and allowed and reuse is not None:
            try:
                model = reuse(digest, as_of)
            except Exception:
                model = None
        if model is None and allowed and not scan_capped and analyzer is not None:
            claimed = True
            if acquire is not None:
                try:
                    claimed = acquire(digest, as_of)
                except Exception:
                    claimed = False
            if claimed:
                ai_calls += 1
                try:
                    model = analyzer.analyze(packet, as_of)
                except Exception:
                    model = None
                if model is not None and record_ai is not None and not (model.raw or {}).get("cached"):
                    try:
                        record_ai(model, digest, underlying.symbol)
                    except Exception:
                        pass
        if model is not None:
            model.input_hash = digest
            seen[digest] = model
        budget_blocked = model is None and (not allowed or scan_capped)
        chosen = None
        if model is not None:
            chosen = next((item for item in shortlist if item.option_symbol == model.option_symbol), None)
        target = chosen or shortlist[0]
        sector_name = (sector_of or {}).get(underlying.symbol, "UNCLASSIFIED")
        book = sector_book or {}
        sector_premium = Decimal(str(book.get(sector_name, 0)))
        open_premium = sum((Decimal(str(value)) for value in book.values()), Decimal("0"))
        same_underlying = 1 if underlying.symbol in (open_underlyings or set()) else 0
        plan = risk_plan(
            target,
            cash,
            config,
            open_premium=open_premium,
            open_positions=len(active_symbols),
            same_underlying_positions=same_underlying,
            sector_premium=sector_premium,
            open_planned_risk=open_planned_risk,
        )
        quantity, max_entry = plan.quantity, plan.max_entry
        if quantity >= 1:
            risk_passed += 1
        target_scenarios = scenarios_for(target, underlying, config, as_of)
        recommendation = build_recommendation(
            recommendation_id=str(uuid4()),
            as_of=as_of,
            underlying=underlying,
            option=target,
            config=config,
            events=events,
            news_items=symbol_news,
            news_ok=confirmed,
            scenarios=target_scenarios,
            quantity=quantity,
            max_entry=max_entry,
            model=model,
            shortlist_symbols={item.option_symbol for item in shortlist},
            budget_blocked=budget_blocked,
        )
        if quantity < 1:
            recommendation.suppress_reason = {
                "sector": "SECTOR_EXPOSURE_LIMIT",
                "exposure": "EXPOSURE_LIMIT",
                "aggregate_risk": "AGGREGATE_RISK_LIMIT",
                "positions": "UNDERLYING_POSITION_LIMIT" if same_underlying else "MAX_POSITIONS",
                "buying_power": "BUYING_POWER",
                "capital": "CAPITAL_LIMIT",
                "hard_cap": "HARD_CAP",
                "risk": "RISK_BUDGET",
            }.get(plan.limiter, recommendation.suppress_reason)
        recommendation.policy_trace = {
            "risk_policy_version": plan.risk_policy_version,
            "exit_policy_version": plan.exit_policy_version,
            "risk_budget": str(plan.risk_budget),
            "contract_cost": str(plan.contract_cost),
            "risk_per_contract": str(plan.risk_per_contract),
            "by_risk": plan.by_risk,
            "by_capital": plan.by_capital,
            "by_exposure": plan.by_exposure,
            "by_sector": plan.by_sector,
            "by_aggregate": plan.by_aggregate,
            "by_buying_power": plan.by_buying_power,
            "quantity": plan.quantity,
            "limiter": plan.limiter,
        }
        loss = None
        if enforce_loss:
            loss = "DAILY_LOSS_LIMIT_REACHED" if daily_pnl is None else daily_loss_reason(cash, daily_pnl, config)
        elif daily_pnl is not None:
            loss = daily_loss_reason(cash, daily_pnl, config)
        if loss:
            recommendation.decision = DecisionKind.SUPPRESS
            recommendation.suppress_reason = loss
        if enforce_exposure and sector_book is None and recommendation.decision is DecisionKind.BUY:
            recommendation.decision = DecisionKind.SUPPRESS
            recommendation.suppress_reason = "SECTOR_EXPOSURE_LIMIT"
        elif sector_book is not None and recommendation.decision is DecisionKind.BUY:
            sector = (sector_of or {}).get(underlying.symbol, "UNCLASSIFIED")
            added = target.ask * Decimal(recommendation.quantity) * Decimal(config.contract_multiplier)
            sector_reason = sector_exposure_reason(cash, sector, sector_book, added, config)
            if sector_reason:
                recommendation.decision = DecisionKind.SUPPRESS
                recommendation.suppress_reason = sector_reason
        if regime is not None and recommendation.decision is DecisionKind.BUY:
            action, why = regime_trade_policy(regime, recommendation.call_put.value)
            if action == "SUPPRESS":
                recommendation.decision = DecisionKind.SUPPRESS
                recommendation.suppress_reason = why
        if recommendation.decision is DecisionKind.BUY:
            if buys >= config.max_buys_per_scan:
                recommendation.decision = DecisionKind.SUPPRESS
                recommendation.suppress_reason = "scan_cap"
            else:
                buys += 1
                ledger.buy_count += 1
                active_symbols.add(recommendation.option_symbol)
        results.append(recommendation)
    if counts is not None:
        counts.update(
            {
                "contracts_checked": contracts_checked,
                "contracts_passed": contracts_passed,
                "news_passed": news_passed,
                "risk_checked": risk_checked,
                "risk_passed": risk_passed,
                "ai_calls": ai_calls,
                "buys": buys,
                "decisions": len(results),
            }
        )
    return results
