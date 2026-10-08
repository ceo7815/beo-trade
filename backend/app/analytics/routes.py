from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path

from fastapi import FastAPI, Header, Query

from app.analytics.finance import daily_rows, drawdown_from_equity, dte_bucket, filter_trades, monthly_rows, period_cards, split_by, summarize_trades, trading_day_pnl
from app.analytics.trades import round_trips, summarize, window, within
from app.broker.normalize import execution_pnl, parse_option_symbol
from app.broker.runtime import open_paper_broker
from app.config.settings import Settings
from app.models.db import database_ready, session_scope
from app.models.store import list_decisions
from app.models.tables import AIRequestLog, BrokerTradeEvent

LAST_SCAN = Path(__file__).resolve().parents[2] / "data" / "last_scan.json"
HEARTBEAT = Path(__file__).resolve().parents[2] / "data" / "worker_heartbeat.txt"


def write_last_scan(payload: dict) -> None:
    LAST_SCAN.parent.mkdir(parents=True, exist_ok=True)
    temporary = LAST_SCAN.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(LAST_SCAN)


def read_last_scan() -> dict | None:
    return _read_json(LAST_SCAN)


SCAN_PROGRESS = LAST_SCAN.with_name("scan_progress.json")


def write_scan_progress(payload: dict) -> None:
    SCAN_PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SCAN_PROGRESS.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(SCAN_PROGRESS)


