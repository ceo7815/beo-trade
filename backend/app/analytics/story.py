"""Plain-language account of one trade, built only from stored records and the exit policy."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.config.settings import TradingConfig

REASON_TEXT = {
    "MOMENTUM": "המניה זזה חזק בכיוון אחד",
    "TECHNICAL_CONFIRMATION": "גרף המחיר אישר את הכיוון",
    "UNUSUAL_VOLUME": "היה נפח מסחר חריג במניה",
    "NEWS_CATALYST": "הייתה ידיעה טרייה שתומכת בכיוון",
    "EVENT_DRIVEN": "יש אירוע שמניע את המניה",
    "LIQUIDITY": "החוזה נסחר הרבה והמרווח בין קנייה למכירה קטן",
    "IV_SETUP": "מחיר התנודתיות של החוזה היה סביר",
    "GAMMA_SETUP": "החוזה מגיב מהר לתנועה במניה",
    "DELTA_SETUP": "החוזה צמוד מספיק לתנועה של המניה",
    "SHORT_TERM_STRUCTURE": "המבנה של הדקות האחרונות תמך בכניסה",
}


def _dec(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _usd(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _price(value: Decimal) -> str:
    return f"{value:.2f}"


def _minutes(value: object) -> str:
    number = _dec(value)
    if number is None:
        return ""
    total = int(number)
    if total < 60:
        return f"{total} דקות"
    hours, minutes = divmod(total, 60)
    return f"{hours} שעות ו-{minutes} דקות" if minutes else f"{hours} שעות"


def entry_story(trade: dict, recommendation: dict | None, config: TradingConfig) -> list[str]:
    underlying = trade.get("underlying") or trade.get("symbol") or ""
    right = trade.get("right")
    expiration = trade.get("expiration") or ""
    entry = _dec(trade.get("entry_price"))
    qty = _dec(trade.get("qty"))
    lines: list[str] = []
    if right == "PUT":
        lines.append(f"קנינו PUT, כלומר הימור ש-{underlying} ירד עד {expiration}. אם המניה יורדת, החוזה מתייקר.")
    elif right == "CALL":
        lines.append(f"קנינו CALL, כלומר הימור ש-{underlying} יעלה עד {expiration}. אם המניה עולה, החוזה מתייקר.")
    if recommendation is None:
        lines.append("לא נמצאה ההמלצה השמורה שפתחה את העסקה, ולכן אין פירוט של הסיבות.")
    else:
        thesis = str(recommendation.get("thesis") or "").strip()
        if thesis:
            lines.append(f"מה ראינו: {thesis}")
        catalyst = str(recommendation.get("catalyst") or "").strip()
        if catalyst and catalyst != thesis:
            lines.append(f"הטריגר: {catalyst}")
        reasons = [REASON_TEXT[code] for code in recommendation.get("reason_codes") or [] if code in REASON_TEXT]
        if reasons:
            lines.append("מה עבר את הבדיקות: " + "; ".join(reasons) + ".")
        lines.append("ה-AI אישר את העסקה." if recommendation.get("ai_reviewed") else "העסקה נפתחה לפי החוקים הכמותיים, בלי אישור AI שמור.")
        moved = _dec(recommendation.get("underlying_price"))
        if moved is not None:
            lines.append(f"מחיר {underlying} ברגע ההחלטה: {_price(moved)}.")
    if entry is not None and qty is not None and entry > 0:
        stop = Decimal(str(config.initial_stop_decline_pct))
        multiplier = Decimal(config.contract_multiplier)
        invested = entry * qty * multiplier
        worst = invested * stop
        lines.append(f"השקענו {_usd(invested)}: {qty:.0f} חוזים במחיר {_price(entry)} לחוזה.")
        lines.append(f"הסיכון שתכננו: אם החוזה יורד ל-{_price(entry * (1 - stop))} ({stop * 100:.0f}% פחות), יוצאים. ההפסד המקסימלי המתוכנן הוא בערך {_usd(worst)}.")
    return lines


def exit_story(trade: dict, config: TradingConfig) -> list[str]:
    entry = _dec(trade.get("entry_price"))
    exit_price = _dec(trade.get("exit_price"))
    reason = trade.get("exit_reason")
    plan = trade.get("plan") or {}
    peak = _dec(plan.get("peak_price"))
    stop = Decimal(str(config.initial_stop_decline_pct))
    trail = Decimal(str(config.trailing_pct))
    lines: list[str] = []
    if reason == "TRAILING":
        if peak is not None and entry is not None and peak > entry:
            lines.append(f"החוזה עלה עד שיא של {_price(peak)}. מהשיא הזה המערכת נעלה רווח: אם המחיר יורד {trail * 100:.0f}% מהשיא, יוצאים.")
        else:
            lines.append(f"החוזה הרוויח מספיק כדי להפעיל סטופ נגרר, ואז ירד {trail * 100:.0f}% מהשיא שלו.")
        lines.append("לכן יצאנו ושמרנו את רוב הרווח, במקום לחכות ולהחזיר אותו.")
    elif reason == "TIME_STOP":
        lines.append(
            f"נגמר זמן ההחזקה המקסימלי: {_minutes(config.holding_minutes_1dte)} לחוזה שפוקע מחר, {_minutes(config.holding_minutes_0dte)} לחוזה שפוקע היום. "
            "חוזים קצרים מאבדים ערך מהר עם הזמן, ולכן לא מחזיקים אותם יותר מזה."
        )
    elif reason == "STOP":
        lines.append(f"החוזה ירד {stop * 100:.0f}% ממחיר הכניסה. זה הסטופ שתוכנן מראש, כדי שהפסד אחד לא יגדל.")
    elif reason == "PROTECTED_STOP":
        lines.append("העסקה הייתה ברווח, ולכן הסטופ הועלה קרוב למחיר הכניסה. המחיר חזר אליו ויצאנו כמעט בלי הפסד.")
    elif reason == "EXPIRATION":
        lines.append(f"החוזה פוקע היום או מחר, ולכן נסגר {config.exit_before_expiration_minutes} דקות לפני סוף המסחר, כדי לא להישאר איתו אחרי הסגירה.")
    elif reason == "SESSION_CLOSE":
        lines.append("יום המסחר עמד להסתיים, ולכן העסקה נסגרה לפני הסגירה.")
    elif reason == "INVALIDATION":
        lines.append(f"המניה זזה {config.invalidation_underlying_percent * 100:.0f}% נגד הכיוון שהימרנו עליו. הסיבה שבגללה נכנסנו כבר לא הייתה נכונה, אז יצאנו.")
    elif reason == "LIQUIDITY":
        lines.append("המסחר בחוזה נהיה דליל, או שהמרווח בין קנייה למכירה גדל מדי. יצאנו לפני שיהיה קשה למכור.")
    elif reason in {"KILL_SWITCH", "CLOSE_ALL"}:
        lines.append("כל הפוזיציות נסגרו בבקשה ידנית או בעצירת חירום.")
    else:
        lines.append("לא נמצא תיעוד לסיבת היציאה, ולכן אין כאן הסבר.")
    pnl = _dec(trade.get("pnl"))
    share = _dec(trade.get("return_pct"))
    if entry is not None and exit_price is not None and pnl is not None:
        result = "רווח" if pnl > 0 else "הפסד" if pnl < 0 else "איזון"
        held = _minutes(trade.get("held_minutes"))
        tail = f" אחרי {held}" if held else ""
        percent = f" ({share:+.2f}%)" if share is not None else ""
        lines.append(f"תוצאה: נכנסנו ב-{_price(entry)}, יצאנו ב-{_price(exit_price)}. {result} של {_usd(pnl)}{percent}{tail}.")
    return lines


def open_story(trade: dict, config: TradingConfig) -> list[str]:
    entry = _dec(trade.get("entry_price"))
    if entry is None or entry <= 0:
        return ["אין מחיר כניסה, ולכן אין חישוב של רמות היציאה."]
    one_r = entry * Decimal(str(config.initial_stop_decline_pct))
    protect = entry + one_r * Decimal(str(config.protect_at_r))
    protected_stop = entry - one_r * Decimal(str(config.protective_stop_r))
    trail_on = entry + one_r * Decimal(str(config.trail_activate_r))
    lines = [
        "העסקה עדיין פתוחה. מה יוציא אותה:",
        f"ירידה ל-{_price(entry - one_r)}: יציאה בהפסד מתוכנן.",
        f"עלייה ל-{_price(protect)}: הסטופ עולה ל-{_price(protected_stop)}, כמעט מחיר הכניסה.",
        f"עלייה ל-{_price(trail_on)}: מתחיל סטופ נגרר. יציאה אם המחיר יורד {config.trailing_pct * 100:.0f}% מהשיא.",
        f"זמן: עד {_minutes(config.holding_minutes_1dte)} לחוזה שפוקע מחר, ויציאה לפני סוף יום המסחר אם הוא פוקע היום או מחר.",
    ]
    current = _dec(trade.get("current_price"))
    if current is not None:
        lines.append(f"המחיר עכשיו {_price(current)}.")
    return lines


def trade_story(trade: dict, recommendation: dict | None, config: TradingConfig) -> dict:
    closed = bool(trade.get("closed_at"))
    return {
        "entry": entry_story(trade, recommendation, config),
        "exit": exit_story(trade, config) if closed else open_story(trade, config),
        "closed": closed,
    }
