"""Read-only effective trading rules for the settings UI."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.capital import (
    configured_kill_pct,
    day_giveback_enabled,
    day_trail_arm_usd,
    day_trail_protect_usd,
    effective_kill_usd,
    equity_ils,
    ils_per_usd,
    ladder_basis_usd,
    profile_is_20k,
    resolve_tier,
    scaled_day_floor_steps,
    working_capital_usd,
)
from app.capital.session_lock import read_locked_open_equity
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
    from datetime import datetime, timezone

    from app.positions.entry_pace import call_block_clocks

    trading = settings.trading()
    rate = ils_per_usd(trading)
    wc = working_capital_usd(trading)
    opened = read_locked_open_equity()
    basis = opened if opened is not None else (equity if equity is not None else wc)
    tier = resolve_tier(trading, basis)
    kill = effective_kill_usd(trading, equity, open_equity=opened or basis)
    ny_clock, dubai_clock = call_block_clocks(int(trading.opening_call_block_until_minutes), datetime.now(timezone.utc))
    call_value = f"עד {ny_clock} ניו־יורק / {dubai_clock} דובאי"
    call_explain = (
        "CALL נחסם לפני 10:00 שעון ניו־יורק; PUT מותר. "
        "דובאי בלי שעון קיץ, אז אותה שעה היא 19:00 בדובאי בחורף ו־18:00 בקיץ. "
        f"לפי שעון ניו־יורק עכשיו: {dubai_clock} בדובאי."
    )
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
    v2610 = str(getattr(trading, "capital_profile", "") or "") == "v2610_40k"
    small_20k = profile_is_20k(trading)
    ladder_basis = ladder_basis_usd(trading, opened)
    if v2610:
        steps = tuple((Decimal(str(arm)), Decimal(str(lock))) for arm, lock in trading.day_floor_steps)
    elif small_20k:
        steps = scaled_day_floor_steps(trading, ladder_basis)
    else:
        steps = ()
    kill_pct = configured_kill_pct(trading) if small_20k else Decimal(str(tier.kill_pct))
    per_trade = Decimal("0.15") if small_20k else tier.per_trade_pct
    exposure = Decimal("1") if small_20k else tier.exposure_pct
    ladder_rules = []
    for index, (step_arm, step_floor) in enumerate(steps, start=1):
        ladder_rules.append(
            _rule(
                f"ladder_{index}",
                f"מדרגה {index}",
                f"שיא {_money(step_arm)}$ → רצפה {_money(step_floor)}$",
                "הרצפה רק עולה. נגיעה ברצפה סוגרת את כל הפוזיציות ועוצרת את היום.",
            )
        )
    if v2610:
        scale = Decimal(str(getattr(trading, "options2_scale", 0.40) or 0.40))
        call_value = "כבוי"
        call_explain = (
            "בשבוע 26–30.10 לא הייתה חסימת CALL לפני 10:00 שעון ניו־יורק. "
            "opening_call_block_until_minutes = 0."
        )
        sizing_rules = [
            _rule("risk", "סיכון לעסקה", _pct(trading.risk_per_trade_pct), "2% מההון החי, חלקי סטופ 40%. אותו אחוז כמו ב־b7f62cf."),
            _rule("per_trade", "תקרת עסקה", _pct(trading.normal_position_capital_pct), "7% מההון. תקרה קשיחה 10%.", _money(None if basis is None else basis * Decimal(str(trading.normal_position_capital_pct)))),
            _rule("hard_cap", "תקרה קשיחה", _pct(trading.hard_position_capital_pct), "אף עסקה לא עוברת 10% מההון."),
            _rule("exposure", "חשיפה מצטברת", _pct(trading.max_total_open_exposure_pct), "סך הפרמיה הפתוחה עד 35% מההון.", _money(None if basis is None else basis * Decimal(str(trading.max_total_open_exposure_pct)))),
            _rule("aggregate", "סיכון מצטבר", _pct(trading.max_aggregate_planned_risk_pct), "סך הסיכון המתוכנן עד 15% מההון."),
            _rule("sector", "מגבלת סקטור", _pct(trading.max_sector_exposure_pct), "10% לסקטור. שם בלי סקטור הוא דלי משלו."),
            _rule("max_positions", "מקסימום פוזיציות", str(trading.max_concurrent_positions), "עד 10 פוזיציות, אחת לכל נכס בסיס."),
            _rule("per_underlying", "פוזיציה אחת לנכס", str(trading.max_positions_per_underlying), "אחרי STOP או INVALIDATION הנכס חסום עד סוף הסשן."),
        ]
        brake_rules = [
            _rule(
                "kill",
                "קייל יומי",
                f"${_money(kill)} או {_pct(trading.daily_hard_stop_pct)}",
                "המוקדם מבין הפסד 1,400$ (3,500$ × 0.40) לבין הפסד 5% מההון. סוגר הכל וחוסם את היום.",
                _money(kill),
            ),
            _rule("warn", "אזהרה", _pct(trading.daily_warning_pct), "2.5% הפסד יומי. לא סוגר ולא חוסם."),
            _rule("entry_brake", "עצירת כניסות", _pct(trading.daily_entry_stop_pct), "הפסד 4% חוסם קנייה חדשה. המכירות נשארות."),
            *ladder_rules,
            _rule("day_trail", "טרייל יום", "כבוי", "לא היה בשבוע 26–30.10. הרצפה היא רק סולם הדולרים."),
            _rule(
                "scale",
                "מקדם סקייל",
                str(scale),
                f"מקור {getattr(trading, 'options2_source_commit', 'b7f62cf')} על 100,000$. החשבון הזה הוא {getattr(trading, 'options2_capital_usd', 40000)}$.",
            ),
        ]
        tier_rows = [
            {
                "id": "v2610_40k",
                "label": "אופציות 2 · 40,000$",
                "per_trade": "7% / קשיח 10%",
                "kill": "$1,400 או 5%",
                "arm": "1,600$",
                "protect": "רצפה 800$",
                "exposure": "35%",
                "provisional": False,
                "explain": "שחזור b7f62cf. אחוזים זהים. בלמי דולר × 0.40.",
            }
        ]
    elif small_20k:
        sizing_rules = [
            _rule("per_trade", "מגבלת עסקה", "15% קשיח", "אם חוזה אחד עולה יותר מ־15% מההון — מדלגים. אין חריגת חוזה אחד מעל התקרה.", _money(None if basis is None else basis * per_trade), _money(None if eq_ils is None else eq_ils * per_trade)),
            _rule("exposure", "חשיפה מצטברת", "100%", "סך הפרמיה הפתוחה יכול להגיע לכל ההון.", _money(None if basis is None else basis * exposure)),
            _rule("size_too_small", "SIZE_TOO_SMALL", "בלי חריגה", "חוזה מעל 15% לא נקנה, גם אם הוא החוזה היחיד."),
            _rule("sector", "מגבלת סקטור", _pct(trading.max_sector_exposure_pct), "תקרת פרמיה לסקטור אחד. בלי סקטור, כל שם הוא דלי משלו, והתקרה גבוהה מספיק לעסקת 15%."),
            _rule("max_positions", "מקסימום פוזיציות", str(trading.max_concurrent_positions), "לא יותר מעשר פוזיציות פתוחות."),
            _rule("per_underlying", "פוזיציה אחת לנכס", str(trading.max_positions_per_underlying), "לא שתי עסקאות פתוחות על אותו Underlying."),
        ]
        brake_rules = [
            _rule(
                "kill",
                "קייל יומי נעול",
                _pct(kill_pct),
                "אחוז מההון בפתיחת הסשן (last_equity), לא מההון אחרי ההפסד. לא מחושב מחדש במהלך היום.",
                _money(kill),
                _money(None if kill is None else kill * rate),
            ),
            _rule(
                "kill_lock",
                "בסיס הקייל",
                "נעול לפתיחה" if opened is not None else "הון עבודה עד הקריאה הראשונה",
                "הסכום בדולרים נשמר לקובץ הסשן ברגע שיש last_equity.",
                _money(ladder_basis),
            ),
            *ladder_rules,
            _rule(
                "day_trail",
                "טרייל יום 40%",
                _pct(trading.day_trail_pct) if day_giveback_enabled(trading) else "כבוי",
                "אחרי המדרגה הראשונה: אם רווח היום יורד 40% משיא היום, סוגרים הכול ואין כניסות עד מחר. הרצפה הגבוהה יותר — מדרגה או טרייל — היא זו שמגינה.",
            ),
            _rule(
                "entry_brake",
                "בלם כניסות",
                _pct(trading.daily_entry_stop_pct),
                "הפסד יום בשיעור הזה חוסם קנייה חדשה. הקייל הנעול סוגר את היום לפניו כשהחשבון פרוס.",
            ),
        ]
        tier_rows = [
            {
                "id": "20k",
                "label": "20,000 ₪",
                "per_trade": "15% קשיח",
                "kill": _pct(kill_pct),
                "arm": "מדרגה ראשונה",
                "protect": "טרייל 40%",
                "exposure": "100%",
                "provisional": False,
                "explain": "סולם ה־100K כפול הון הפתיחה חלקי 30,000$. הפרופיל 10K נשאר בקובץ trading_10k.toml.",
            }
        ]
    else:
        sizing_rules = [
            _rule("per_trade", "מגבלת עסקה", _pct(tier.per_trade_pct), "כמה מההון מותר להשקיע בעסקה אחת.", _money(None if basis is None else basis * tier.per_trade_pct), _money(None if eq_ils is None else eq_ils * tier.per_trade_pct)),
            _rule("exposure", "חשיפה מצטברת", _pct(tier.exposure_pct), "סך הפרמיה הפתוחה מול ההון החי.", _money(None if basis is None else basis * tier.exposure_pct)),
            _rule("size_too_small", "SIZE_TOO_SMALL", "חוזה אחד שלא נכנס", "אם חוזה בודד חורג ממגבלת העסקה — לא מגדילים כמות, מדלגים."),
            _rule("sector", "מגבלת סקטור", _pct(trading.max_sector_exposure_pct), "תקרת פרמיה לסקטור אחד מההון."),
            _rule("max_positions", "מקסימום פוזיציות", str(trading.max_concurrent_positions), "לא יותר ממספר זה של פוזיציות פתוחות במקביל."),
            _rule("per_underlying", "פוזיציה אחת לנכס", str(trading.max_positions_per_underlying), "לא שתי עסקאות פתוחות על אותו Underlying."),
        ]
        brake_rules = [
            _rule("day_trail", "טרייל יומי DAY_TRAIL", f"נדרך {_pct(tier.arm_pct)} · רצפה {_pct(tier.protect_pct)} · טרייל {_pct(trading.day_trail_pct)}", "מחליף את סולם המדרגות בפרופיל 10K. סיבת יציאה: DAY_TRAIL.", _money(arm), _money(None if arm is None else arm * rate)),
            _rule("protect_floor", "רצפה ראשונה", _pct(tier.protect_pct), "אחרי החימוש הרצפה לא יורדת מתחת לזה.", _money(protect)),
            _rule("kill", "קייל יומי (ראשון)", _pct(tier.kill_pct), "נעול להון בפתיחת הסשן, לא להון החי אחרי הפסד.", _money(kill), _money(None if kill is None else kill * rate)),
            _rule("daily_order", "סדר בלמי יום", f"קייל {_pct(tier.kill_pct)} ← אזהרה {_pct(trading.daily_warning_pct)} ← עצירת כניסה {_pct(trading.daily_entry_stop_pct)} ← הארד {_pct(trading.daily_hard_stop_pct)}", "הקייל ננעל בפתיחה. האחוזים האחרים נשארים מעל הקייל."),
        ]
        tier_rows = [
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
        ]

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
        "ladder": [{"arm": _money(arm), "floor": _money(lock)} for arm, lock in steps],
        "kill_locked": opened is not None,
        "kill_open_equity": _money(opened),
        "call_block_dubai": dubai_clock,
        "sections": {
            "account_sizing": sizing_rules,
            "entry": [
                _rule("premium", "רצועת פרמיה", f"{trading.min_option_price:.2f}–{trading.max_option_price:.2f}$", "רק אופציות בטווח המחיר הזה נכנסות."),
                _rule("opening_call", "חסימת CALL בפתיחה", call_value, call_explain),
                _rule(
                    "call_extended_at_high",
                    "בלי רדיפת CALL מתוח על השיא",
                    (
                        f"{'פעיל' if trading.call_extended_at_high_enabled else 'כבוי'}"
                        f" · תנועה ≥ {_pct(trading.call_extended_min_move_from_open)} מהפתיחה"
                        f" ומרחק < {_pct(trading.call_extended_max_dist_from_high)} מהשיא"
                    ),
                    "CALL נחסם רק כששני התנאים מתקיימים יחד (CALL_EXTENDED_AT_HIGH), עם אחוז התנועה ואחוז המרחק בפירוט. PUT לא נחסם. בלי מחיר פתיחה או בלי שיא היום אין חסימה. כיבוי: call_extended_at_high_enabled = false.",
                ),
                _rule(
                    "open_guard",
                    "גאפ הפתיחה של QQQ",
                    "09:30–10:10 ניו־יורק · תיקון 50% מ־09:45",
                    "OPEN_NO_FADE: עד 10:10 אין CALL אחרי גאפ למטה ואין PUT אחרי גאפ למעלה. OPEN_GAP_RETRACE: מ־09:45, אם QQQ כבר תיקן לפחות חצי מהגאפ, אין כניסה חדשה עד 10:10. בלי מחיר פתיחה או בלי סגירה קודמת אין חסימה (GAP_DATA_MISSING). פוזיציה פתוחה לא נסגרת. השער פעיל ב־20K ובאופציות 2.",
                ),
                _rule(
                    "add_brake",
                    "בלם כניסות על רבע מהקייל",
                    "25% מהקייל היומי הנעול",
                    "ADD_BRAKE: אין כניסה חדשה כש-P&L של היום (הון עכשיו פחות הון פתיחת הסשן, אותו מספר שבסיכון) יורד אל 25% מהקייל הנעול או מתחתיו. באופציות 2 הקייל 1,400$ והבלם ב־350$-. ב־20K הקייל הוא 3500/30000 מהון הפתיחה. פוזיציה פתוחה ממשיכה להיות מנוהלת. אם ה-P&L חוזר מעל הקו, כניסות חוזרות. אין מתג.",
                ),
                _rule(
                    "chase_atr",
                    "בלי רדיפה אחרי תנועה גדולה",
                    "0.65 × ATR של 14 ימים",
                    "CHASE_ATR: מועמד נחסם אם המניה כבר זזה מהפתיחה של היום יותר מ־0.65 פעמים ATR14 בכיוון העסקה. CALL: (מחיר − פתיחה) / ATR. PUT: (פתיחה − מחיר) / ATR. בלי ATR או בלי מחיר פתיחה אין חסימה. המספר move_atr נשמר בכל מועמד בסריקה.",
                ),
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
            "day_brakes": brake_rules,
            "tiers": tier_rows,
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