def read_scan_progress() -> dict | None:
    return _read_json(SCAN_PROGRESS)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _parsed_time(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _dte(contract: dict | None, closed_at: datetime | None) -> int | None:
    if not contract or closed_at is None:
        return None
    expiration = contract.get("expiration")
    if not expiration:
        return None
    try:
        from datetime import date

        return (date.fromisoformat(str(expiration)) - closed_at.astimezone(timezone.utc).date()).days
    except ValueError:
        return None


def _history_window(name: str) -> tuple[str, str]:
    windows = {
        "day": ("1D", "5Min"),
        "week": ("1W", "1H"),
        "month": ("1M", "1D"),
        "quarter": ("3M", "1D"),
        "half": ("6M", "1D"),
        "year": ("1A", "1D"),
        "all": ("all", "1D"),
    }
    return windows.get(name, ("1M", "1D"))


def register(app: FastAPI, settings: Settings) -> None:
    @app.get("/api/v1/desk")
    def desk(authorization: str | None = Header(default=None)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        now = datetime.now(timezone.utc)
        age = None
        if HEARTBEAT.exists():
            age = max(0, int(now.timestamp() - HEARTBEAT.stat().st_mtime))
        fresh = age is not None and age <= settings.trading().scan_interval_seconds * 3
        return {
            "environment": "PAPER",
            "autonomous": fresh,
            "heartbeat_age_seconds": age,
            "last_scan": read_last_scan(),
            "scan_progress": read_scan_progress(),
        }

    @app.get("/api/v1/decisions")
    def decisions(authorization: str | None = Header(default=None), limit: int = Query(default=50, ge=1, le=200)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        if not database_ready():
            return {"items": []}
        with session_scope() as session:
            rows = list_decisions(session, limit)
            return {
                "items": [
                    {
                        "id": row.id,
                        "decision": row.decision,
                        "underlying": row.underlying,
                        "option_symbol": row.option_symbol,
                        "call_put": row.call_put,
                        "strike": str(row.strike),
                        "expiration": row.expiration,
                        "thesis": row.thesis,
                        "catalyst": row.catalyst,
                        "risk": row.risk,
                        "invalidation": row.invalidation,
                        "reason_codes": row.reason_codes,
                        "suppress_reason": row.suppress_reason or "",
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "policy": _decision_policy(session, row.analysis_id),
                    }
                    for row in rows
                ]
            }

    @app.get("/api/v1/finance")
    def finance(
        authorization: str | None = Header(default=None),
        window: str = "month",
        side: str = "",
        outcome: str = "",
        symbol: str = "",
        dte: str = "",
        start: str = "",
        end: str = "",
        exit_reason: str = "",
        hour: str = "",
    ):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        period, timeframe = _history_window(window)
        account = None
        positions = None
        history = None
        year = None
        activities = None
        try:
            adapter = open_paper_broker(settings)
        except Exception:
            adapter = None
        if adapter is not None:
            try:
                account = adapter.get_account()
            except Exception:
                account = None
            try:
                positions = adapter.get_positions()
            except Exception:
                positions = None
            try:
                history = adapter.get_portfolio_history(period, timeframe)
            except Exception:
                history = None
            try:
                year = adapter.get_portfolio_history("1A", "1D")
            except Exception:
                year = None
            try:
                activities = adapter.get_activities()
            except Exception:
                activities = None
            adapter.close()
        exposure = None
        if positions is not None:
            total = Decimal("0")
            seen = False
            for row in positions:
                raw = row.get("market_value")
                if raw in {None, ""}:
                    continue
                seen = True
                total += abs(Decimal(str(raw)))
            exposure = str(total.quantize(Decimal("0.01"))) if seen or positions == [] else None
            if positions == []:
                exposure = "0.00"
        trades = _stored_closed()
        filtered = filter_trades(
            trades,
            start=_parsed_time(start),
            end=_parsed_time(end),
            side=side,
            outcome=outcome,
            symbol=symbol,
            dte=dte,
            exit_reason=exit_reason,
            hour=hour,
        )
        session_date = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.exchange_timezone)).date()
        measured = None if account is None else trading_day_pnl(account.get("equity"), account.get("last_equity"), activities, session_date)
        points = [] if history is None else history.get("points") or []
        year_points = [] if year is None else year.get("points") or []
        return {
            "environment": "PAPER",
            "account": None
            if account is None
            else {
                "equity": account.get("equity"),
                "cash": account.get("cash"),
                "buying_power": account.get("buying_power"),
                "portfolio_value": account.get("portfolio_value"),
            },
            "today_pnl": None if measured is None else str(measured.quantize(Decimal("0.01"))),
            "paper_limitation": "ביצוע Paper אינו משחזר השפעת שוק, מיקום בתור, דליפת מידע או slippage של לייב. רווח Paper אינו ראיה לרווח בלייב.",
            "open_positions": None if positions is None else len(positions),
            "exposure": exposure,
            "history": history,
            "drawdown": drawdown_from_equity(points),
            "periods": period_cards(year_points),
            "daily": daily_rows(year_points),
            "monthly": monthly_rows(year_points),
            "utilization": None
            if account is None or account.get("equity") in {None, ""} or exposure is None or Decimal(str(account["equity"])) <= 0
            else str((Decimal(exposure) / Decimal(str(account["equity"]))).quantize(Decimal("0.0001"))),
            "hours": {"note": "אין מספיק נתונים"},
            "exits": {"note": "אין מספיק נתונים"},
            "trades": summarize_trades(filtered),
            "closed_trades": filtered,
            "by_side": split_by(filtered, "right"),
            "by_dte": split_by([{**row, "bucket": row.get("bucket")} for row in filtered if row.get("bucket")], "bucket"),
            "by_symbol": split_by(filtered, "underlying"),
            "sector": {"available": False, "note": "נתוני סקטור אינם זמינים"},
            "best_worst": {"note": "אין מספיק מידע לניתוח"} if len(filtered) < 3 else _best_worst(filtered),
        }

    @app.get("/api/v1/risk")
    def risk_view(authorization: str | None = Header(default=None)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        trading = settings.trading()
        account = None
        positions = None
        activities = None
        try:
            adapter = open_paper_broker(settings)
            try:
                account = adapter.get_account()
                positions = adapter.get_positions()
                activities = adapter.get_activities()
            finally:
                adapter.close()
        except Exception:
            account = None
        equity = None if account is None or account.get("equity") in {None, ""} else Decimal(str(account["equity"]))
        session_date = datetime.now(timezone.utc).astimezone(ZoneInfo(settings.exchange_timezone)).date()
        measured = None if account is None else trading_day_pnl(account.get("equity"), account.get("last_equity"), activities, session_date)
        daily = None if measured is None else str(measured.quantize(Decimal("0.01")))
        loss_used = None
        if daily is not None and Decimal(daily) < 0:
            loss_used = str(abs(Decimal(daily)))
        elif daily is not None:
            loss_used = "0.00"
        count = None if positions is None else len(positions)
        stopped = False
        warning = False
        from app.options.risk_limits import daily_loss_state

        level = "ok" if equity is None or measured is None else daily_loss_state(equity, measured, trading)
        if level in {"entry_stop", "hard_stop"}:
            stopped = True
        elif level == "warning":
            warning = True
        if count is not None and count >= trading.max_concurrent_positions:
            warning = True
        if equity is None:
            state = "אין נתוני חשבון"
        elif daily is None:
            state = "אין בסיס הפסד יומי"
        elif stopped:
            state = "עצור"
        elif warning:
            state = "אזהרה"
        else:
            state = "בתוך המגבלה"
        return {
            "environment": "PAPER",
            "state": state,
            "per_trade_limit": None if equity is None else str((equity * Decimal(str(trading.max_capital_per_trade_pct))).quantize(Decimal("0.01"))),
            "exposure_limit": None if equity is None else str((equity * Decimal(str(trading.max_total_open_exposure_pct))).quantize(Decimal("0.01"))),
            "daily_loss_limit": None if equity is None else str((equity * Decimal(str(trading.daily_entry_stop_pct))).quantize(Decimal("0.01"))),
            "daily_loss_state": level,
            "daily_loss_used": loss_used,
            "today_pnl": daily,
            "max_positions": trading.max_concurrent_positions,
            "open_positions": count,
            "max_spread": str(trading.max_bid_ask_spread),
            "max_slippage": str(trading.slippage_percent),
            "sector_note": "נתוני סקטור אינם זמינים",
        }

    @app.get("/api/v1/report/trades")
    def trades_report(authorization: str | None = Header(default=None), period: str = "all", start: str = "", end: str = ""):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        frame = window(period, start, end, zone=settings.exchange_timezone)
        try:
            adapter = open_paper_broker(settings)
        except Exception as exc:
            return {"environment": "PAPER", "available": False, "detail": type(exc).__name__}
        try:
            fills = adapter.get_all_fills()
            positions = adapter.get_positions()
        except Exception as exc:
            return {"environment": "PAPER", "available": False, "detail": type(exc).__name__}
        finally:
            adapter.close()
        report = round_trips(fills, positions, multiplier=settings.trading().contract_multiplier)
        closed = [row for row in report["closed"] if within(row.get("closed_at"), frame)]
        still_open = report["open"] if frame["includes_today"] else []
        ai_cost = None
        if database_ready():
            with session_scope() as session:
                _annotate(session, closed + still_open)
                _explain(session, closed + still_open, settings.trading())
                _levels(session, still_open, settings)
                from app.ai.budget import make_ledger
                from app.models.store import load_usage

                entries = load_usage(session, datetime(2000, 1, 1, tzinfo=timezone.utc), make_ledger(settings).cost_of)
                ai_cost = sum((entry.cost for entry in entries if within(entry.created_at, frame)), Decimal("0"))
        return {
            "environment": "PAPER",
            "available": True,
            "source": "alpaca-paper FILL",
            "window": frame,
            "summary": summarize(closed, still_open, ai_cost),
            "closed": closed,
            "open": still_open,
            "orphan_sells": report["orphan_sells"],
            "paper_limitation": "מחירי מילוי Paper אופטימיים ביחס ללייב. רווח Paper אינו ראיה לרווח בכסף אמיתי.",
        }

    @app.get("/api/v1/ai/summary")
    def ai_summary(authorization: str | None = Header(default=None)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        if not database_ready():
            return {"items": 0, "latency_ms": None, "cache_hit_rate": None, "reasoning_tokens": None}
        with session_scope() as session:
            rows = list(session.scalars(select_logs()))
        hits = [row.cache_hit for row in rows if row.cache_hit is not None]
        latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
        return {
            "items": len(rows),
            "latency_ms": None if not latencies else int(sum(latencies) / len(latencies)),
            "cache_hit_rate": None if not hits else str(Decimal(sum(1 for item in hits if item)) / Decimal(len(hits))),
            "input_tokens": _sum_optional(row.input_tokens for row in rows),
            "output_tokens": _sum_optional(row.output_tokens for row in rows),
            "reasoning_tokens": _sum_optional(row.reasoning_tokens for row in rows),
        }

    @app.get("/api/v1/audit")
    def audit(authorization: str | None = Header(default=None)):
        from app.main import user_id_from_header

        user_id_from_header(settings, authorization)
        if not database_ready():
            return {"items": []}
        from sqlalchemy import select

        with session_scope() as session:
            rows = list(session.scalars(select(BrokerTradeEvent).order_by(BrokerTradeEvent.created_at.desc()).limit(40)))
            return {
                "items": [
                    {"trade_id": row.trade_id, "state": row.state, "source": row.source, "reason": row.reason, "created_at": row.created_at.isoformat()}
                    for row in rows
                ]
            }


def _decision_policy(session, analysis_id: str | None) -> dict:
    if not analysis_id:
        return {}
    from app.models.tables import AIAnalysis

    row = session.get(AIAnalysis, analysis_id)
    snapshot = row.snapshot if row is not None and isinstance(row.snapshot, dict) else {}
    policy = snapshot.get("policy")
    return policy if isinstance(policy, dict) else {}


def select_logs():
    from sqlalchemy import select

    return select(AIRequestLog).order_by(AIRequestLog.created_at.desc()).limit(500)


def _sum_optional(values) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return int(sum(present))


def _round_trip_fee(buy: dict, sell: dict) -> str | None:
    def one(row: dict) -> Decimal | None:
        if "fee" not in row and "commission" not in row:
            return None
        raw = row.get("fee", row.get("commission"))
        if raw in {None, ""}:
            return None
        return Decimal(str(raw))

    paid = [one(buy), one(sell)]
    if any(item is None for item in paid):
        return None
    return str(sum(paid, Decimal("0")))


def _annotate(session, trades: list[dict]) -> None:
    """Exit reason and entry plan from the desk's own records, matched by contract and time."""
    from datetime import timedelta

    from sqlalchemy import select

    from app.models.tables import PositionState

    symbols = {row["symbol"] for row in trades}
    if not symbols:
        return
    events = list(
        session.scalars(
            select(BrokerTradeEvent)
            .where(BrokerTradeEvent.state.in_(("EXIT_TRIGGERED", "CLOSE_ALL")))
            .order_by(BrokerTradeEvent.created_at.asc())
        )
    )
    states = list(session.scalars(select(PositionState).where(PositionState.symbol.in_(symbols))))
    slack = timedelta(minutes=3)
    for trade in trades:
        opened = _parsed_time(trade.get("opened_at") or "")
        closed = _parsed_time(trade.get("closed_at") or "")
        trade["exit_reason"] = None
        trade["exit_detail"] = None
        if opened is not None and closed is not None:
            for event in events:
                stamp = event.created_at if event.created_at.tzinfo else event.created_at.replace(tzinfo=timezone.utc)
                if not (opened - slack <= stamp <= closed + slack):
                    continue
                if event.state == "CLOSE_ALL":
                    trade["exit_reason"] = "CLOSE_ALL"
                    trade["exit_detail"] = event.reason
                elif event.trade_id.replace(" ", "").upper() == trade["symbol"]:
                    parts = (event.reason or "").split()
                    trade["exit_reason"] = parts[0] if parts else None
                    trade["exit_detail"] = event.reason
        trade["plan"] = None
        if opened is None:
            continue
        nearest = None
        for state in states:
            if state.symbol.replace(" ", "").upper() != trade["symbol"]:
                continue
            entry = state.entry_time if state.entry_time.tzinfo else state.entry_time.replace(tzinfo=timezone.utc)
            gap = abs((entry - opened).total_seconds())
            if gap <= 900 and (nearest is None or gap < nearest[0]):
                nearest = (gap, state)
        if nearest is None:
            continue
        state = nearest[1]
        text = lambda value: None if value is None else str(value)  # noqa: E731
        trade["plan"] = {
            "thesis": state.thesis or None,
            "invalidation": state.invalidation or None,
            "direction": state.underlying_direction or None,
            "underlying_at_entry": text(state.entry_underlying_price),
            "delta_at_entry": text(state.entry_delta),
            "iv_at_entry": text(state.entry_iv),
            "stop_price": text(state.initial_stop_price),
            "target_1": text(state.target_1),
            "target_2": text(state.target_2),
            "planned_risk": text(state.planned_risk),
            "peak_price": text(state.peak_option_price),
        }


def _entry_recommendation(session, trade: dict) -> dict | None:
    """The BUY that opened this trade: by its broker order first, then by contract and time."""
    from sqlalchemy import select

    from app.models.tables import AIAnalysis, BrokerOrderRecord, RecommendationRow

    row = None
    order_ids = [str(item) for item in trade.get("order_ids") or [] if item]
    if order_ids:
        orders = session.scalars(
            select(BrokerOrderRecord).where(
                BrokerOrderRecord.broker_order_id.in_(order_ids),
                BrokerOrderRecord.position_intent == "buy_to_open",
                BrokerOrderRecord.recommendation_id != "",
            )
        )
        for order in orders:
            row = session.get(RecommendationRow, order.recommendation_id)
            if row is not None:
                break
    opened = _parsed_time(trade.get("opened_at") or "")
    if row is None and opened is not None:
        symbol = str(trade.get("symbol") or "").replace(" ", "").upper()
        candidates = session.scalars(
            select(RecommendationRow).where(
                RecommendationRow.underlying == str(trade.get("underlying") or ""),
                RecommendationRow.decision == "BUY",
            )
        )
        best = None
        for candidate in candidates:
            if candidate.option_symbol.replace(" ", "").upper() != symbol:
                continue
            made = candidate.created_at if candidate.created_at.tzinfo else candidate.created_at.replace(tzinfo=timezone.utc)
            gap = (opened - made).total_seconds()
            if -120 <= gap <= 1800 and (best is None or abs(gap) < best[0]):
                best = (abs(gap), candidate)
        row = None if best is None else best[1]
    if row is None:
        return None
    analysis = session.get(AIAnalysis, row.analysis_id) if row.analysis_id else None
    return {
        "thesis": row.thesis,
        "catalyst": row.catalyst,
        "reason_codes": list(row.reason_codes or []),
        "underlying_price": str(row.underlying_price),
        "ai_reviewed": bool(analysis is not None and analysis.model),
    }


def _levels(session, trades: list[dict], settings) -> None:
    from zoneinfo import ZoneInfo

    from app.analytics.levels import position_levels
    from app.positions.state import load_open_state

    now = datetime.now(timezone.utc)
    session_day = now.astimezone(ZoneInfo(settings.exchange_timezone)).date()
    for trade in trades:
        state = load_open_state(session, str(trade.get("symbol") or ""))
        trade["levels"] = position_levels(trade, state, settings.trading(), now, session_day)


def _explain(session, trades: list[dict], config) -> None:
    from app.analytics.story import trade_story

    for trade in trades:
        trade["story"] = trade_story(trade, _entry_recommendation(session, trade), config)


def _stored_closed() -> list[dict]:
    if not database_ready():
        return []
    from sqlalchemy import select

    from app.models.tables import BrokerFillRecord, BrokerTradeEvent

    with session_scope() as session:
        rows = list(session.scalars(select(BrokerFillRecord).order_by(BrokerFillRecord.observed_at.asc())))
        exits = {
            row.trade_id: row.reason
            for row in session.scalars(select(BrokerTradeEvent).where(BrokerTradeEvent.state == "EXIT_TRIGGERED"))
            if row.trade_id
        }
    grouped: dict[str, list] = {}
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        grouped.setdefault(row.trade_id or str(payload.get("symbol") or ""), []).append(payload)
    closed = []
    for key, items in grouped.items():
        buys = [item for item in items if str(item.get("side") or "").lower() == "buy" and item.get("price") not in {None, ""}]
        sells = [item for item in items if str(item.get("side") or "").lower() == "sell" and item.get("price") not in {None, ""}]
        if not buys or not sells:
            continue
        result = execution_pnl(str(buys[0]["price"]), str(sells[-1]["price"]), str(buys[0].get("qty") or "0"), _round_trip_fee(buys[0], sells[-1]))
        if result is None:
            continue
        contract = parse_option_symbol(str(buys[0].get("symbol") or ""))
        closed_at = _parsed_time(str(sells[-1].get("timestamp") or ""))
        dte = _dte(contract, closed_at)
        closed.append(
            {
                "trade_id": key,
                "symbol": buys[0].get("symbol") or "",
                "underlying": None if contract is None else contract["underlying"],
                "right": None if contract is None else contract["right"],
                "pnl": result.get("gross_pnl"),
                "gross_pnl": result.get("gross_pnl"),
                "net_pnl": result.get("net_pnl"),
                "fees": result.get("fees"),
                "fees_note": result.get("fees_note"),
                "return_pct": result.get("return_pct"),
                "closed_at": closed_at,
                "dte": dte,
                "bucket": dte_bucket(dte),
                "exit_reason": exits.get(key) or "",
                "entry_price": str(buys[0]["price"]),
                "exit_price": str(sells[-1]["price"]),
                "qty": str(buys[0].get("qty") or ""),
                "opened_at": None if _parsed_time(str(buys[0].get("timestamp") or "")) is None else _parsed_time(str(buys[0].get("timestamp") or "")).isoformat(),
                "hour": None if closed_at is None else closed_at.astimezone(ZoneInfo("Asia/Dubai")).strftime("%H"),
            }
        )
    return closed


def _closed_from_fills(fills: list[dict]) -> list[dict]:
    grouped: dict[str, list] = {}
    for row in fills:
        grouped.setdefault(str(row.get("order_id") or row.get("symbol") or ""), []).append(row)
    closed = []
    for key, rows in grouped.items():
        buys = [row for row in rows if str(row.get("side") or "").lower() == "buy" and row.get("price") not in {None, ""}]
        sells = [row for row in rows if str(row.get("side") or "").lower() == "sell" and row.get("price") not in {None, ""}]
        if not buys or not sells:
            continue
        result = execution_pnl(str(buys[0]["price"]), str(sells[-1]["price"]), str(buys[0].get("qty") or "0"), _round_trip_fee(buys[0], sells[-1]))
        if result is None:
            continue
        contract = parse_option_symbol(str(buys[0].get("symbol") or ""))
        closed_at = _parsed_time(str(sells[-1].get("timestamp") or ""))
        dte = _dte(contract, closed_at)
        closed.append(
            {
                "trade_id": key,
                "symbol": buys[0].get("symbol") or "",
                "underlying": None if contract is None else contract["underlying"],
                "right": None if contract is None else contract["right"],
                "pnl": result.get("gross_pnl"),
                "gross_pnl": result.get("gross_pnl"),
                "net_pnl": result.get("net_pnl"),
                "fees": result.get("fees"),
                "fees_note": result.get("fees_note"),
                "return_pct": result.get("return_pct"),
                "closed_at": closed_at,
                "dte": dte,
                "bucket": dte_bucket(dte),
            }
        )
    return closed


def _best_worst(trades: list[dict]) -> dict:
    ranked = [row for row in trades if row.get("pnl") not in {None, ""}]
    if len(ranked) < 3:
        return {"note": "אין מספיק מידע לניתוח"}
    ordered = sorted(ranked, key=lambda row: Decimal(str(row["pnl"])))
    return {"note": None, "worst_symbol": ordered[0].get("symbol"), "best_symbol": ordered[-1].get("symbol")}
