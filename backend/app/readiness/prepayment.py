from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.config.settings import Settings
from app.control.halt import order_blocked_by_halt
from app.models.db import database_ready, session_scope
from app.models.tables import IntegrationCheck

ACTIVATION_STEPS = [
    "הפעל את ThetaData ושולם את ה-entitlement לציטוטים ולשרשראות.",
    "הפעל את Theta Terminal על המכונה שמריצה את ה-API.",
    "ודא ציטוט אמיתי של נכס בסיס.",
    "ודא שרשרת אופציות אמיתית עם bid, ask, IV ו-Greeks.",
    "הפעל את Benzinga production.",
    "ודא ידיעה חיה עם חותמת זמן וטיקר.",
    "ודא שמפתח OpenAI של production מוגדר בשרת בלבד.",
    "ודא שחשבון Alpaca הוא Paper ושדגל PAPER_ONLY ירוק.",
    "השאר את המערכת רצה בזמן סשן מזומן פתוח.",
    "ודא שסריקה שמרה מונים: יקום, נכסים, חוזים, חדשות, סיכון, AI.",
    "ודא החלטת AI אמיתית על מועמד שעבר את הסינון.",
    "ודא BUY אוטומטי ב-Paper, בלי אישור משתמש.",
    "ודא fill אמיתי.",
    "ודא פוזיציה רק אחרי ה-fill.",
    "ודא שיציאה אוטומטית נשלחת כ-sell_to_close.",
    "ודא fill של המכירה.",
    "ודא P&L ממומש מה-fills, בנפרד ממימון.",
    "ודא התאמה בין הספר הפנימי ל-Alpaca Paper.",
    "ודא שמסך בקרת הכספים מציג את אותם מספרים.",
]

PROVIDER_STATUS = {
    "connected": "CONNECTED",
    "no_data": "AVAILABLE",
    "blocked_by_entitlement": "BLOCKED_BY_ENTITLEMENT",
    "missing_key": "NOT_CONFIGURED",
    "disconnected": "FAILED",
    "error": "FAILED",
    "unknown": "NOT_CONFIGURED",
    "planned": "NOT_CONFIGURED",
}


def build_report(settings: Settings) -> dict:
    trading = settings.trading()
    equity = Decimal("100000")
    funding = trading_check()
    over_cap = Decimal("1") + equity * Decimal(str(trading.max_total_open_exposure_pct))
    from app.options.risk_limits import exposure_reason

    blocked_buy = order_blocked_by_halt("buy_to_open", True)
    allowed_sell = order_blocked_by_halt("sell_to_close", True)
    exposure = exposure_reason(equity, over_cap, Decimal("1"), trading)
    gates = [
        {"item": "paper_mode", "status": "PASS" if settings.trading_mode == "PAPER" else "FAIL"},
        {"item": "halt_blocks_new_buys", "status": "PASS" if blocked_buy else "FAIL"},
        {"item": "halt_allows_protective_sell", "status": "PASS" if allowed_sell is None else "FAIL"},
        {"item": "funding_separated_from_trading_pnl", "status": "PASS" if funding == Decimal("0") else "FAIL"},
        {"item": "exposure_recalculated", "status": "PASS" if exposure == "EXPOSURE_LIMIT" else "FAIL"},
    ]
    code_paths = [
        {"item": "autonomous_buy", "status": "IMPLEMENTED", "live_fill": "REQUIRES_MARKET_AND_THETA"},
        {"item": "autonomous_sell", "status": "IMPLEMENTED", "live_fill": "REQUIRES_MARKET_AND_THETA"},
        {"item": "close_all", "status": "IMPLEMENTED", "live_fill": "REQUIRES_QUOTE"},
    ]
    providers = _provider_rows()
    ready = all(row["status"] == "PASS" for row in gates)
    return {
        "pre_payment_ready": ready,
        "trading_mode": settings.trading_mode,
        "software": gates,
        "code_paths": code_paths,
        "providers": providers,
        "activation_steps": ACTIVATION_STEPS,
        "paper_limitation": "Paper execution does not reproduce market impact, queue position, information leakage, or live slippage.",
    }


def trading_check() -> Decimal | None:
    from app.analytics.finance import trading_day_pnl

    return trading_day_pnl(
        "110000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "10000", "date": "2026-10-02"}],
        date(2026, 10, 2),
    )


def _provider_rows() -> list[dict]:
    names = ("thetadata", "benzinga", "openai", "alpaca", "sec", "fred", "redis")
    stored: dict[str, IntegrationCheck] = {}
    if database_ready():
        with session_scope() as session:
            rows = session.scalars(select(IntegrationCheck)).all()
            for row in rows:
                current = stored.get(row.integration_id)
                if current is None or (row.checked_at and current.checked_at and row.checked_at > current.checked_at):
                    stored[row.integration_id] = row
    items = []
    for name in names:
        row = stored.get(name)
        raw = None if row is None else row.status
        items.append(
            {
                "id": name,
                "status": "NOT_CONFIGURED" if raw is None else PROVIDER_STATUS.get(raw, "FAILED"),
                "detail": None if row is None else row.detail,
            }
        )
    return items
