from __future__ import annotations

from app.schemas.domain import REASON_CODES

PROMPT_VERSION = "beo-trade-decision-v2"

SYSTEM_PROMPT = """
ROLE: מנתח מקצועי של שוק אופציות למסחר תוך-יומי בחוזה האופציה עצמו.
OBJECTIVE: לבחור חוזה אופציה אחד מתוך הרשימה שסופקה, או לדחות, למסחר דמה של דקות עד שעות.
RULE: החזר BUY רק אם כל הראיות שסופקו תומכות בכך. אחרת SUPPRESS.
RULE: אין להמציא נתונים, מחירים, יווניות או חדשות.
RULE: אין להסתמך על מידע שלא נכלל בקלט.
RULE: אם המידע לא מספיק, decision חייב להיות SUPPRESS.
RULE: ההסבר חייב להישען על הנתונים שסופקו.
RULE: המערכת לא מבצעת מסחר אמיתי ואינה מבטיחה תוצאה.
RULE: אסור להשתמש במילים בטוח, מובטח, ודאי, סיכון אפס, guaranteed, risk-free.
RULE: אין לציין אחוז הצלחה.
RULE: בחר option_symbol רק מתוך shortlist.
RULE: כתוב thesis, catalyst, risk, invalidation בעברית.
RULE: reason_codes חייבים להיות לפחות שניים ורק מהרשימה המותרת.
RULE: אין לקבוע כמות, גודל פוזיציה, יתרת חשבון, כוח קנייה, חשיפה, סטטוס פקודה, מחיר ביצוע או רווח והפסד.
RULE: option_price, max_entry_price ו-holding_window_minutes הם הד של הנתונים שסופקו, לא הוראת ביצוע.
RULE: אם איכות הנתונים אינה מספיקה, data_quality חייב להיות FAIL ו-decision חייב להיות SUPPRESS.
RULE: market_regime, אם קיים בקלט, הוא הקשר שוק בלבד. אין להמציא ערכי SPY, QQQ, IWM או VIX.
RULE: macro, אם קיים בקלט, הוא הקשר מאקרו בלבד. אין להמציא ריבית, תשואות או מרווח עקום.
RULE: research, אם קיים בקלט, הוא הקשר רגולטורי בלבד. אין להפוך דיווח SEC ל-BUY ואין להמציא טפסים או מספרי XBRL.
החזר JSON בלבד לפי הסכימה.
""".strip()


def decision_json_schema() -> dict:
    text = {"type": "string"}
    number = {"type": "number"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "decision",
            "underlying",
            "option_symbol",
            "call_put",
            "strike",
            "expiration",
            "option_price",
            "max_entry_price",
            "holding_window_minutes",
            "thesis",
            "catalyst",
            "risk",
            "invalidation",
            "reason_codes",
            "confidence",
            "risk_factors",
            "holding_window",
            "required_conditions",
            "data_quality",
        ],
        "properties": {
            "decision": {"type": "string", "enum": ["BUY", "SUPPRESS"]},
            "underlying": text,
            "option_symbol": text,
            "call_put": {"type": "string", "enum": ["CALL", "PUT"]},
            "strike": number,
            "expiration": text,
            "option_price": number,
            "max_entry_price": number,
            "holding_window_minutes": {"type": "integer"},
            "thesis": text,
            "catalyst": text,
            "risk": text,
            "invalidation": text,
            "reason_codes": {
                "type": "array",
                "items": {"type": "string", "enum": list(REASON_CODES)},
            },
            "confidence": number,
            "risk_factors": {"type": "array", "items": text},
            "holding_window": text,
            "required_conditions": {"type": "array", "items": text},
            "data_quality": {"type": "string", "enum": ["PASS", "FAIL"]},
        },
    }
