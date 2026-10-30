"""Bug-trade exclusion: day P&L, stats, display $0, exits still see the lot."""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.trades.exclusion import (
    BUG_LABEL,
    SEED,
    adjust_day_pnl,
    annotate_display,
    annotate_trades,
    countable_positions,
    for_stats,
    matches_trade,
)


RULE = {
    "symbol": SEED["symbol"],
    "quantity": SEED["quantity"],
    "entry_price": SEED["entry_price"],
    "entry_after": SEED["entry_after"],
    "entry_before": SEED["entry_before"],
    "reason": SEED["reason"],
    "active": True,
}

META_OPEN = {
    "symbol": "META261030C00630000",
    "qty": 7,
    "avg_entry_price": "5.55",
    "unrealized_pl": "-3143.00",
    "unrealized_intraday_pl": "-2520.00",
    "opened_at": "2026-10-29T17:47:00+00:00",
}

META_CLOSED = {
    "symbol": "META261030C00630000",
    "quantity": 7,
    "entry_price": "5.55",
    "exit_price": "1.06",
    "pnl": "-3143.00",
    "net_pnl": "-3143.00",
    "gross_pnl": "-3143.00",
    "return_pct": "-80.90",
    "opened_at": "2026-10-29T17:47:00+00:00",
    "closed_at": "2026-10-30T13:30:22+00:00",
}

OTHER = {
    "symbol": "AMD261030C00100000",
    "qty": 1,
    "avg_entry_price": "2.00",
    "unrealized_pl": "50.00",
    "unrealized_intraday_pl": "50.00",
}


def test_seed_matches_open_and_closed_meta_lot():
    assert matches_trade(META_OPEN, [RULE])
    assert matches_trade(META_CLOSED, [RULE])
    assert not matches_trade(OTHER, [RULE])


def test_open_day_pnl_strips_intraday_unrealized():
    measured = Decimal("-2520.00")
    out = adjust_day_pnl(measured, [META_OPEN, OTHER], [RULE], session_date=date(2026, 10, 30))
    assert out == Decimal("0.00")


def test_closed_day_pnl_uses_seeded_session_piece():
    # Lot sold; no longer in positions. Account day P&L still carries -1827.
    measured = Decimal("-1827.00")
    out = adjust_day_pnl(measured, [], [RULE], session_date=date(2026, 10, 30))
    assert out == Decimal("0.00")


def test_closed_day_pnl_leaves_other_session_days_alone():
    measured = Decimal("2751.00")
    out = adjust_day_pnl(measured, [], [RULE], session_date=date(2026, 10, 29))
    assert out == Decimal("2751.00")


def test_countable_positions_drop_excluded_but_keep_others():
    rows = countable_positions([META_OPEN, OTHER], [RULE])
    assert len(rows) == 1
    assert rows[0]["symbol"] == OTHER["symbol"]


def test_for_stats_omits_closed_bug_trade():
    kept = for_stats([META_CLOSED, {**META_CLOSED, "symbol": "QQQ261030C00600000", "entry_price": "1.00", "pnl": "100.00"}], [RULE])
    assert len(kept) == 1
    assert kept[0]["symbol"].startswith("QQQ")


def test_annotate_forces_zero_pnl_and_bug_label():
    shown = annotate_display(META_CLOSED, [RULE])
    assert shown["bug_trade"] is True
    assert shown["excluded"] is True
    assert shown["bug_label"] == BUG_LABEL
    assert shown["pnl"] == "0.00"
    assert shown["net_pnl"] == "0.00"
    assert Decimal(shown["pnl"]) == 0

    open_shown = annotate_display(META_OPEN, [RULE])
    assert open_shown["unrealized_pl"] == "0.00"
    assert open_shown["unrealized_intraday_pl"] == "0.00"


def test_annotate_trades_keeps_row_visible():
    rows = annotate_trades([META_CLOSED], [RULE])
    assert len(rows) == 1
    assert rows[0]["bug_trade"] is True
    assert rows[0]["pnl"] == "0.00"


def test_does_not_double_count_when_still_open_on_exit_day():
    measured = Decimal("-2520.00")
    out = adjust_day_pnl(measured, [META_OPEN], [RULE], session_date=date(2026, 10, 30))
    # Open path only: -2520 - (-2520) = 0. Must not also apply -1827.
    assert out == Decimal("0.00")
