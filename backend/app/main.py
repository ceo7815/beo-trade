from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select

from app.config.settings import Settings, get_settings
from app.core.sessions import format_clock, phase_at, session_bounds
from app.models.db import configure_database, database_ready, init_db, session_scope
from app.models.store import (
    add_watch,
    close_paper_trade,
    count_internal_candidates,
    create_paper_trade,
    get_buy,
    get_user,
    list_buys,
    list_trades,
    list_watchlist,
    load_usage,
    open_positions,
    paper_equity,
    remove_watch,
)
from app.models.tables import AIUsage, RecommendationRow
from app.ai.budget import make_ledger
from app.providers.base import ProviderUnavailable
from app.providers.benzinga import BenzingaError
from app.providers.registry import build_providers
from app.recommendations.live_scan import MarketDataMissing, execute_scan
from app.schemas.domain import REASON_HEBREW, DecisionKind
from app.ops.paths import HEARTBEAT
from app.ops.readiness import build_health, build_ready
from app.ops.safety import assert_boot_safe
from app.security.auth import user_id_from_header
from app.security.middleware import RateLimitMiddleware, SecurityHeadersMiddleware

log = structlog.get_logger()


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def _card(row: RecommendationRow) -> dict:
    return {
        "recommendation_id": row.id,
        "timestamp": row.created_at.isoformat(),
        "underlying": row.underlying,
        "underlying_price": _money(row.underlying_price),
        "option_symbol": row.option_symbol,
        "call_put": row.call_put,
        "strike": _money(row.strike),
        "expiration": row.expiration,
        "option_price": _money(row.option_price),
        "max_entry_price": _money(row.max_entry_price),
        "quantity": row.quantity,
        "holding_window_min": row.holding_window_min,
        "holding_window_max": row.holding_window_max,
        "why": [REASON_HEBREW.get(code, code) for code in (row.reason_codes or [])],
        "thesis": row.thesis,
        "catalyst": row.catalyst,
        "risk": row.risk,
        "invalidation": row.invalidation,
    }


def _detail(row: RecommendationRow) -> dict:
    card = _card(row)
    vega = float(row.vega)
    card.update(
        {
            "bid": _money(row.bid),
            "ask": _money(row.ask),
            "delta": f"{float(row.delta):.3f}",
            "gamma": f"{float(row.gamma):.4f}",
            "theta": f"{float(row.theta):.4f}",
            "vega": f"{vega * 0.01:.4f}",
            "iv": f"{float(row.iv) * 100:.1f}%",
            "volume": row.volume,
            "open_interest": row.open_interest,
            "scenarios": row.scenarios or [],
            "greek_notes": [
                f"דלתא {float(row.delta):.2f}: דולר אחד בנכס מזיז את מחיר האופציה בכ-{abs(float(row.delta)):.2f} דולר.",
                f"גמא {float(row.gamma):.4f}: קצב שינוי הדלתא כשהמחיר זז.",
                f"תטא {float(row.theta):.3f}: שחיקת זמן משוערת ליממה.",
                f"וגא {vega * 0.01:.3f}: שינוי מחיר משוער לכל 1% בתנודתיות.",
            ],
        }
    )
    return card


class PaperOpenBody(BaseModel):
    recommendation_id: str


class PaperCloseBody(BaseModel):
    exit_price: Decimal
    reason: str = "MANUAL"


class WatchBody(BaseModel):
    symbol: str


class TradingHaltBody(BaseModel):
    halted: bool


def _decision_analyzer(settings: Settings, ledger):
    from app.ai.client import OpenAIResponsesClient
    from app.integrations.secrets import resolve_secret

    key, _origin = resolve_secret(settings, "openai_api_key")
    if not key:
        return None
    if settings.openai_price_input_per_million <= 0 or settings.openai_price_output_per_million <= 0:
        return None
    return OpenAIResponsesClient(settings, ledger)


