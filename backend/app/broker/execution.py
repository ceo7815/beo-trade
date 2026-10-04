from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.broker.normalize import clock_day, parse_option_symbol
from app.control.halt import is_halted, order_blocked_by_halt
from app.broker.store import open_order_for_symbol, order_by_client, record_trade_event
from app.config.settings import Settings
from app.integrations.alpaca import ALLOWED_INTENTS, PaperOnlyError, option_order
from app.models.tables import ApprovalRecord, BrokerOrderRecord, RecommendationRow
from app.schemas.domain import OptionSnapshot


def client_order_id_for(trade_id: str, intent: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"beo-trade:{trade_id}:{intent}"))


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
    client_order_id = client_order_id_for(trade_id, intent)
    existing = order_by_client(session, client_order_id)
    try:
        remote = adapter.get_order_by_client_id(client_order_id)
    except Exception as exc:
        raise PaperOnlyError("מצב הפקודה לא ידוע. לא נשלחת פקודה נוספת.") from exc
    if not isinstance(remote, dict):
        remote = None
    if existing is not None or remote is not None:
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
