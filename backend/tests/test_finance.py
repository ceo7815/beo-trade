from datetime import datetime, timezone
from decimal import Decimal

from app.analytics.finance import drawdown_from_equity, filter_trades, summarize_trades, today_pnl


def test_today_pnl_needs_both_equity_marks():
    assert today_pnl(None, "100") is None
    assert today_pnl("100", None) is None
    assert today_pnl("98", "100") == "-2.00"


def test_empty_trades_do_not_invent_a_win_rate():
    body = summarize_trades([])
    assert body["enough"] is False
    assert body["note"] == "אין מספיק מידע לניתוח"


def test_trade_stats_split_wins_and_losses():
    trades = [
        {"pnl": "30", "return_pct": "0.15", "right": "CALL", "underlying": "NVDA", "symbol": "NVDA", "dte": 0, "bucket": "0DTE"},
        {"pnl": "-10", "return_pct": "-0.05", "right": "PUT", "underlying": "AAPL", "symbol": "AAPL", "dte": 1, "bucket": "1DTE"},
    ]
    body = summarize_trades(trades)
    assert body["net"] == "20.00"
    assert body["count"] == 2
    assert Decimal(body["win_rate"]) == Decimal("0.5000")
    calls = filter_trades(trades, side="CALL")
    assert len(calls) == 1
    losses = filter_trades(trades, outcome="loss")
    assert losses[0]["underlying"] == "AAPL"
    zero = filter_trades(trades, dte="0DTE")
    assert zero[0]["underlying"] == "NVDA"


def test_drawdown_from_real_equity_points():
    empty = drawdown_from_equity([{"equity": 100}])
    assert empty["enough"] is False
    body = drawdown_from_equity([{"equity": 100}, {"equity": 120}, {"equity": 90}])
    assert body["maximum"] == "30.00"
    assert body["current"] == "30.00"


def test_funding_jump_is_not_reported_as_profit():
    from app.analytics.finance import period_cards

    cards = period_cards(
        [
            {"timestamp": 1, "equity": 0, "profit_loss": 0},
            {"timestamp": 2, "equity": 100000, "profit_loss": 0},
            {"timestamp": 3, "equity": 100000, "profit_loss": 0},
        ]
    )
    assert cards["cumulative"] == "0.00"
    assert cards["yesterday"] == "0.00"
    from app.analytics.finance import period_cards

    assert period_cards([{"timestamp": 1, "equity": 100}])["cumulative"] is None
    cards = period_cards(
        [
            {"timestamp": 1, "equity": 100},
            {"timestamp": 2, "equity": 110},
            {"timestamp": 90, "equity": 90},
        ]
    )
    assert cards["yesterday"] == "10.00"
    assert cards["cumulative"] == "-10.00"
    trades = [{"pnl": "1", "return_pct": "0.01", "symbol": "QQQ", "underlying": "QQQ", "right": "CALL"}]
    assert filter_trades(trades, symbol="nvda") == []
    assert len(filter_trades(trades, symbol="QQQ")) == 1
    start = datetime(2026, 10, 3, tzinfo=timezone.utc)
    dated = [{**trades[0], "closed_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}]
    assert filter_trades(dated, start=start) == []
