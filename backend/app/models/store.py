from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.budget import UsageEntry
from app.ai.prompts import PROMPT_VERSION
from app.models.tables import (
    AIAnalysis,
    AIRequestLog,
    AIUsage,
    Alert,
    AuditLog,
    PaperPosition,
    PaperTrade,
    RecommendationRow,
    User,
    WatchItem,
)
from app.paper_trading.engine import pnl
from app.schemas.domain import REASON_HEBREW, DecisionKind, Recommendation


def _num(value) -> float:
    return float(value)


def save_recommendation(session: Session, item: Recommendation, user_id: str) -> None:
    if session.get(RecommendationRow, item.recommendation_id) is not None:
        return
    analysis_id = None
    if item.audit_hash:
        analysis = AIAnalysis(
            prompt_version=item.prompt_version or PROMPT_VERSION,
            model=item.model or "",
            input_hash=item.audit_hash,
            output={
                "decision": item.decision.value,
                "model_decision": item.model_decision or item.decision.value,
                "thesis": item.thesis,
                "catalyst": item.catalyst,
                "risk": item.risk,
                "invalidation": item.invalidation,
                "reason_codes": item.reason_codes,
                "data_quality": "PASS",
            },
            decision=item.decision.value,
            snapshot={
                "option_symbol": item.option_symbol,
                "call_put": item.call_put.value,
                "gates": item.gate_results,
                "input_tokens": item.input_tokens,
                "cached_tokens": item.cached_tokens,
                "output_tokens": item.output_tokens,
                "reasoning_tokens": item.reasoning_tokens,
                "estimated_cost": str(item.estimated_cost),
                "prompt_version": item.prompt_version,
                "model": item.model,
                "policy": getattr(item, "policy_trace", {}) or {},
            },
            cost=item.estimated_cost,
            input_tokens=item.input_tokens,
            cached_tokens=item.cached_tokens,
            output_tokens=item.output_tokens,
            created_at=item.timestamp,
        )
        session.add(analysis)
        session.flush()
        analysis_id = analysis.id
    session.add(
        RecommendationRow(
            id=item.recommendation_id,
            analysis_id=analysis_id,
            user_id=user_id,
            decision=item.decision.value,
            underlying=item.underlying,
            underlying_price=_num(item.underlying_price),
            option_symbol=item.option_symbol,
            call_put=item.call_put.value,
            strike=_num(item.strike),
            expiration=item.expiration.isoformat(),
            option_price=_num(item.option_price),
            bid=_num(item.bid),
            ask=_num(item.ask),
            delta=_num(item.delta),
            gamma=_num(item.gamma),
            theta=_num(item.theta),
            vega=_num(item.vega),
            iv=_num(item.iv),
            volume=item.volume,
            open_interest=item.open_interest,
            max_entry_price=_num(item.max_entry_price),
            quantity=item.quantity,
            holding_window_min=item.holding_window_min,
            holding_window_max=item.holding_window_max,
            thesis=item.thesis,
            catalyst=item.catalyst,
            risk=item.risk,
            invalidation=item.invalidation,
            reason_codes=item.reason_codes,
            gate_results=item.gate_results,
            suppress_reason=item.suppress_reason,
            scenarios=[
                {
                    "move_percent": point.move_percent,
                    "underlying_price": _num(point.underlying_price),
                    "option_price": _num(point.option_price),
                    "change_dollars": _num(point.change_dollars),
                    "change_percent": _num(point.change_percent),
                }
                for point in item.scenarios
            ],
            quote_observed_at=item.timestamp,
            created_at=item.timestamp,
        )
    )
    session.add(
        AuditLog(
            actor="decision-engine",
            action=item.decision.value,
            entity="recommendations",
            entity_id=item.recommendation_id,
            payload={
                "option_symbol": item.option_symbol,
                "prompt_version": item.prompt_version,
                "input_hash": item.audit_hash,
                "gates": item.gate_results,
                "suppress_reason": item.suppress_reason,
            },
            created_at=item.timestamp,
        )
    )
    if item.decision is DecisionKind.BUY:
        why = " · ".join(REASON_HEBREW.get(code, code) for code in item.reason_codes)
        session.add(
            Alert(
                user_id=user_id,
                recommendation_id=item.recommendation_id,
                kind="BUY",
                message=f"BUY {item.underlying} {item.call_put.value} {item.strike} עד ${item.max_entry_price} — {why}",
                created_at=item.timestamp,
            )
        )


def list_decisions(session: Session, limit: int = 100) -> list[RecommendationRow]:
    return list(session.scalars(select(RecommendationRow).order_by(RecommendationRow.created_at.desc()).limit(limit)))


