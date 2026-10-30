from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.normalize import OPEN_ORDER_STATES, clock_day, parse_option_symbol
from app.control.halt import is_halted, order_blocked_by_halt
from app.broker.store import open_order_for_symbol, order_by_client, record_trade_event
from app.config.settings import Settings
from app.integrations.alpaca import ALLOWED_INTENTS, PaperOnlyError, option_order
from app.models.tables import ApprovalRecord, BrokerOrderRecord, RecommendationRow
from app.schemas.domain import OptionSnapshot


FINISHED_ORDER_STATES = frozenset({"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "DONE_FOR_DAY", "REPLACED"})
MAX_EXIT_ATTEMPTS = 50


def client_order_id_for(trade_id: str, intent: str, attempt: int = 0) -> str:
    key = trade_id if attempt == 0 else f"{trade_id}#{attempt}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"beo-trade:{key}:{intent}"))


def _finished(existing: BrokerOrderRecord | None, remote: dict | None) -> bool:
    """A replaced Alpaca order is done; its replacement is a separate order. pending_replace stays live."""
    if remote is not None:
        raw = str(remote.get("status") or "").lower()
        if raw == "replaced":
            return True
        if raw == "pending_replace":
            return False
        return str(remote.get("internal_state") or "").upper() in FINISHED_ORDER_STATES
    return existing is not None and str(existing.status or "").upper() in FINISHED_ORDER_STATES


def _order_is_live(existing: BrokerOrderRecord | None, remote: dict | None) -> bool:
    if remote is not None:
        raw = str(remote.get("status") or "").lower()
        if raw == "replaced":
            return False
        if raw == "pending_replace":
            return True
        state = str(remote.get("internal_state") or "").upper()
        return state in OPEN_ORDER_STATES or state == "PENDING_REPLACE"
    if existing is None:
        return False
    state = str(existing.status or "").upper()
    return state in OPEN_ORDER_STATES or state == "PENDING_REPLACE"


def _resolve_client_order_id(session: Session, adapter, trade_id: str, intent: str):
    """An exit id is reused only while its order is live. A trade id can be a contract
    symbol that was bought and sold before, so a finished sell must not block the next one."""
    attempts = MAX_EXIT_ATTEMPTS if intent == "sell_to_close" else 1
    for attempt in range(attempts):
        client_order_id = client_order_id_for(trade_id, intent, attempt)
        existing = order_by_client(session, client_order_id)
        try:
            remote = adapter.get_order_by_client_id(client_order_id)
        except Exception as exc:
            raise PaperOnlyError("מצב הפקודה לא ידוע. לא נשלחת פקודה נוספת.") from exc
        if not isinstance(remote, dict):
            remote = None
        if existing is None and remote is None:
            return client_order_id, None, None
        if intent != "sell_to_close" or not _finished(existing, remote):
            return client_order_id, existing, remote
    raise PaperOnlyError("יותר מדי ניסיונות יציאה לאותה עסקה")


def validate_order(
    settings: Settings,
    session: Session,
    account: dict,
    clock: dict,
    asset: dict | None,
    body_symbol: str,
    qty: int,
    limit_price: str,
    intent: str,
    approved: bool,
    recommendation_id: str,
    now: datetime,
    system_validated: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if settings.trading_mode.upper() != "PAPER":
        reasons.append("TRADING_MODE אינו PAPER")
    if not approved and not system_validated:
        reasons.append("נדרש אישור משתמש")
    if intent not in ALLOWED_INTENTS:
        reasons.append("כוונת הפוזיציה אינה buy_to_open או sell_to_close")
    if qty < 1:
        reasons.append("הכמות אינה תקינה")
    try:
        limit = Decimal(limit_price)
    except Exception:
        reasons.append("המחיר אינו תקין")
        limit = Decimal("0")
    if limit <= 0:
        reasons.append("המחיר אינו מקובל")
    contract = parse_option_symbol(body_symbol)
    if contract is None:
        reasons.append("החוזה אינו מזוהה")
    elif contract["expiration"] < clock_day(clock) and clock_day(clock):
        reasons.append("האופציה פגה")
    if str(account.get("status") or "").upper() != "ACTIVE" or account.get("trading_blocked") is True or account.get("account_blocked") is True:
        reasons.append("החשבון אינו זמין למסחר")
    if clock.get("is_open") is not True:
        reasons.append("השוק סגור לפי שעון Alpaca")
    if asset is not None:
        if asset.get("tradable") is False or str(asset.get("status") or "").lower() not in {"", "active"}:
            reasons.append("הנכס אינו סחיר אצל הברוקר")
        if asset.get("has_options") is False:
            reasons.append("אין אופציות על הנכס אצל הברוקר")
    if intent == "buy_to_open":
        recommendation = session.get(RecommendationRow, recommendation_id) if recommendation_id else None
        if recommendation is None or recommendation.decision != "BUY":
            reasons.append("אין המלצת BUY מאושרת")
        else:
            age = (now - _aware(recommendation.quote_observed_at)).total_seconds()
            if age < 0 or age > settings.trading().recommendation_max_age_seconds:
                reasons.append("ההמלצה אינה טרייה")
            gates = recommendation.gate_results if isinstance(recommendation.gate_results, dict) else {}
            if not gates or any(value is False for value in gates.values()):
                reasons.append("שער הסיכון לא עבר")
            drift = Decimal(str(settings.trading().revalidation_max_price_drift))
            ceiling = Decimal(str(recommendation.max_entry_price)) * (Decimal("1") + drift)
            if limit > ceiling:
                reasons.append("המחיר כבר אינו מקובל")
            if recommendation.option_symbol.replace(" ", "") != body_symbol.replace(" ", ""):
                reasons.append("החוזה אינו תואם להמלצה")
            if system_validated and qty > int(recommendation.quantity):
                reasons.append("הכמות חורגת ממגבלת הסיכון")
        buying_power = _decimal(account.get("options_buying_power") or account.get("buying_power"))
        needed = limit * Decimal(qty) * Decimal(settings.trading().contract_multiplier)
        if buying_power is None or needed > buying_power:
            reasons.append("אין כוח קנייה מספיק")
        if not system_validated:
            approval = session.scalar(select(ApprovalRecord).where(ApprovalRecord.recommendation_id == recommendation_id)) if recommendation_id else None
            if approval is None or approval.status != "APPROVED":
                reasons.append("אין אישור שמור")
    if open_order_for_symbol(session, body_symbol, intent) is not None:
        reasons.append("כבר קיימת פקודה פתוחה לחוזה הזה")
    return reasons


def fresh_quote_reasons(settings: Settings, symbol: str, intent: str, now: datetime, quotes: list[OptionSnapshot] | None, context: dict | None = None) -> list[str]:
    """A buy needs a real option row. Missing data blocks. Nothing is invented."""
    if intent != "buy_to_open":
        return []
    rows = quotes if quotes is not None else _live_option_quotes(settings, now)
    if not rows:
        return ["אין ציטוט טרי לחוזה"]
    match = next((row for row in rows if row.option_symbol.replace(" ", "") == symbol.replace(" ", "")), None)
    if match is None:
        return ["אין ציטוט טרי לחוזה"]
    reasons: list[str] = []
    age = (now - _aware(match.observed_at)).total_seconds()
    if age < 0 or age > settings.trading().min_data_freshness_seconds:
        reasons.append("הציטוט אינו טרי")
    if match.mid > 0 and (match.ask - match.bid) / match.mid > Decimal(str(settings.trading().max_bid_ask_spread)):
        reasons.append("המרווח כבר אינו מקובל")
    if match.volume < settings.trading().min_option_volume or match.open_interest < settings.trading().min_open_interest:
        reasons.append("הנזילות כבר אינה מספיקה")
    if context is None:
        return reasons
    if match.bid <= 0 or match.ask < match.bid or match.mid <= 0:
        reasons.append("הציטוט אינו שלם")
    if match.implied_volatility is None or match.implied_volatility <= 0:
        reasons.append("אין IV טרי")
    if context.get("news_fresh") is not True:
        reasons.append("החדשות אינן טריות")
    if context.get("regime_allowed") is not True:
        reasons.append("מצב השוק אינו מאפשר כניסה")
    if context.get("session_open") is not True:
        reasons.append("הסשן אינו פתוח")
    if context.get("entry_window_open") is False:
        reasons.append("ENTRY_CUTOFF: אין כניסות חדשות בדקות האחרונות לפני הסגירה")
    if context.get("exposure_ok") is not True:
        reasons.append("החשיפה אינה במגבלה")
    if context.get("daily_loss_ok") is not True:
        reasons.append("DAILY_LOSS_LIMIT_REACHED")
    if context.get("sector_ok") is not True:
        reasons.append("SECTOR_EXPOSURE_LIMIT")
    reference = context.get("reference_price")
    if reference not in {None, ""}:
        basis = Decimal(str(reference))
        if basis > 0:
            drift = abs(match.mid - basis) / basis
            if drift > Decimal(str(settings.trading().revalidation_max_price_drift)):
                reasons.append("המחיר השתנה מעבר למדיניות")
    return reasons


def _live_option_quotes(settings: Settings, now: datetime) -> list[OptionSnapshot] | None:
    from app.providers.base import ProviderNotConfigured, ProviderUnavailable
    from app.providers.registry import build_providers

    try:
        providers = build_providers(settings)
        if providers.options is None or providers.options.name == "unconfigured":
            return None
        return list(providers.options.load_options(now))
    except (ProviderNotConfigured, ProviderUnavailable, OSError):
        return None


def place_order(settings: Settings, session: Session, adapter, request: dict, now: datetime | None = None, quotes: list[OptionSnapshot] | None = None) -> dict:
    intent = str(request["position_intent"])
    blocked = order_blocked_by_halt(intent, is_halted())
    if blocked:
        raise PaperOnlyError(blocked)
    moment = now or datetime.now(timezone.utc)
    recommendation_id = str(request.get("recommendation_id") or "")
    stored = session.scalar(select(ApprovalRecord).where(ApprovalRecord.recommendation_id == recommendation_id)) if recommendation_id else None
    trade_id = str(request.get("trade_id") or (stored.trade_id if stored is not None else "") or uuid.uuid4())
    client_order_id, existing, remote = _resolve_client_order_id(session, adapter, trade_id, intent)
    if existing is not None or remote is not None:
        if intent == "sell_to_close" and not _order_is_live(existing, remote):
            from app.ops.failure import log_failure

            log_failure(
                "worker.exit_duplicate_without_live_order",
                RuntimeError("exit duplicate without live order"),
                service="worker",
                where="place_order",
                trade_id=trade_id,
                client_order_id=client_order_id,
            )
            record_trade_event(
                session,
                trade_id,
                "BLOCKED",
                "beo-trade",
                "EXIT_DUPLICATE_WITHOUT_LIVE_ORDER",
                str(request.get("recommendation_id") or ""),
            )
        record_trade_event(session, trade_id, "ENTRY_SUBMITTED" if intent == "buy_to_open" else "EXIT_SUBMITTED", "beo-trade", "פקודה קיימת לא נשלחה שוב", str(request.get("recommendation_id") or ""))
        return {"duplicate": True, "order": remote or {"client_order_id": client_order_id, "status": existing.status}, "trade_id": trade_id}
    account = adapter.get_account()
    clock = adapter.get_market_clock()
    symbol = str(request["symbol"]).replace(" ", "").upper()
    contract = parse_option_symbol(symbol)
    asset = None
    if contract is not None:
        try:
            asset = adapter.get_asset(contract["underlying"])
        except Exception:
            asset = None
    reasons = validate_order(
        settings,
        session,
        account,
        clock,
        asset,
        symbol,
        int(request["qty"]),
        str(request["limit_price"]),
        intent,
        bool(request.get("approved")),
        str(request.get("recommendation_id") or ""),
        moment,
        bool(request.get("system_validated")),
    )
    context = request.get("revalidation") if request.get("system_validated") else None
    if request.get("system_validated") and intent == "buy_to_open" and not isinstance(context, dict):
        reasons.append("אין בדיקה מחדש")
    reasons.extend(fresh_quote_reasons(settings, symbol, intent, moment, quotes, context if isinstance(context, dict) else None))
    if intent == "buy_to_open":
        from app.options.account_read import entry_block_reason
        from app.options.day_floor import day_close_reason

        unavailable = entry_block_reason()
        if unavailable:
            reasons.append(unavailable)
        closed_for = day_close_reason(settings, moment)
        if not closed_for and clock.get("is_open") is True:
            closed_for = _live_day_kill(settings, adapter, account, moment)
        if closed_for:
            reasons.append(closed_for)
        held = adapter.get_positions()
        if any(str(row.get("symbol") or "").replace(" ", "") == symbol and (_decimal(row.get("qty")) or Decimal("0")) > 0 for row in held):
            reasons.append("כבר קיימת פוזיציה פתוחה")
    if reasons:
        record_trade_event(session, trade_id, "BLOCKED", "beo-trade", "; ".join(reasons), str(request.get("recommendation_id") or ""))
        raise PaperOnlyError("; ".join(reasons))
    record_trade_event(session, trade_id, "APPROVED", "beo-trade", "אימות מערכת" if request.get("system_validated") else "אושר על ידי המשתמש", str(request.get("recommendation_id") or ""))
    record_trade_event(session, trade_id, "REVALIDATED", "alpaca-paper", "חשבון, שעון ומחיר נבדקו מחדש", str(request.get("recommendation_id") or ""))
    record_trade_event(session, trade_id, "RISK_OK", "beo-trade", "שערי הסיכון עברו", str(request.get("recommendation_id") or ""))
    payload = option_order(symbol, int(request["qty"]), str(request["limit_price"]), intent, client_order_id)
    if intent == "sell_to_close":
        order = adapter.submit_exit_order(payload)
        state = "EXIT_SUBMITTED"
    else:
        order = adapter.submit_entry_order(payload)
        state = "ENTRY_SUBMITTED"
    order["trade_id"] = trade_id
    session.add(
        BrokerOrderRecord(
            broker="alpaca",
            environment="PAPER",
            broker_order_id=str(order.get("broker_order_id") or ""),
            client_order_id=client_order_id,
            symbol=symbol,
            side=str(payload["side"]),
            position_intent=intent,
            status=str(order.get("internal_state") or "SUBMITTED"),
            recommendation_id=str(request.get("recommendation_id") or ""),
        )
    )
    record_trade_event(session, trade_id, state, "alpaca-paper", str(order.get("broker_order_id") or ""), str(request.get("recommendation_id") or ""))
    from app.broker.store import remember_orders

    remember_orders(session, [order])
    return {"duplicate": False, "order": order, "trade_id": trade_id}


def _live_day_kill(settings: Settings, adapter, account: dict, moment: datetime) -> str | None:
    """Block the buy and latch the day when the dollar kill or the percent hard stop is already reached."""
    from zoneinfo import ZoneInfo

    from app.analytics.finance import trading_day_pnl
    from app.options.day_floor import latch_hard_stop
    from app.options.risk_limits import day_flatten_reason

    if not hasattr(adapter, "get_activities") and not hasattr(adapter, "get_session_activities"):
        return None
    session_date = moment.astimezone(ZoneInfo(settings.exchange_timezone)).date()
    try:
        from app.analytics.finance import read_session_funding

        activities = read_session_funding(adapter, session_date)
    except Exception:
        return None
    from app.trades.exclusion import adjust_day_pnl

    try:
        held = adapter.get_positions()
    except Exception:
        held = []
    pnl = trading_day_pnl(account.get("equity"), account.get("last_equity"), activities, session_date)
    # Must pass session_date + activities so closed bug lots are stripped before
    # the percent hard stop / dollar kill decide to latch.
    pnl = adjust_day_pnl(
        pnl,
        held if isinstance(held, list) else [],
        session_date=session_date,
        activities=activities if isinstance(activities, list) else None,
    )
    equity = _decimal(account.get("equity"))
    if day_flatten_reason(equity, pnl, settings.trading()) != "DAILY_HARD_STOP":
        return None
    try:
        latch_hard_stop(settings, moment)
    except Exception:
        pass
    return "DAILY_HARD_STOP"


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
