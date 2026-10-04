from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    paper_cash: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    paper_starting_cash: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Underlying(Base):
    __tablename__ = "underlyings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OptionContract(Base):
    __tablename__ = "option_contracts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    underlying_id: Mapped[str] = mapped_column(ForeignKey("underlyings.id"), nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    call_put: Mapped[str] = mapped_column(String(4), nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    expiration: Mapped[str] = mapped_column(String(10), nullable=False)
    multiplier: Mapped[int] = mapped_column(nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OptionQuote(Base):
    __tablename__ = "option_quotes"
    __table_args__ = (Index("ix_option_quotes_contract_time", "contract_id", "observed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("option_contracts.id"), nullable=False)
    bid: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    ask: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    last: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    bid_size: Mapped[int] = mapped_column(default=0)
    ask_size: Mapped[int] = mapped_column(default=0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OptionTrade(Base):
    __tablename__ = "option_trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("option_contracts.id"), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    size: Mapped[int] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OptionGreek(Base):
    __tablename__ = "option_greeks"
    __table_args__ = (Index("ix_option_greeks_contract_time", "contract_id", "observed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("option_contracts.id"), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    gamma: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    theta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    vega: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    implied_volatility: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OptionSnapshotRow(Base):
    __tablename__ = "option_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    contract_id: Mapped[str] = mapped_column(ForeignKey("option_contracts.id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_underlying_time", "underlying_id", "observed_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    underlying_id: Mapped[str] = mapped_column(ForeignKey("underlyings.id"), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    open: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)
    relative_volume: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class VolatilitySnapshot(Base):
    __tablename__ = "volatility_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    underlying_id: Mapped[str] = mapped_column(ForeignKey("underlyings.id"), nullable=False)
    implied_volatility: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    historical_volatility: Mapped[float | None] = mapped_column(Numeric(18, 6))
    iv_rank: Mapped[float | None] = mapped_column(Numeric(18, 6))
    iv_percentile: Mapped[float | None] = mapped_column(Numeric(18, 6))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class NewsArticle(Base):
    __tablename__ = "news_articles"
    __table_args__ = (UniqueConstraint("url"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False)
    category: Mapped[str] = mapped_column(String(80), default="")
    event_type: Mapped[str] = mapped_column(String(80), default="")
    importance: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    source_credibility: Mapped[float] = mapped_column(Numeric(8, 4), default=0)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class NewsEvent(Base):
    __tablename__ = "news_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    article_id: Mapped[str] = mapped_column(ForeignKey("news_articles.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False)
    importance: Mapped[int] = mapped_column(default=0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class NewsEmbedding(Base):
    __tablename__ = "news_embeddings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    article_id: Mapped[str] = mapped_column(ForeignKey("news_articles.id"), unique=True, nullable=False)
    embedding: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    scan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), default="")
    score: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict] = mapped_column(JSON, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    input_tokens: Mapped[int] = mapped_column(default=0)
    cached_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RecommendationRow(Base):
    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_decision_time", "decision", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    analysis_id: Mapped[str | None] = mapped_column(ForeignKey("ai_analyses.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    underlying_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    call_put: Mapped[str] = mapped_column(String(4), nullable=False)
    strike: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    expiration: Mapped[str] = mapped_column(String(10), nullable=False)
    option_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    bid: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    ask: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    gamma: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    theta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    vega: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    iv: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(nullable=False)
    open_interest: Mapped[int] = mapped_column(nullable=False)
    max_entry_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    holding_window_min: Mapped[int] = mapped_column(nullable=False)
    holding_window_max: Mapped[int] = mapped_column(nullable=False)
    thesis: Mapped[str] = mapped_column(Text, default="")
    catalyst: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(Text, default="")
    invalidation: Mapped[str] = mapped_column(Text, default="")
    reason_codes: Mapped[list] = mapped_column(JSON, nullable=False)
    gate_results: Mapped[dict] = mapped_column(JSON, nullable=False)
    suppress_reason: Mapped[str] = mapped_column(String(80), default="")
    scenarios: Mapped[list] = mapped_column(JSON, nullable=False)
    quote_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str] = mapped_column(String(40), default="")
    pnl_dollars: Mapped[float | None] = mapped_column(Numeric(18, 6))
    pnl_percent: Mapped[float | None] = mapped_column(Numeric(18, 6))
    max_favorable: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    max_adverse: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    bid: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    ask: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    iv: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    delta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    gamma: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    theta: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    vega: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    underlying_at_entry: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PaperPosition(Base):
    __tablename__ = "paper_positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    trade_id: Mapped[str] = mapped_column(ForeignKey("paper_trades.id"), nullable=False)
    option_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradeOutcome(Base):
    __tablename__ = "trade_outcomes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    trade_id: Mapped[str] = mapped_column(ForeignKey("paper_trades.id"), nullable=False)
    horizon_minutes: Mapped[int] = mapped_column(nullable=False)
    return_percent: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    max_gain_percent: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    max_loss_percent: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    time_to_peak_seconds: Mapped[int] = mapped_column(nullable=False)
    time_to_drawdown_seconds: Mapped[int] = mapped_column(nullable=False)
    success: Mapped[bool] = mapped_column(nullable=False)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    params: Mapped[dict] = mapped_column(JSON, nullable=False)
    leakage_checks: Mapped[dict] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BacktestResult(Base):
    __tablename__ = "backtest_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("backtest_runs.id"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_created", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    process: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False)
    cached_tokens: Mapped[int] = mapped_column(nullable=False)
    output_tokens: Mapped[int] = mapped_column(nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WatchItem(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    recommendation_id: Mapped[str | None] = mapped_column(ForeignKey("recommendations.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationCheck(Base):
    __tablename__ = "integration_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    integration_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column()
    detail: Mapped[str] = mapped_column(Text, default="")
    environment: Mapped[str] = mapped_column(String(24), default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class IntegrationEvent(Base):
    __tablename__ = "integration_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    integration_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BrokerOrderRecord(Base):
    __tablename__ = "broker_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    broker: Mapped[str] = mapped_column(String(24), nullable=False, default="alpaca")
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    broker_order_id: Mapped[str] = mapped_column(String(64), default="")
    client_order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    position_intent: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    recommendation_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class BrokerAccountSnapshot(Base):
    __tablename__ = "broker_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="alpaca")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerPositionSnapshot(Base):
    __tablename__ = "broker_positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerOrderSnapshot(Base):
    __tablename__ = "broker_order_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    broker_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    trade_id: Mapped[str] = mapped_column(String(36), default="")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    internal_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerFillRecord(Base):
    __tablename__ = "broker_fills"
    __table_args__ = (UniqueConstraint("execution_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    execution_id: Mapped[str] = mapped_column(String(80), nullable=False)
    trade_id: Mapped[str] = mapped_column(String(36), default="")
    broker_order_id: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerActivityRecord(Base):
    __tablename__ = "broker_activities"
    __table_args__ = (UniqueConstraint("activity_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    activity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionState(Base):
    """Authoritative open-position memory. A quote refresh must not replace entry facts."""

    __tablename__ = "position_states"
    trade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    entry_fill_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_fill_quantity: Mapped[int] = mapped_column(nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_underlying_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    entry_delta: Mapped[float | None] = mapped_column(Numeric(18, 6))
    entry_iv: Mapped[float | None] = mapped_column(Numeric(18, 6))
    entry_option_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    peak_option_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    peak_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    planned_risk: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    one_r_amount: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    initial_stop_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    protected_stop_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    target_1: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    target_2: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    trailing_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    trailing_peak: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    trailing_trigger: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    expiration: Mapped[str] = mapped_column(String(10), default="")
    dte_at_entry: Mapped[int] = mapped_column(nullable=False, default=0)
    underlying_direction: Mapped[str] = mapped_column(String(8), default="")
    thesis: Mapped[str] = mapped_column(Text, default="")
    invalidation: Mapped[str] = mapped_column(Text, default="")
    risk_policy_version: Mapped[str] = mapped_column(String(40), default="")
    exit_policy_version: Mapped[str] = mapped_column(String(40), default="")
    protected_mode: Mapped[bool] = mapped_column(nullable=False, default=False)
    winner_run_mode: Mapped[bool] = mapped_column(nullable=False, default=False)
    current_option_bid: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_option_ask: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_underlying_price: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_iv: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_spread: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 6))
    current_holding_minutes: Mapped[float | None] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class BrokerReconciliation(Base):
    __tablename__ = "broker_reconciliations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    environment: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    ok: Mapped[bool] = mapped_column(nullable=False)
    mismatches: Mapped[list] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerTrade(Base):
    __tablename__ = "broker_trades"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), default="")
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ApprovalRecord(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("recommendation_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    trade_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    recommendation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrokerTradeEvent(Base):
    __tablename__ = "broker_trade_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    trade_id: Mapped[str] = mapped_column(ForeignKey("broker_trades.id"), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AIFingerprintLock(Base):
    __tablename__ = "ai_fingerprint_locks"
    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIRequestLog(Base):
    __tablename__ = "ai_request_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    request_id: Mapped[str | None] = mapped_column(String(80))
    scan_id: Mapped[str | None] = mapped_column(String(36))
    candidate_id: Mapped[str | None] = mapped_column(String(64))
    candidate_fingerprint: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None] = mapped_column()
    cached_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    reasoning_tokens: Mapped[int | None] = mapped_column()
    total_tokens: Mapped[int | None] = mapped_column()
    latency_ms: Mapped[int | None] = mapped_column()
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(18, 6))
    cache_hit: Mapped[bool | None] = mapped_column()
    decision: Mapped[str | None] = mapped_column(String(16))
    retry_count: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
