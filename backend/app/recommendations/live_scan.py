from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from app.ai.budget import BudgetLedger
from app.ai.cache import load_cached_model
from app.config.settings import TradingConfig
from app.market.regime import regime_from_context
from app.models.db import database_ready, session_scope
from app.models.tables import Candidate
from app.providers.base import ProviderNotConfigured, ProviderUnavailable
from app.providers.registry import ProviderSet
from app.quant.filters import quote_age_seconds, rank_underlyings, reject_underlying, would_fail_old_volume_floor
from app.recommendations.pipeline import Analyzer, run_scan
from app.schemas.domain import Recommendation


class MarketDataMissing(Exception):
    def __init__(self, variable: str, news_count: int = 0) -> None:
        super().__init__(variable)
        self.variable = variable
        self.news_count = news_count


class ScanResult:
    def __init__(
        self,
        recommendations: list[Recommendation],
        news_count: int,
        underlyings: list | None = None,
        universe: dict | None = None,
        rejections: list | None = None,
        option_symbols: int = 0,
        ai_calls: int = 0,
        scan_id: str = "",
    ) -> None:
        self.recommendations = recommendations
        self.news_count = news_count
        self.underlyings = underlyings or []
        self.universe = universe or {}
        self.rejections = rejections or []
        self.option_symbols = option_symbols
        self.ai_calls = ai_calls
        self.scan_id = scan_id
        self.stages: dict = {}
        self.filter_profile: dict = {}


def execute_scan(
    providers: ProviderSet,
    config: TradingConfig,
    analyzer: Analyzer | None,
    ledger: BudgetLedger,
    now: datetime,
    session_date: date,
    cash: Decimal,
    active_symbols: set[str],
    daily_pnl: Decimal | None = None,
    sector_book: dict[str, Decimal] | None = None,
    enforce_loss: bool = False,
    enforce_exposure: bool = False,
    open_underlyings: set[str] | None = None,
    open_planned_risk: Decimal = Decimal("0"),
) -> ScanResult:
    """Load news when Benzinga is configured, then run the decision pipeline.

    Market and options data are still required before run_scan. A configured
    news provider is fetched even when those providers are missing, so the
    news step is not what stops the scan.
    """
    missing_market: str | None = None
    underlyings = []
    options = []
    scan_id = str(uuid4())
    universe = _bind_dynamic_universe(providers, config, now)
    try:
        underlyings = list(providers.market.load_underlyings(now))
    except ProviderNotConfigured as exc:
        missing_market = exc.variable
    if providers.market.name == "thetadata" and missing_market is None and not underlyings:
        raise ProviderUnavailable("אין ציטוט או שרשרת אופציות")
    kept, rejections = _liquidity_stage(underlyings, providers, config, now)
    requested = tuple(getattr(getattr(providers.market, "feed", None), "_symbols", ()) or ())
    filter_profile = _filter_observation(requested, underlyings, config, now)
    if missing_market is None and kept:
        if hasattr(providers.options, "bind_option_symbols"):
            providers.options.bind_option_symbols(tuple(item.symbol for item in kept))
        try:
            options = list(providers.options.load_options(now))
        except ProviderNotConfigured as exc:
            missing_market = exc.variable

    if providers.news.name == "unconfigured":
        if missing_market:
            raise MarketDataMissing(missing_market)
        news = []
    else:
        symbols = tuple(dict.fromkeys(item.symbol.strip().upper() for item in kept if item.symbol))
        try:
            news = providers.news.load_news(now, symbols or None)
        except ProviderNotConfigured as exc:
            raise MarketDataMissing(exc.variable) from exc
        if missing_market:
            raise MarketDataMissing(missing_market, news_count=len(news))

    macro = None
    if providers.macro is not None:
        try:
            macro = providers.macro.load(now)
        except ProviderUnavailable:
            macro = None
    context = getattr(providers.market, "context_quotes", None)
    quotes = context() if callable(context) else None
    regime = regime_from_context(quotes, macro)
    research = None
    if providers.research is not None and kept:
        try:
            research = providers.research.load(now, tuple(item.symbol for item in kept))
        except ProviderUnavailable:
            research = None
    def _reuse(digest: str, moment: datetime):
        if not database_ready():
            return None
        with session_scope() as session:
            return load_cached_model(session, digest, moment, config.ai_decision_ttl_seconds)

    counter = _CountingAnalyzer(analyzer)

    def _acquire(digest: str, moment: datetime) -> bool:
        if not database_ready():
            return True
        from app.ai.lock import claim_fingerprint

        with session_scope() as session:
            return claim_fingerprint(session, digest, moment, config.ai_decision_ttl_seconds)

    def _record(model, digest: str, symbol: str) -> None:
        if not database_ready() or not model.telemetry:
            return
        from app.ai.telemetry import store_ai_request

        row = dict(model.telemetry)
        row["scan_id"] = scan_id
        row["candidate_id"] = symbol
        row["candidate_fingerprint"] = digest
        with session_scope() as session:
            store_ai_request(session, row, now)
            session.commit()

    counts: dict = {}
    rows = run_scan(
        kept,
        options,
        news,
        config,
        None if analyzer is None else counter,
        ledger,
        now,
        session_date,
        cash,
        active_symbols,
        regime,
        macro,
        research,
        _reuse,
        _acquire,
        daily_pnl,
        sector_book,
        record_ai=_record,
        enforce_loss=enforce_loss,
        enforce_exposure=enforce_exposure,
        counts=counts,
        open_underlyings=open_underlyings,
        open_planned_risk=open_planned_risk,
    )
    _store_rejections(scan_id, now, rejections)
    result = ScanResult(rows, len(news), kept, _universe_view(universe), rejections, len({item.symbol for item in kept}), counter.calls, scan_id)
    result.filter_profile = filter_profile
    _remember_scan_liquidity(underlyings)
    _store_rv_audit(scan_id, now, filter_profile.get("samples") or [])
    book = universe
    result.stages = {
        "universe_checked": None if book is None else getattr(book, "discovered", None),
        "universe_passed": None if book is None else getattr(book, "filtered", None),
        "underlyings_checked": len(underlyings),
        "underlyings_passed": len(kept),
        "chains_requested": len(kept),
        "old_volume_floor": config.min_underlying_volume,
        "eligible_below_old_volume_floor": sum(1 for item in kept if would_fail_old_volume_floor(item, config)),
        "contracts_checked": counts.get("contracts_checked", 0),
        "contracts_passed": counts.get("contracts_passed", 0),
        "news_checked": len(news),
        "news_passed": counts.get("news_passed", 0),
        "risk_checked": counts.get("risk_checked", 0),
        "risk_passed": counts.get("risk_passed", 0),
        "ai_calls": counter.calls,
        "buys": counts.get("buys", 0),
        "decisions": len(rows),
        "rejections": len(rejections),
        "orders": 0,
        "fills": 0,
        "exits": 0,
        "option_status": getattr(getattr(providers.options, "feed", None), "last_option_status", None),
    }
    return result


