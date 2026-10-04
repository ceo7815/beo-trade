"""Open position memory from a real entry fill. A quote is not an entry."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.normalize import parse_option_symbol
from app.config.settings import TradingConfig
from app.models.tables import AIAnalysis, BrokerOrderRecord, RecommendationRow
from app.positions.state import open_state


def _when(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _decimal(raw: object) -> Decimal | None:
    if raw in {None, ""}:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def capture_entry_fill(session: Session, fill: dict, config: TradingConfig) -> None:
    side = str(fill.get("side") or fill.get("position_intent") or "").lower()
    if side not in {"buy", "buy_to_open"}:
        return
    price = _decimal(fill.get("price") or fill.get("average_price"))
    quantity_raw = fill.get("cumulative_qty") or fill.get("qty")
    quantity = _decimal(quantity_raw)
    entry_time = _when(fill.get("timestamp"))
    symbol = str(fill.get("symbol") or "").replace(" ", "")
    if price is None or price <= 0 or quantity is None or quantity <= 0 or entry_time is None or not symbol:
        return
    contract = parse_option_symbol(symbol)
    underlying = "" if contract is None else str(contract["underlying"])
    expiration = "" if contract is None else str(contract["expiration"])
    direction = "" if contract is None else str(contract["right"])
    order = None
    broker_order_id = str(fill.get("broker_order_id") or "")
    if broker_order_id:
        order = session.scalar(select(BrokerOrderRecord).where(BrokerOrderRecord.broker_order_id == broker_order_id).limit(1))
    recommendation = None
    if order is not None and order.recommendation_id:
        recommendation = session.get(RecommendationRow, order.recommendation_id)
    trade_id = str(fill.get("trade_id") or symbol)
    versions = _policy_versions(session, recommendation, config)
    dte = 0
    if expiration:
        try:
            dte = (datetime.fromisoformat(expiration).date() - entry_time.date()).days
        except ValueError:
            dte = 0
    entry_underlying = None if recommendation is None else Decimal(str(recommendation.underlying_price))
    entry_delta = None if recommendation is None else Decimal(str(recommendation.delta))
    entry_iv = None if recommendation is None else Decimal(str(recommendation.iv))
    if recommendation is not None and recommendation.underlying:
        underlying = recommendation.underlying
    row = open_state(
        session,
        trade_id=trade_id,
        symbol=symbol,
        underlying=underlying,
        entry_price=price,
        quantity=int(quantity),
        entry_time=entry_time,
        config=config,
        entry_underlying=entry_underlying,
        entry_delta=entry_delta,
        entry_iv=entry_iv,
        expiration=expiration,
        dte_at_entry=dte,
        direction=direction or ("" if recommendation is None else recommendation.call_put),
        thesis="" if recommendation is None else recommendation.thesis,
        invalidation="" if recommendation is None else recommendation.invalidation,
    )
    row.risk_policy_version = versions[0]
    row.exit_policy_version = versions[1]


def _policy_versions(session: Session, recommendation: RecommendationRow | None, config: TradingConfig) -> tuple[str, str]:
    if recommendation is None or not recommendation.analysis_id:
        return config.risk_policy_version, config.exit_policy_version
    analysis = session.get(AIAnalysis, recommendation.analysis_id)
    snapshot = analysis.snapshot if analysis is not None and isinstance(analysis.snapshot, dict) else {}
    policy = snapshot.get("policy") if isinstance(snapshot.get("policy"), dict) else {}
    return (
        str(policy.get("risk_policy_version") or config.risk_policy_version),
        str(policy.get("exit_policy_version") or config.exit_policy_version),
    )