def list_buys(session: Session) -> list[RecommendationRow]:
    return list(
        session.scalars(
            select(RecommendationRow)
            .where(RecommendationRow.decision == "BUY")
            .order_by(RecommendationRow.created_at.desc())
        )
    )


def get_buy(session: Session, recommendation_id: str) -> RecommendationRow | None:
    row = session.get(RecommendationRow, recommendation_id)
    if row is None or row.decision != "BUY":
        return None
    return row


def active_option_symbols(session: Session) -> set[str]:
    rows = session.scalars(select(PaperPosition.option_symbol).where(PaperPosition.status == "open"))
    return set(rows)


def open_positions(session: Session) -> list[PaperPosition]:
    return list(session.scalars(select(PaperPosition).where(PaperPosition.status == "open")))


def count_internal_candidates(session: Session) -> int:
    return len(list(session.scalars(select(RecommendationRow.id))))


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def load_usage(session: Session, since: datetime, cost_of: Callable[[int, int, int], Decimal] | None = None) -> list[UsageEntry]:
    """Every model call the worker logged, priced from its own estimate or from its tokens."""
    entries = [
        UsageEntry(
            process=row.process,
            created_at=_utc(row.created_at),
            cost=Decimal(str(row.cost)),
            input_tokens=row.input_tokens,
            cached_tokens=row.cached_tokens,
            output_tokens=row.output_tokens,
            model=row.model,
        )
        for row in session.scalars(select(AIUsage).where(AIUsage.created_at >= since))
    ]
    for row in session.scalars(select(AIRequestLog).where(AIRequestLog.created_at >= since)):
        fresh = row.input_tokens or 0
        cached = row.cached_tokens or 0
        output = row.output_tokens or 0
        if row.estimated_cost is not None:
            cost = Decimal(str(row.estimated_cost))
        elif cost_of is not None and (fresh or output):
            cost = cost_of(fresh, cached, output)
        else:
            cost = Decimal("0")
        entries.append(
            UsageEntry(
                process="decision",
                created_at=_utc(row.created_at),
                cost=cost,
                input_tokens=fresh,
                cached_tokens=cached,
                output_tokens=output,
                model=row.model or "",
            )
        )
    return entries


def save_usage(session: Session, entry: UsageEntry) -> None:
    session.add(
        AIUsage(
            process=entry.process,
            model=entry.model,
            input_tokens=entry.input_tokens,
            cached_tokens=entry.cached_tokens,
            output_tokens=entry.output_tokens,
            cost=entry.cost,
            created_at=entry.created_at,
        )
    )


def get_user(session: Session, user_id: str) -> User | None:
    return session.get(User, user_id)


def paper_equity(session: Session, user_id: str) -> Decimal:
    user = session.get(User, user_id)
    if user is None:
        return Decimal("0")
    cash = Decimal(str(user.paper_cash))
    positions = session.scalars(
        select(PaperTrade)
        .join(PaperPosition, PaperPosition.trade_id == PaperTrade.id)
        .where(PaperPosition.status == "open", PaperTrade.user_id == user_id)
    )
    marked = cash
    for trade in positions:
        marked += Decimal(str(trade.entry_price)) * Decimal(trade.quantity) * Decimal(100)
    return marked.quantize(Decimal("0.01"))


def create_paper_trade(session: Session, user_id: str, recommendation_id: str, multiplier: int) -> PaperTrade:
    from app.paper_trading.engine import open_paper
    from app.config.settings import get_settings

    row = get_buy(session, recommendation_id)
    if row is None:
        raise LookupError("buy")
    user = session.get(User, user_id)
    if user is None:
        raise LookupError("user")
    existing = session.scalar(select(PaperTrade).where(PaperTrade.recommendation_id == recommendation_id))
    if existing is not None:
        raise ValueError("already_open")
    recommendation = _row_to_recommendation(row)
    fill = open_paper(recommendation, get_settings().trading(), datetime.now(timezone.utc))
    if fill is None:
        raise ValueError("entry_rejected")
    cost = Decimal(str(fill.entry_price)) * Decimal(fill.quantity) * Decimal(multiplier)
    if Decimal(str(user.paper_cash)) < cost:
        raise ValueError("insufficient_cash")
    user.paper_cash = Decimal(str(user.paper_cash)) - cost
    trade = PaperTrade(
        id=fill.trade_id,
        user_id=user_id,
        recommendation_id=recommendation_id,
        option_symbol=fill.option_symbol,
        quantity=fill.quantity,
        entry_price=fill.entry_price,
        entry_at=fill.entry_at,
        bid=fill.bid_at_entry,
        ask=fill.ask_at_entry,
        iv=fill.iv_at_entry,
        delta=fill.delta_at_entry,
        gamma=fill.gamma_at_entry,
        theta=fill.theta_at_entry,
        vega=fill.vega_at_entry,
        underlying_at_entry=fill.underlying_at_entry,
        max_favorable=0,
        max_adverse=0,
    )
    session.add(trade)
    session.add(
        PaperPosition(
            user_id=user_id,
            trade_id=trade.id,
            option_symbol=trade.option_symbol,
            quantity=trade.quantity,
            status="open",
            opened_at=trade.entry_at,
        )
    )
    session.add(
        AuditLog(
            actor=user_id,
            action="PAPER_ENTRY",
            entity="paper_trades",
            entity_id=trade.id,
            payload={"recommendation_id": recommendation_id, "entry_price": _num(fill.entry_price)},
        )
    )
    return trade