class _CountingAnalyzer:
    def __init__(self, inner: Analyzer | None) -> None:
        self.inner = inner
        self.calls = 0

    def analyze(self, packet, as_of):
        self.calls += 1
        if self.inner is None:
            return None
        return self.inner.analyze(packet, as_of)


def _bind_dynamic_universe(providers: ProviderSet, config: TradingConfig, now: datetime):
    feed = getattr(providers.market, "feed", None)
    if feed is None or providers.market.name != "thetadata" or getattr(feed, "_symbols", ()):
        return getattr(feed, "universe_book", None)
    from app.universe.builder import UniverseUnavailable, next_scan_symbols

    try:
        book, batch = next_scan_symbols(feed.settings, config, now)
    except UniverseUnavailable as exc:
        raise ProviderUnavailable(str(exc)) from exc
    feed.bind_symbols(batch)
    feed.universe_book = book
    return book


def _filter_observation(requested: tuple[str, ...], underlyings, config: TradingConfig, now: datetime) -> dict:
    """Record why each requested symbol stopped, without changing the filter decision."""
    by_symbol = {item.symbol: item for item in underlyings}
    floor = Decimal(str(config.universe_min_price))
    counts = {
        "symbols_requested": len(requested),
        "quotes_received": 0,
        "quotes_fresh": 0,
        "rejected_no_quote": 0,
        "rejected_stale": 0,
        "rejected_price": 0,
        "rejected_volume": 0,
        "rejected_relative_volume": 0,
        "rejected_move": 0,
        "rejected_session": 0,
        "rejected_future": 0,
        "passed_underlying": 0,
    }
    samples = []
    symbols = requested or tuple(item.symbol for item in underlyings)
    for symbol in symbols:
        item = by_symbol.get(symbol)
        if item is None:
            counts["rejected_no_quote"] += 1
            continue
        counts["quotes_received"] += 1
        age = quote_age_seconds(item.observed_at, now)
        if 0 <= age <= config.min_data_freshness_seconds:
            counts["quotes_fresh"] += 1
        reason = reject_underlying(item, config, now)
        if reason is None and item.price < floor:
            reason = "min_price"
        if reason == "underlying_liquidity" and item.price <= 0:
            bucket = "rejected_price"
        else:
            bucket = {
                "stale_underlying": "rejected_stale",
                "min_price": "rejected_price",
                "underlying_liquidity": "rejected_volume",
                "relative_volume": "rejected_relative_volume",
                "underlying_move": "rejected_move",
                "session_closed": "rejected_session",
                "future_quote": "rejected_future",
            }.get(reason or "", "passed_underlying")
        counts[bucket] += 1
        samples.append(
            {
                "symbol": item.symbol,
                "price": float(item.price),
                "volume": int(item.volume),
                "relative_volume": float(item.relative_volume),
                "move_percent": float(item.change_percent),
                "quote_age_seconds": round(age, 3),
                "reason": reason or "passed",
                "rv_method": getattr(item, "rv_method", "") or "",
                "rv_numerator": int(getattr(item, "rv_numerator", 0) or 0),
                "rv_denominator": float(getattr(item, "rv_denominator", 0) or 0),
                "rv_day_count": int(getattr(item, "rv_day_count", 0) or 0),
                "rv_cutoff": None if getattr(item, "rv_cutoff", None) is None else item.rv_cutoff.isoformat(),
                "rv_prior_days": list(getattr(item, "rv_prior_days", ()) or ()),
                "rv_prior_totals": list(getattr(item, "rv_prior_totals", ()) or ()),
                "prior_day_volume": int(getattr(item, "prior_day_volume", 0) or 0),
                "would_fail_old_volume_floor": would_fail_old_volume_floor(item, config),
                "rv_high_priority": float(item.relative_volume) >= config.relative_volume_priority,
            }
        )
    return {"counts": counts, "samples": samples}


