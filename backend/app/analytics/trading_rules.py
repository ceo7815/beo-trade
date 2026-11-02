"""Read-only effective trading rules for the settings UI."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.capital import (
    day_trail_arm_usd,
    day_trail_protect_usd,
    effective_kill_usd,
    equity_ils,
    ils_per_usd,
    resolve_tier,
    working_capital_usd,
)
from app.config.settings import Settings, TradingConfig, load_trading_config, CONFIG_DIR
from app.analytics.periods import public_periods


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def _pct(value: float | Decimal) -> str:
    return f"{(Decimal(str(value)) * Decimal('100')).quantize(Decimal('0.01'))}%"


def _rule(id: str, title: str, value: str, explain: str, dollars: str | None = None, ils: str | None = None) -> dict:
    row = {"id": id, "title": title, "value": value, "explain": explain}
    if dollars is not None:
        row["dollars"] = dollars
    if ils is not None:
        row["ils"] = ils
    return row


def build_trading_rules(settings: Settings, equity: Decimal | None = None) -> dict:
    trading = settings.trading()
    rate = ils_per_usd(trading)
    wc = working_capital_usd(trading)
    basis = equity if equity is not None else wc
    tier = resolve_tier(trading, basis)
    kill = effective_kill_usd(trading, basis)
    arm = day_trail_arm_usd(trading, basis)
    protect = day_trail_protect_usd(trading, basis)
    eq_ils = equity_ils(basis, trading) if basis is not None else None

    # FX source
    fx_source = "config"
    try:
        from app.capital.fx import live_ils_per_usd

        live = live_ils_per_usd()
        if live is not None and abs(live - rate) < Decimal("0.0001"):
            fx_source = "frankfurter.dev"
        elif live is not None:
            rate = live
            fx_source = "frankfurter.dev"
    except Exception:
        pass

    release = _release_version()
    periods = public_periods()

    # Comparison 100K profile from trading_100k.toml when present.
    classic_path = CONFIG_DIR / "trading_100k.toml"
    classic = load_trading_config(classic_path) if classic_path.exists() else TradingConfig(capital_profile="100k")

    active = {
        "profile": trading.capital_profile,
        "tier": {
            "name": tier.name,
            "provisional": tier.provisional,
            "per_trade_pct": float(tier.per_trade_pct),
            "kill_pct": float(tier.kill_pct),
            "arm_pct": float(tier.arm_pct),
            "protect_pct": float(tier.protect_pct),
            "exposure_pct": float(tier.exposure_pct),
            "day_trail": tier.day_trail,
        },
        "equity_usd": _money(basis),
        "equity_ils": _money(eq_ils),
        "ils_per_usd": str(rate),
        "fx_source": fx_source,
        "working_capital_ils": str(getattr(trading, "working_capital_ils", 0) or 0),
        "working_capital_usd": _money(wc),
        "sections": {
            "account_sizing": [
                _rule("per_trade", "מגבלת עסקה", _pct(tier.per_trade_pct), "כמה מההון מותר להשקיע בעסקה אחת.", _money(None if basis is None else basis * tier.per_trade_pct), _money(None if eq_ils is None else eq_ils * tier.per_trade_pct)),
                _rule("exposure", "חשיפה מצטברת", _pct(tier.exposure_pct), "סך הפרמיה הפתוחה מול ההון החי.", _money(None if basis is None else basis * tier.exposure_pct)),
                _rule("size_too_small", "SIZE_TOO_SMALL", "חוזה אחד שלא נכנס", "אם חוזה בודד חורג ממגבלת העסקה — לא מגדילים כמות, מדלגים."),
                _rule("sector", "מגבלת סקטור", _pct(trading.max_sector_exposure_pct), "תקרת פרמיה לסקטור אחד מההון."),
                _rule("max_positions", "מקסימום פוזיציות", str(trading.max_concurrent_positions), "לא יותר ממספר זה של פוזיציות פתוחות במקביל."),
                _rule("per_underlying", "פוזיציה אחת לנכס", str(trading.max_positions_per_underlying), "לא שתי עסקאות פתוחות על אותו Underlying."),
            ],
            "entry": [
                _rule("premium", "רצועת פרמיה", f"{trading.min_option_price:.2f}–{trading.max_option_price:.2f}$", "רק אופציות בטווח המחיר הזה נכנסות."),
                _rule("opening_call", "חסימת CALL בפתיחה", "עד 10:00 ניו־יורק / 17:00–19:00 דובאי (DST)", "CALL נחסם לפני 10:00 שעון ניו־יורק; PUT מותר. השעה בדובאי זזה עם שעון קיץ."),
                _rule("dte", "DTE", f"מועדף {trading.preferred_dte}, מקס׳ מועדף {trading.preferred_max_dte}, תקרה {trading.max_expiration_days}", "דירוג לפי ימי פקיעה; מעבר לתקרה נחסם."),
                _rule("pace", "קצב כניסות", f"עד {trading.entry_pace_max_entries} ב־{trading.entry_pace_window_minutes} דק׳", "בלי מיקס צדדים בחלון; אחרי הפסד הצד ההפוך מחכה."),
                _rule("regime", "משטר שוק", "פעיל במערכת", "כניסות מסוננות גם לפי מצב המשטר (regime) כשהוא זמין."),
            ],
            "per_trade_exits": [
                _rule("stop", "סטופ", _pct(trading.initial_stop_decline_pct), "יציאה כשהפרמיה יורדת בשיעור הזה מהכניסה."),
                _rule("thesis", "ביטול תזה", _pct(trading.invalidation_underlying_percent), "יציאה כשהמניה זזה נגד התזה."),
                _rule("trail", "טרייל לעסקה", f"+30% / {_pct(trading.trailing_pct)} מהשיא", "נדרך ב־+30% מהפרמיה (0.75R), מוותר 15% מהשיא."),
                _rule("protected", "סטופ מוגן", f"arm {trading.protect_at_r}R / lock {trading.protective_stop_r}R", "אחרי רווח מספיק נועלים רצפת הגנה."),
                _rule("session_exp", "סגירת סשן / פקיעה", f"סשן {trading.session_exit_minutes} דק׳; לפני פקיעה {trading.exit_before_expiration_minutes} דק׳", "סוגרים לפני הפעמון ולפני פקיעת החוזה."),
            ],
            "day_brakes": [
                _rule("day_trail", "טרייל יומי DAY_TRAIL", f"נדרך {_pct(tier.arm_pct)} · רצפה {_pct(tier.protect_pct)} · טרייל {_pct(trading.day_trail_pct)}", "מחליף את סולם המדרגות בפרופיל 10K. סיבת יציאה: DAY_TRAIL.", _money(arm), _money(None if arm is None else arm * rate)),
                _rule("protect_floor", "רצפה ראשונה", _pct(tier.protect_pct), "אחרי החימוש הרצפה לא יורדת מתחת לזה.", _money(protect)),
                _rule("kill", "קייל יומי (ראשון)", _pct(tier.kill_pct), "בלם ההפסד היומי הראשון — סוגר את היום לפני אזהרה/עצירת כניסה/הארד־סטופ האחוזיים.", _money(kill), _money(None if kill is None else kill * rate)),
                _rule("daily_order", "סדר בלמי יום", f"קייל {_pct(tier.kill_pct)} ← אזהרה {_pct(trading.daily_warning_pct)} ← עצירת כניסה {_pct(trading.daily_entry_stop_pct)} ← הארד {_pct(trading.daily_hard_stop_pct)}", "האחוזים מעבר לקייל מוגדרים גבוה יותר כדי שלא יירו קודם."),
            ],
            "tiers": [
                {
                    "id": "tier1",
                    "label": "עד 25,000 ₪",
                    "per_trade": "15%",
                    "kill": "8%",
                    "arm": "6%",
                    "protect": "4.5%",
                    "exposure": "100%",
                    "provisional": False,
                    "explain": "שכבת ברירת המחדל לחשבון ה־10K.",
                },
                {
                    "id": "tier2",
                    "label": "25,000–100,000 ₪",
                    "per_trade": "10%",
                    "kill": "6%",
                    "arm": "4.5%",
                    "protect": "3.375%",
                    "exposure": "100%",
                    "provisional": True,
                    "explain": "הערכה זמנית — arm/protect לפי יחס הקייל 6/8.",
                },
                {
                    "id": "tier3",
                    "label": "מעל 100,000 ₪",
                    "per_trade": "7%",
                    "kill": "3.5%",
                    "arm": "סולם 100K",
                    "protect": "סולם 100K",
                    "exposure": "35%",
                    "provisional": False,
                    "explain": "חוזר לכללי שולחן ה־100K (בלי DAY_TRAIL).",
                },
            ],
        },
        "compare_100k": {
            "per_trade": _pct(classic.normal_position_capital_pct),
            "exposure": _pct(classic.max_total_open_exposure_pct),
            "kill_usd": str(classic.daily_kill_loss_usd),
            "day_floor": "סולם מדרגות",
            "profile": classic.capital_profile,
        },
    }

    return {
        "environment": "PAPER",
        "available": True,
        "release": release,
        "trading_periods": periods,
        "active": active,
        "read_only": True,
    }


def _release_version() -> str:
    path = CONFIG_DIR / "releases.json"
    try:
        import json

        body = json.loads(path.read_text(encoding="utf-8"))
        rows = body.get("releases") or []
        return str(rows[0]["version"]) if rows else ""
    except Exception:
        return ""