def close_paper_trade(session: Session, trade_id: str, exit_price: Decimal, reason: str, multiplier: int) -> PaperTrade:
    trade = session.get(PaperTrade, trade_id)
    if trade is None or trade.exit_at is not None:
        raise LookupError("trade")
    dollars, percent = pnl(Decimal(str(trade.entry_price)), exit_price, trade.quantity, multiplier)
    trade.exit_price = exit_price
    trade.exit_at = datetime.now(timezone.utc)
    trade.exit_reason = reason
    trade.pnl_dollars = dollars
    trade.pnl_percent = percent
    proceeds = exit_price * Decimal(trade.quantity) * Decimal(multiplier)
    user = session.get(User, trade.user_id)
    if user is not None:
        user.paper_cash = Decimal(str(user.paper_cash)) + proceeds
    position = session.scalar(select(PaperPosition).where(PaperPosition.trade_id == trade.id))
    if position is not None:
        position.status = "closed"
        position.closed_at = trade.exit_at
    session.add(
        AuditLog(
            actor=trade.user_id,
            action="PAPER_EXIT",
            entity="paper_trades",
            entity_id=trade.id,
            payload={"reason": reason, "pnl_dollars": _num(dollars)},
        )
    )
    return trade


def list_trades(session: Session, user_id: str) -> list[PaperTrade]:
    return list(
        session.scalars(select(PaperTrade).where(PaperTrade.user_id == user_id).order_by(PaperTrade.entry_at.desc()))
    )


def _row_to_recommendation(row: RecommendationRow) -> Recommendation:
    from datetime import date

    from app.schemas.domain import OptionRight

    return Recommendation(
        recommendation_id=row.id,
        decision=DecisionKind(row.decision),
        timestamp=row.created_at,
        underlying=row.underlying,
        underlying_price=Decimal(str(row.underlying_price)),
        option_symbol=row.option_symbol,
        call_put=OptionRight(row.call_put),
        strike=Decimal(str(row.strike)),
        expiration=date.fromisoformat(row.expiration),
        option_price=Decimal(str(row.option_price)),
        bid=Decimal(str(row.bid)),
        ask=Decimal(str(row.ask)),
        delta=Decimal(str(row.delta)),
        gamma=Decimal(str(row.gamma)),
        theta=Decimal(str(row.theta)),
        vega=Decimal(str(row.vega)),
        iv=Decimal(str(row.iv)),
        volume=row.volume,
        open_interest=row.open_interest,
        max_entry_price=Decimal(str(row.max_entry_price)),
        quantity=row.quantity,
        holding_window_min=row.holding_window_min,
        holding_window_max=row.holding_window_max,
        thesis=row.thesis,
        catalyst=row.catalyst,
        risk=row.risk,
        invalidation=row.invalidation,
        reason_codes=list(row.reason_codes or []),
        gate_results=dict(row.gate_results or {}),
        scenarios=[],
    )


def list_watchlist(session: Session, user_id: str) -> list[WatchItem]:
    return list(
        session.scalars(
            select(WatchItem).where(WatchItem.user_id == user_id).order_by(WatchItem.created_at.desc())
        )
    )


def add_watch(session: Session, user_id: str, symbol: str) -> WatchItem:
    existing = session.scalar(
        select(WatchItem).where(WatchItem.user_id == user_id, WatchItem.symbol == symbol)
    )
    if existing is not None:
        return existing
    item = WatchItem(user_id=user_id, symbol=symbol, created_at=datetime.now(timezone.utc))
    session.add(item)
    return item


def remove_watch(session: Session, user_id: str, symbol: str) -> None:
    item = session.scalar(
        select(WatchItem).where(WatchItem.user_id == user_id, WatchItem.symbol == symbol)
    )
    if item is not None:
        session.delete(item)