def create_app(settings: Settings | None = None) -> FastAPI:
    current = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.JSONRenderer(),
            ]
        )
        assert_boot_safe(current)
        configure_database(current)
        init_db(current)
        log.info("api.started", service="api", trading_mode="PAPER")
        yield

    app = FastAPI(title=current.app_name, lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    # The desk polls several live endpoints. Local use stays well above the
    # production ceiling so a single open tab cannot lock itself out.
    rate_limit = 120 if current.app_env == "production" else 2000
    app.add_middleware(RateLimitMiddleware, limit=rate_limit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[item.strip() for item in current.cors_origins.split(",") if item.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    def health():
        code, body = build_health(current)
        if code != 200:
            raise HTTPException(status_code=code, detail=body)
        return body

    @app.get("/ready")
    def ready():
        code, body = build_ready(current)
        if code != 200:
            raise HTTPException(status_code=code, detail=body)
        return body

    @app.get("/metrics")
    def metrics():
        lines = ["# TYPE beo_up gauge", "beo_up 1"]
        if database_ready():
            with session_scope() as session:
                buys = session.scalar(select(func.count()).select_from(RecommendationRow).where(RecommendationRow.decision == "BUY")) or 0
                cost = session.scalar(select(func.coalesce(func.sum(AIUsage.cost), 0))) or 0
            lines.append(f"beo_active_buys {buys}")
            lines.append(f"beo_ai_cost_usd {cost}")
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain")

    @app.get("/api/v1/system")
    def system_status(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        now = datetime.now(timezone.utc)
        calendar = current.calendar()
        phase = phase_at(now, calendar)
        bounds = session_bounds(now, calendar)
        provider_set = build_providers(current)
        providers = provider_set.status()
        news_status = provider_set.news_status()
        market_status = provider_set.market_status()
        options_status = provider_set.options_status()
        if provider_set.macro is not None:
            provider_set.macro.latest(now)
        macro_status = provider_set.macro_status()
        if provider_set.research is not None:
            provider_set.research.latest(now)
        research_status = provider_set.research_status()
        alerts: list[str] = []
        missing_labels = [
            label
            for key, label in (("market", "נתוני השוק"), ("options", "האופציות"), ("news", "החדשות"))
            if providers.get(key) == "unconfigured"
        ]
        if missing_labels:
            if len(missing_labels) == 1:
                missing_text = missing_labels[0]
            elif len(missing_labels) == 2:
                missing_text = f"{missing_labels[0]} ו{missing_labels[1]}"
            else:
                missing_text = ", ".join(missing_labels[:-1]) + " ו" + missing_labels[-1]
            alerts.append(f"עדיין לא חוברו: {missing_text}. לא יוצגו המלצות בלי נתונים אמיתיים.")
        database = "ok" if database_ready() else "down"
        month_cost = Decimal("0")
        day_cost = Decimal("0")
        hour_cost = Decimal("0")
        calls = 0
        average = None
        mode = "unpriced"
        equity = Decimal("0")
        cash = Decimal("0")
        buys = 0
        internal = 0
        if database == "ok":
            with session_scope() as session:
                user = get_user(session, current.dev_user_id if not current.auth_required else user_id_from_header(current, authorization))
                ledger = make_ledger(current, [])
                from app.ai.budget import BudgetLedger

                start = ledger._start(now, day_only=False)
                entries = load_usage(session, start, ledger.cost_of)
                ledger = make_ledger(current, entries)
                month_cost = ledger.month_total(now)
                day_cost = ledger.day_total(now)
                hour_cost = ledger.hour_total(now)
                calls = len(entries)
                mode = ledger.mode(now).value
                buys = len(list_buys(session))
                ledger.buy_count = buys
                average = ledger.average_per_buy(now)
                internal = count_internal_candidates(session)
                if user is not None:
                    cash = Decimal(str(user.paper_cash))
                    equity = paper_equity(session, user.id)
        if mode == "unpriced":
            alerts.append("מחירי הטוקנים לא הוגדרו. קריאות AI חסומות כדי לא לחרוג מתקרת 200 הדולר.")
        elif mode == "stopped":
            alerts.append("תקציב ה-AI הגיע לתקרה. לא יישלחו קריאות נוספות החודש.")
        elif mode in {"soft", "critical"}:
            alerts.append("השימוש ב-AI התקרב לתקרה. נשארו רק קריאות החלטה.")
        heartbeat_age = None
        if HEARTBEAT.exists():
            heartbeat_age = now.timestamp() - HEARTBEAT.stat().st_mtime
            if heartbeat_age > current.trading().scan_interval_seconds * 3:
                alerts.append("הסורק לא דיווח לאחרונה.")
        else:
            alerts.append("הסורק עדיין לא עלה.")
        health_state = "ok" if database == "ok" and not alerts else "degraded"
        from app.control.halt import is_halted

        return {
            "app": current.app_name,
            "market_phase": phase,
            "exchange_timezone": calendar.timezone,
            "display_timezone": current.display_timezone,
            "now_exchange": format_clock(now, calendar.timezone),
            "now_display": format_clock(now, current.display_timezone),
            "session_open": bounds[0].isoformat() if bounds else None,
            "session_close": bounds[1].isoformat() if bounds else None,
            "providers": providers,
            "news_status": news_status,
            "market_status": market_status,
            "options_status": options_status,
            "macro_status": macro_status,
            "research_status": research_status,
            "regime_status": __import__("app.market.regime", fromlist=["last_regime"]).last_regime(),
            "internal_candidates": internal,
            "active_buys": buys,
            "paper_equity": _money(equity),
            "paper_cash": _money(cash),
            "equity_basis": "entry_price",
            "ai_month_usd": _money(month_cost),
            "ai_day_usd": _money(day_cost),
            "ai_hour_usd": _money(hour_cost),
            "ai_hard_limit": _money(current.ai_hard_limit),
            "ai_soft_budget": _money(current.ai_soft_budget),
            "ai_critical_budget": _money(current.ai_critical_budget),
            "ai_calls": calls,
            "ai_avg_cost_per_buy": None if average is None else _money(average),
            "ai_mode": mode,
            "ai_model": current.openai_model,
            "health": health_state,
            "database": database,
            "alerts": alerts,
            "worker_heartbeat_age_seconds": heartbeat_age,
            "paper_only": True,
            "trading_halted": is_halted(),
        }

    @app.get("/api/v1/control/trading")
    def trading_control(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.control.halt import halt_view

        return halt_view()

    @app.post("/api/v1/control/trading")
    def set_trading_control(body: TradingHaltBody, authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.control.halt import set_halted

        return set_halted(body.halted)

    @app.post("/api/v1/control/close-all")
    def close_all(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.workers.loops import close_all_positions

        return close_all_positions(current)

    @app.get("/api/v1/readiness/pre-payment")
    def pre_payment(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.readiness.prepayment import build_report

        return build_report(current)

    @app.get("/api/v1/recommendations")
    def recommendations(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        with session_scope() as session:
            return {"items": [_card(row) for row in list_buys(session)]}

    @app.get("/api/v1/recommendations/{recommendation_id}")
    def recommendation_detail(recommendation_id: str, authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        with session_scope() as session:
            row = get_buy(session, recommendation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="ההמלצה לא נמצאה")
            return _detail(row)

    @app.post("/api/v1/recommendations/{recommendation_id}/approve")
    def approve(recommendation_id: str, authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.broker.approval import approve_recommendation
        from app.infra.redis_client import publish_quiet

        with session_scope() as session:
            row = get_buy(session, recommendation_id)
            if row is None or row.decision != "BUY":
                raise HTTPException(status_code=404, detail="אין המלצת BUY לאישור")
            approval = approve_recommendation(session, recommendation_id)
            session.commit()
            payload = {
                "approval_id": approval.id,
                "trade_id": approval.trade_id,
                "recommendation_id": approval.recommendation_id,
                "approval_status": approval.status,
                "approved_at": approval.approved_at.isoformat(),
            }
        publish_quiet("trade.approved", payload["trade_id"])
        return payload

    @app.get("/api/v1/trades/{trade_id}")
    def trade_audit(trade_id: str, authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.models.tables import ApprovalRecord, BrokerFillRecord, BrokerOrderSnapshot, BrokerTradeEvent

        with session_scope() as session:
            approval = session.scalar(select(ApprovalRecord).where(ApprovalRecord.trade_id == trade_id))
            events = list(session.scalars(select(BrokerTradeEvent).where(BrokerTradeEvent.trade_id == trade_id)))
            orders = list(session.scalars(select(BrokerOrderSnapshot).where(BrokerOrderSnapshot.trade_id == trade_id)))
            fills = list(session.scalars(select(BrokerFillRecord).where(BrokerFillRecord.trade_id == trade_id)))
        return {
            "trade_id": trade_id,
            "approval": None if approval is None else {"approval_id": approval.id, "recommendation_id": approval.recommendation_id, "status": approval.status, "approved_at": approval.approved_at.isoformat()},
            "events": [{"state": row.state, "source": row.source, "reason": row.reason, "at": row.created_at.isoformat()} for row in events],
            "orders": [row.payload for row in orders],
            "fills": [row.payload for row in fills],
        }

    @app.get("/api/v1/health")
    def health(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.integrations.probes import PROBES
        from app.market.regime import last_regime

        report = {}
        for name, probe in PROBES.items():
            if name == "xcloud":
                continue
            try:
                status, latency, detail = probe(current) if name != "xcloud" else probe()
            except Exception:
                status, latency, detail = "error", None, "תקלה"
            report[name] = {"status": status, "latency_ms": latency, "detail": detail}
        regime = last_regime()
        report["regime"] = {"status": "blocked" if regime.get("status") != "CALCULATED" else "internal", "detail": "ממומש — ממתין לנתוני שוק" if regime.get("status") != "CALCULATED" else "חושב מנתוני הקשר", "latency_ms": None}
        worker_age = None if not HEARTBEAT.exists() else max(0, int(datetime.now(timezone.utc).timestamp() - HEARTBEAT.stat().st_mtime))
        worker_fresh = worker_age is not None and worker_age <= current.trading().scan_interval_seconds * 3
        report["scan_worker"] = {"status": "internal" if worker_fresh else "not_verified", "detail": "פעימה טרייה" if worker_fresh else "הלולאה בקוד. אין פעימה טרייה.", "latency_ms": worker_age, "last_success": None if not worker_fresh else "heartbeat"}
        report["position_monitor"] = report["scan_worker"]
        report["reconciliation"] = {"status": "not_verified", "detail": "לולאת ההתאמה רצה עם ה-worker. אין פער מאומת בלי קריאה חיה.", "latency_ms": None}
        return {"paper_only": True, "items": report}

    @app.post("/api/v1/scan")
    def scan(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        providers = build_providers(current)
        now = datetime.now(timezone.utc)
        trading = current.trading()
        ledger = make_ledger(current, [])
        with session_scope() as session:
            user = get_user(session, current.dev_user_id if not current.auth_required else user_id_from_header(current, authorization))
            entries = load_usage(session, ledger._start(now, day_only=False), ledger.cost_of)
            from app.broker.runtime import paper_equity

            cash = paper_equity(current)
            if cash is None:
                cash = Decimal("0")
            active = {row.option_symbol for row in open_positions(session)}
        ledger = make_ledger(current, entries)
        try:
            result = execute_scan(
                providers,
                trading,
                _decision_analyzer(current, ledger),
                ledger,
                now,
                now.astimezone(ZoneInfo(current.exchange_timezone)).date(),
                cash,
                active,
            )
        except MarketDataMissing as exc:
            raise HTTPException(
                status_code=409,
                detail=f"{exc.variable} לא הוגדר. הסריקה לא רצה בלי ספק נתונים.",
            ) from exc
        except BenzingaError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        buys = sum(1 for row in result.recommendations if row.decision is DecisionKind.BUY)
        return {
            "started": True,
            "recommendations": len(result.recommendations),
            "buys": buys,
            "news_count": result.news_count,
            "scan_id": result.scan_id,
            "option_symbols": result.option_symbols,
            "ai_calls": result.ai_calls,
            "universe": result.universe,
        }

    @app.get("/api/v1/policy")
    def policy_view(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        from app.policy.sizing import policy_view as view

        return view(current.trading())

    @app.get("/api/v1/settings")
    def settings_view(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        trading = current.trading()
        return {
            "paper_only": True,
            "model": current.openai_model,
            "display_timezone": current.display_timezone,
            "exchange_timezone": current.exchange_timezone,
            "thresholds": {
                "min_option_volume": trading.min_option_volume,
                "min_open_interest": trading.min_open_interest,
                "max_bid_ask_spread": trading.max_bid_ask_spread,
                "max_expiration_days": trading.max_expiration_days,
                "min_delta": trading.min_delta,
                "max_delta": trading.max_delta,
                "max_buys_per_scan": trading.max_buys_per_scan,
                "scan_interval_seconds": trading.scan_interval_seconds,
                "holding_window_min_minutes": trading.holding_window_min_minutes,
                "holding_window_max_minutes": trading.holding_window_max_minutes,
                "max_capital_per_trade": trading.max_capital_per_trade,
                "paper_account_balance": _money(current.paper_account_balance),
            },
        }

    @app.get("/api/v1/paper/account")
    def paper_account(authorization: str | None = Header(default=None)):
        user_id = user_id_from_header(current, authorization)
        with session_scope() as session:
            user = get_user(session, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="חשבון הדמה לא נמצא")
            trades = list_trades(session, user_id)
            starting = Decimal(str(user.paper_starting_cash))
            equity = paper_equity(session, user_id)
            realized = sum((Decimal(str(trade.pnl_dollars or 0)) for trade in trades if trade.pnl_dollars is not None), Decimal("0"))
            open_cost = sum(
                (
                    Decimal(str(trade.entry_price)) * Decimal(trade.quantity) * Decimal(current.trading().contract_multiplier)
                    for trade in trades
                    if trade.exit_at is None
                ),
                Decimal("0"),
            )
            return_percent = ((equity - starting) / starting) if starting else Decimal("0")
            return {
                "source": "beo-trade-internal",
                "cash": _money(user.paper_cash),
                "starting_cash": _money(starting),
                "equity": _money(equity),
                "realized_pnl": _money(realized),
                "open_cost": _money(open_cost),
                "return_percent": f"{(return_percent * 100).quantize(Decimal('0.01'))}",
                "open_positions": len(open_positions(session)),
                "trades": [
                    {
                        "trade_id": trade.id,
                        "recommendation_id": trade.recommendation_id,
                        "option_symbol": trade.option_symbol,
                        "quantity": trade.quantity,
                        "entry_price": _money(trade.entry_price),
                        "entry_at": trade.entry_at.isoformat(),
                        "exit_price": None if trade.exit_price is None else _money(trade.exit_price),
                        "exit_reason": trade.exit_reason,
                        "pnl_dollars": None if trade.pnl_dollars is None else _money(trade.pnl_dollars),
                        "pnl_percent": None if trade.pnl_percent is None else str(trade.pnl_percent),
                    }
                    for trade in trades
                ],
            }

    @app.post("/api/v1/paper/trades")
    def open_trade(body: PaperOpenBody, authorization: str | None = Header(default=None)):
        user_id = user_id_from_header(current, authorization)
        with session_scope() as session:
            try:
                trade = create_paper_trade(
                    session, user_id, body.recommendation_id, current.trading().contract_multiplier,
                )
                session.commit()
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="ההמלצה לא זמינה") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="אי אפשר לפתוח את עסקת הדמה") from exc
            return {"trade_id": trade.id, "entry_price": _money(trade.entry_price), "quantity": trade.quantity}

    @app.post("/api/v1/paper/trades/{trade_id}/exit")
    def exit_trade(trade_id: str, body: PaperCloseBody, authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        if body.exit_price <= 0:
            raise HTTPException(status_code=422, detail="מחיר יציאה לא תקין")
        with session_scope() as session:
            try:
                trade = close_paper_trade(
                    session, trade_id, body.exit_price, body.reason, current.trading().contract_multiplier,
                )
                session.commit()
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="העסקה לא נמצאה") from exc
            return {"trade_id": trade.id, "pnl_dollars": _money(trade.pnl_dollars), "pnl_percent": str(trade.pnl_percent)}

    @app.post("/api/v1/backtests")
    def backtests(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        raise HTTPException(
            status_code=409,
            detail="אין צילומי שוק היסטוריים במסד. מנוע הבדיקה לא ירוץ על נתונים מומצאים.",
        )

    @app.get("/api/v1/watchlist")
    def watchlist(authorization: str | None = Header(default=None)):
        user_id = user_id_from_header(current, authorization)
        with session_scope() as session:
            return {
                "items": [
                    {"symbol": item.symbol, "created_at": item.created_at.isoformat()}
                    for item in list_watchlist(session, user_id)
                ]
            }

    @app.post("/api/v1/watchlist")
    def watch_add(body: WatchBody, authorization: str | None = Header(default=None)):
        user_id = user_id_from_header(current, authorization)
        symbol = body.symbol.strip().upper()
        if not symbol or len(symbol) > 12 or not symbol.replace(".", "").replace("-", "").isalnum():
            raise HTTPException(status_code=422, detail="סימול לא תקין")
        with session_scope() as session:
            if get_user(session, user_id) is None:
                raise HTTPException(status_code=404, detail="חשבון הדמה לא נמצא")
            item = add_watch(session, user_id, symbol)
            session.commit()
            return {"symbol": item.symbol}

    @app.delete("/api/v1/watchlist/{symbol}")
    def watch_remove(symbol: str, authorization: str | None = Header(default=None)):
        user_id = user_id_from_header(current, authorization)
        with session_scope() as session:
            remove_watch(session, user_id, symbol.strip().upper())
            session.commit()
        return {"removed": symbol.strip().upper()}

    @app.get("/api/v1/alerts")
    def alerts(authorization: str | None = Header(default=None)):
        user_id_from_header(current, authorization)
        with session_scope() as session:
            from app.models.tables import Alert

            rows = session.scalars(select(Alert).order_by(Alert.created_at.desc()).limit(20))
            return {
                "items": [
                    {"id": row.id, "kind": row.kind, "message": row.message, "created_at": row.created_at.isoformat()}
                    for row in rows
                    if row.kind == "BUY"
                ]
            }

    from app.integrations.routes import register as register_integrations

    register_integrations(app, current)
    from app.analytics.routes import register as register_analytics

    register_analytics(app, current)
    return app


app = create_app()
