"""Bug-trade exclusion: day P&L, stats, display $0, exits still see the lot."""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.options.risk_limits import day_flatten_reason, daily_loss_state
from app.config.settings import TradingConfig
from app.trades.exclusion import (
    BUG_LABEL,
    SEED,
    SEEDS,
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

ALL_RULES = [
    {
        "symbol": s["symbol"],
        "quantity": s["quantity"],
        "entry_price": s["entry_price"],
        "entry_after": s["entry_after"],
        "entry_before": s["entry_before"],
        "reason": s["reason"],
        "active": True,
    }
    for s in SEEDS
]

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

NVDA_CLOSED = {
    "symbol": "NVDA261102P00205000",
    "quantity": 32,
    "qty": 32,
    "entry_price": "1.51",
    "avg_entry_price": "1.51",
    "exit_price": "1.07",
    "pnl": "-1408.00",
    "opened_at": "2026-10-30T13:52:29+00:00",
    "closed_at": "2026-10-30T13:59:52+00:00",
}

SPY_CLOSED = {
    "symbol": "SPY261102P00760000",
    "quantity": 15,
    "qty": 15,
    "entry_price": "3.19",
    "avg_entry_price": "3.19",
    "exit_price": "2.58",
    "pnl": "-915.00",
    "opened_at": "2026-10-30T13:52:26+00:00",
    "closed_at": "2026-10-30T13:59:53+00:00",
}

OTHER = {
    "symbol": "AMD261030C00100000",
    "qty": 1,
    "avg_entry_price": "2.00",
    "unrealized_pl": "50.00",
    "unrealized_intraday_pl": "50.00",
}

OTHER_CLOSED_LOSER = {
    "symbol": "AMD261030C00100000",
    "quantity": 1,
    "entry_price": "2.00",
    "exit_price": "1.00",
    "pnl": "-100.00",
    "net_pnl": "-100.00",
    "gross_pnl": "-100.00",
    "opened_at": "2026-10-30T14:00:00+00:00",
    "closed_at": "2026-10-30T15:00:00+00:00",
}


def test_seed_matches_open_and_closed_meta_lot():
    assert matches_trade(META_OPEN, [RULE])
    assert matches_trade(META_CLOSED, [RULE])
    assert not matches_trade(OTHER, [RULE])


def test_seeds_cover_nvda_and_spy_bug_lots():
    assert matches_trade(NVDA_CLOSED, ALL_RULES)
    assert matches_trade(SPY_CLOSED, ALL_RULES)
    assert len(SEEDS) == 3


def test_open_day_pnl_strips_intraday_unrealized():
    measured = Decimal("-2520.00")
    out = adjust_day_pnl(measured, [META_OPEN, OTHER], [RULE], session_date=date(2026, 10, 30))
    assert out == Decimal("0.00")


def test_closed_overnight_meta_uses_seeded_day_impact():
    """(d) Excluded lot closed overnight — META case: prior-close→exit day piece only."""
    measured = Decimal("-1827.00")
    out = adjust_day_pnl(
        measured,
        [],
        [RULE],
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED],
    )
    assert out == Decimal("0.00")
    # Without closed_rows/activities: do not invent impact (stream / open-only marks).
    assert adjust_day_pnl(measured, [], [RULE], session_date=date(2026, 10, 30)) == measured
    # Activities sell on session day attaches seeded overnight day impact.
    activities = [
        {
            "symbol": META_CLOSED["symbol"],
            "side": "sell",
            "price": "1.06",
            "qty": 7,
            "timestamp": "2026-10-30T13:30:22+00:00",
        }
    ]
    out3 = adjust_day_pnl(
        measured, [], [RULE], session_date=date(2026, 10, 30), activities=activities
    )
    assert out3 == Decimal("0.00")


def test_closed_day_pnl_leaves_other_session_days_alone():
    measured = Decimal("2751.00")
    out = adjust_day_pnl(
        measured,
        [],
        [RULE],
        session_date=date(2026, 10, 29),
        closed_rows=[META_CLOSED],
    )
    assert out == Decimal("2751.00")


def test_stream_style_mark_without_session_date_is_untouched():
    # Stream mark path passes positions only — never apply closed seed impact.
    measured = Decimal("10000.00")
    assert adjust_day_pnl(measured, [], [RULE]) == measured
    assert adjust_day_pnl(measured, [META_OPEN], [RULE]) == measured - Decimal("-2520.00")


