from app.analytics.story import trade_story
from app.config.settings import TradingConfig

HOOD = {
    "symbol": "HOOD261009P00110000",
    "underlying": "HOOD",
    "right": "PUT",
    "expiration": "2026-10-09",
    "qty": "21",
    "entry_price": "2.25",
    "exit_price": "3.45",
    "pnl": "2520.00",
    "return_pct": "53.33",
    "held_minutes": 1306,
    "opened_at": "2026-10-07T19:51:56+00:00",
    "closed_at": "2026-10-08T17:38:14+00:00",
    "exit_reason": "TRAILING",
    "plan": {"peak_price": "4.10"},
}

RECOMMENDATION = {
    "thesis": "HOOD נע ירידה של 2.10% והחוזה פוט עבר את שערי הציטוט.",
    "catalyst": "המהלך במחיר המניה הוא הסיבה לכניסה.",
    "reason_codes": ["MOMENTUM", "LIQUIDITY", "NOT_A_CODE"],
    "underlying_price": "108.40",
    "ai_reviewed": True,
}


def test_a_closed_trade_explains_entry_and_trailing_exit_in_plain_words():
    story = trade_story(HOOD, RECOMMENDATION, TradingConfig())
    entry = " ".join(story["entry"])
    exit_text = " ".join(story["exit"])
    assert story["closed"] is True
    assert "הימור ש-HOOD ירד" in entry
    assert "HOOD נע ירידה של 2.10%" in entry
    assert "המניה זזה חזק בכיוון אחד" in entry
    assert "NOT_A_CODE" not in entry
    assert "ה-AI אישר" in entry
    assert "$4,725" in entry
    assert "1.35" in entry
    assert "4.10" in exit_text
    assert "15%" in exit_text
    assert "רווח של $2,520 (+53.33%)" in exit_text


def test_missing_records_are_said_plainly_not_invented():
    trade = dict(HOOD, exit_reason=None, plan=None)
    story = trade_story(trade, None, TradingConfig())
    assert any("לא נמצאה ההמלצה" in line for line in story["entry"])
    assert any("לא נמצא תיעוד לסיבת היציאה" in line for line in story["exit"])


def test_an_open_trade_lists_the_levels_that_would_close_it():
    trade = {key: value for key, value in HOOD.items() if key not in {"closed_at", "exit_price", "pnl", "return_pct", "exit_reason"}}
    trade["current_price"] = "3.10"
    story = trade_story(trade, RECOMMENDATION, TradingConfig())
    text = " ".join(story["exit"])
    assert story["closed"] is False
    assert "1.35" in text
    assert "3.15" in text
    assert "3.60" in text
    assert "3.10" in text