def _liquidity_stage(underlyings, providers, config: TradingConfig, now: datetime):
    requested = tuple(getattr(getattr(providers.market, "feed", None), "_symbols", ()) or ())
    returned = {item.symbol for item in underlyings}
    rejections = [(symbol, "no_quote", "liquidity") for symbol in requested if symbol not in returned]
    kept = []
    floor = Decimal(str(config.universe_min_price))
    for item in underlyings:
        reason = reject_underlying(item, config, now)
        if reason:
            rejections.append((item.symbol, reason, "liquidity"))
        elif item.price < floor:
            rejections.append((item.symbol, "min_price", "liquidity"))
        else:
            kept.append(item)
    return rank_underlyings(kept, now, config.relative_volume_priority), rejections


def _remember_scan_liquidity(underlyings) -> None:
    scores = {}
    for item in underlyings:
        volume = int(getattr(item, "prior_day_volume", 0) or 0)
        symbol = str(getattr(item, "symbol", "") or "").strip().upper()
        if symbol and volume > 0:
            scores[symbol] = volume
    if not scores:
        return
    try:
        from app.universe.builder import remember_liquidity

        remember_liquidity(scores)
    except (OSError, TypeError, ValueError):
        return


def _store_rv_audit(scan_id: str, now: datetime, samples: list[dict]) -> None:
    if not samples or not database_ready():
        return
    from sqlalchemy.exc import SQLAlchemyError

    from app.models.tables import AuditLog

    try:
        with session_scope() as session:
            for sample in samples:
                session.add(
                    AuditLog(
                        actor="scanner",
                        action="relative_volume",
                        entity="underlying",
                        entity_id=str(sample.get("symbol") or "")[:36],
                        payload={
                            "scan_id": scan_id,
                            "method": sample.get("rv_method") or "",
                            "numerator": sample.get("rv_numerator"),
                            "denominator": sample.get("rv_denominator"),
                            "day_count": sample.get("rv_day_count"),
                            "cutoff": sample.get("rv_cutoff"),
                            "as_of": now.isoformat(),
                            "prior_days": sample.get("rv_prior_days") or [],
                            "prior_totals": sample.get("rv_prior_totals") or [],
                            "prior_day_volume": sample.get("prior_day_volume"),
                            "relative_volume": sample.get("relative_volume"),
                        },
                        created_at=now,
                    )
                )
            session.commit()
    except SQLAlchemyError:
        return


def _store_rejections(scan_id: str, now: datetime, rejections: list[tuple[str, str, str]]) -> None:
    if not rejections or not database_ready():
        return
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with session_scope() as session:
            for symbol, reason, stage in rejections:
                session.add(
                    Candidate(
                        scan_id=scan_id,
                        underlying=symbol,
                        status="rejected",
                        reasons={"reason": reason, "stage": stage},
                        observed_at=now,
                    )
                )
            session.commit()
    except SQLAlchemyError:
        return


def _universe_view(book) -> dict:
    if book is None:
        return {}
    return {
        "source": book.source,
        "status": book.status,
        "discovered": book.discovered,
        "filtered": book.filtered,
        "detail": book.detail,
    }
