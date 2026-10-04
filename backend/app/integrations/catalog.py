from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    name: str
    category: str
    role: str
    group: str
    primary: bool = False
    secret_fields: tuple[str, ...] = ()
    environment: str = ""
    future_reason: str = ""


ACTIVE: tuple[IntegrationSpec, ...] = (
    IntegrationSpec("openai", "OpenAI", "AI", "מוח ההחלטה. Structured Output בלבד.", "active", True, ("openai_api_key",), "API"),
    IntegrationSpec("thetadata", "ThetaData", "Market Data", "מקור השוק הראשי לאופציות.", "active", True, ("thetadata_api_key",), "API"),
    IntegrationSpec("benzinga", "Benzinga", "News", "חדשות כדי להבין למה הנכס זז.", "active", True, ("benzinga_api_key",), "API"),
    IntegrationSpec("alpaca", "Alpaca", "Broker / Paper Execution", "שכבת ביצוע דמה בלבד. לא מקור השוק.", "active", True, ("alpaca_api_key", "alpaca_api_secret"), "PAPER"),
    IntegrationSpec("supabase", "Supabase", "Database", "מסד, היסטוריה, הרשאות ואודיט.", "active", True, ("database_url",), "DATABASE"),
    IntegrationSpec("redis", "Redis", "Infrastructure", "מטמון ותור מקומי. אין מפתח.", "active", False, (), "LOCAL"),
    IntegrationSpec("xcloud", "XCloud", "Infrastructure", "השרת הקיים. לא מקימים שרת חדש.", "active", True, (), "HOST"),
    IntegrationSpec("sec", "SEC EDGAR", "Regulatory", "מקור רשמי כשיש סתירה.", "active", True, (), "PUBLIC"),
    IntegrationSpec("fred", "FRED", "Macro", "סדרות מאקרו רלוונטיות בלבד.", "active", True, ("fred_api_key",), "API"),
    IntegrationSpec("quant", "Quant Engine", "Internal Intelligence", "חישובי יווניות, IV, נפח וסיכון בקוד.", "active", True, (), "INTERNAL"),
    IntegrationSpec("candidate", "Candidate Engine", "Internal Intelligence", "סריקת כל השוק. המשתמש רואה רק מה שעבר.", "active", True, (), "INTERNAL"),
    IntegrationSpec("risk", "Risk Engine", "Internal Intelligence", "גודל פוזיציה, מרווח ונזילות לפני אישור.", "active", True, (), "INTERNAL"),
    IntegrationSpec("regime", "Market Regime Engine", "Internal Intelligence", "SPY, QQQ, IWM, VIX ומצב תנודתיות.", "active", False, (), "INTERNAL"),
    IntegrationSpec("audit", "Decision Audit Engine", "Internal Intelligence", "מה היה ידוע, מה הוחלט ומה קרה אחר כך.", "active", True, (), "INTERNAL"),
    IntegrationSpec("backtest", "Backtesting Engine", "Internal Intelligence", "שיחזור היסטורי בלי נתוני עתיד.", "active", True, (), "INTERNAL"),
)

FUTURE: tuple[IntegrationSpec, ...] = (
    IntegrationSpec("databento", "Databento", "Market Data / Research", "נתוני שוק ומחקר מתקדמים, כולל OPRA.", "future", future_reason="ספק מחקר עתידי. ThetaData נשארת הראשית."),
    IntegrationSpec("massive", "Massive", "Market Data / Research", "ספק נתוני שוק חלופי.", "future", future_reason="לא מופעל. אין מפתח ואין חיבור."),
    IntegrationSpec("livevol", "Cboe LiveVol", "Options Analytics", "אנליטיקת אופציות מתקדמת.", "future", future_reason="ספק מחקר עתידי, לא מקור ביצוע."),
    IntegrationSpec("orats", "ORATS", "Options Analytics", "סורק ובדיקות היסטוריות לאופציות.", "future", future_reason="לא מופעל. ThetaData נשארת מקור השוק."),
    IntegrationSpec("fundamentals", "Fundamentals Provider", "Future Research", "דוחות ויחסים פונדמנטליים.", "future", future_reason="אין ספק שנבחר."),
    IntegrationSpec("calendar", "Economic Calendar Provider", "Future Research", "לוח אירועי מאקרו.", "future", future_reason="אין ספק שנבחר."),
    IntegrationSpec("web-research", "Advanced Web Research", "Future Research", "מחקר רשת מעבר לחדשות Benzinga.", "future", future_reason="אין ספק שנבחר."),
)

ALL = {item.id: item for item in (*ACTIVE, *FUTURE)}

INTERNAL = {"quant", "candidate", "risk", "regime", "audit", "backtest"}