def test_same_day_round_trip_uses_full_pnl():
    row = {
        **META_CLOSED,
        "opened_at": "2026-10-30T14:00:00+00:00",
        "closed_at": "2026-10-30T15:00:00+00:00",
        "pnl": "-500.00",
    }
    # Custom rule without seed symbol so overnight impact does not attach.
    rule = {
        "symbol": "ZZZ261030C00100000",
        "quantity": 7,
        "entry_price": "5.55",
        "entry_after": None,
        "entry_before": None,
        "active": True,
    }
    row = {**row, "symbol": "ZZZ261030C00100000"}
    out = adjust_day_pnl(
        Decimal("-500.00"),
        [],
        [rule],
        session_date=date(2026, 10, 30),
        closed_rows=[row],
    )
    assert out == Decimal("0.00")


def test_a_excluded_lots_plus_non_excluded_loser_today_pnl_is_only_loser():
    """(a) Excluded META+NVDA+SPY + one non-excluded closed loser => today_pnl = that loser."""
    # Measured mark = sum of all four: -1827 + -1408 + -915 + -100 = -4250
    measured = Decimal("-4250.00")
    out = adjust_day_pnl(
        measured,
        [],
        ALL_RULES,
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED, NVDA_CLOSED, SPY_CLOSED, OTHER_CLOSED_LOSER],
    )
    # Exclusion strips only the three bug lots: -4250 - (-1827) - (-1408) - (-915) = -100
    assert out == Decimal("-100.00")


def test_b_hard_stop_and_kill_use_adjusted_pnl_not_excluded_lots():
    """(b) Hard stop / kill / floor see adjusted P&L; excluded lots alone do not fire."""
    trading = TradingConfig()
    # Large equity so percent stop needs a big loss; dollar kill at daily_kill_loss_usd.
    equity = Decimal("100000")
    # Raw mark would be -4150 (META day + NVDA + SPY) which trips a -3500 kill.
    raw = Decimal("-4150.00")
    adjusted = adjust_day_pnl(
        raw,
        [],
        ALL_RULES,
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED, NVDA_CLOSED, SPY_CLOSED],
    )
    assert adjusted == Decimal("0.00")
    assert day_flatten_reason(equity, raw, trading) == "DAILY_HARD_STOP"
    assert day_flatten_reason(equity, adjusted, trading) is None
    assert daily_loss_state(equity, adjusted, trading) == "ok"


def test_c_open_excluded_and_open_non_excluded():
    """(c) Open excluded lot + open non-excluded lot: only excluded intraday stripped."""
    excluded_open = {
        "symbol": "NVDA261102P00205000",
        "qty": 32,
        "avg_entry_price": "1.51",
        "unrealized_pl": "-1408.00",
        "unrealized_intraday_pl": "-1408.00",
        "opened_at": "2026-10-30T13:52:29+00:00",
    }
    other_open = {
        "symbol": "AMD261030C00100000",
        "qty": 1,
        "avg_entry_price": "2.00",
        "unrealized_pl": "-50.00",
        "unrealized_intraday_pl": "-50.00",
        "opened_at": "2026-10-30T14:00:00+00:00",
    }
    measured = Decimal("-1458.00")  # -1408 + -50
    out = adjust_day_pnl(
        measured,
        [excluded_open, other_open],
        ALL_RULES,
        session_date=date(2026, 10, 30),
    )
    assert out == Decimal("-50.00")
    kept = countable_positions([excluded_open, other_open], ALL_RULES)
    assert len(kept) == 1
    assert kept[0]["symbol"].startswith("AMD")


def test_all_three_seeds_zero_today_when_only_bug_lots():
    """After seeding META+NVDA+SPY, today_pnl with only those lots is 0."""
    measured = Decimal("-4150.00")  # -1827-1408-915
    out = adjust_day_pnl(
        measured,
        [],
        ALL_RULES,
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED, NVDA_CLOSED, SPY_CLOSED],
    )
    assert out == Decimal("0.00")


def test_exclusion_impact_is_per_lot_not_wipe():
    """Regression: never set today_pnl to measured−countable (wiping real losses)."""
    # Measured includes excluded META day piece (-1827) AND a real -500 loser elsewhere.
    measured = Decimal("-2327.00")
    out = adjust_day_pnl(
        measured,
        [],
        [RULE],
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED],
    )
    # Only META's -1827 stripped → -500 remains. Old inference wiped to 0.
    assert out == Decimal("-500.00")


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
    out = adjust_day_pnl(
        measured,
        [META_OPEN],
        [RULE],
        session_date=date(2026, 10, 30),
        closed_rows=[META_CLOSED],
    )
    # Open path only while still in positions: -2520 - (-2520) = 0.
    assert out == Decimal("0.00")


def test_nvda_spy_same_day_from_prices():
    measured = Decimal("-2323.00")
    out = adjust_day_pnl(
        measured,
        [],
        ALL_RULES,
        session_date=date(2026, 10, 30),
        closed_rows=[NVDA_CLOSED, SPY_CLOSED],
    )
    assert out == Decimal("0.00")
