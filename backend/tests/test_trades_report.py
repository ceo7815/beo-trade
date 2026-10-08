from datetime import datetime, timezone
from decimal import Decimal

from app.ai.budget import make_ledger
from app.ai.telemetry import store_ai_request
from app.analytics.trades import round_trips, summarize
from app.config.settings import Settings
from app.models.db import configure_database, init_db, session_scope
from app.models.store import load_usage

FILLS = [
    ("2026-10-07T16:13:01Z", "AGQ261016P00065000", "buy", 20, "2.4"),
    ("2026-10-07T16:38:40Z", "KORU261016P00018000", "buy", 82, "0.6"),
    ("2026-10-07T16:38:41Z", "HOOD261009P00110000", "buy", 21, "2.33"),
    ("2026-10-07T16:46:34Z", "KORU261016P00018000", "sell", 82, "0.45"),
    ("2026-10-07T16:52:43Z", "JPM261016P00330000", "buy", 1, "7.4"),
    ("2026-10-07T17:16:38Z", "SCHW261016P00095000", "buy", 2, "2"),
    ("2026-10-07T18:00:19Z", "AGQ261016P00065000", "sell", 20, "2"),
    ("2026-10-07T19:31:57Z", "MSTR261009P00152500", "buy", 18, "2.29"),
    ("2026-10-07T19:45:47Z", "HOOD261009P00110000", "sell", 21, "2.28"),
    ("2026-10-07T19:45:48Z", "JPM261016P00330000", "sell", 1, "6.15"),
    ("2026-10-07T19:45:48.5Z", "MSTR261009P00152500", "sell", 18, "2.73"),
    ("2026-10-07T19:45:49Z", "SCHW261016P00095000", "sell", 2, "1.84"),
    ("2026-10-07T19:51:56Z", "HOOD261009P00110000", "buy", 21, "2.25"),
    ("2026-10-07T19:54:30Z", "MSTR261009P00152500", "buy", 17, "2.79"),
    ("2026-10-08T14:43:08Z", "SMCI261009P00043500", "buy", 18, "0.69"),
    ("2026-10-08T14:43:09Z", "MU261009P01070000", "buy", 1, "12.05"),
    ("2026-10-08T15:43:06Z", "SMCI261009P00043500", "sell", 18, "1.05"),
]


def _fills():
    return [
        {"execution_id": f"x{index}", "symbol": symbol, "side": side, "qty": str(qty), "price": price, "timestamp": stamp}
        for index, (stamp, symbol, side, qty, price) in enumerate(reversed(FILLS))
    ]


def test_paper_fills_pair_into_whole_trades_with_return_on_money_invested():
    positions = [
        {"symbol": "HOOD261009P00110000", "current_price": "2.55"},
        {"symbol": "MSTR261009P00152500", "current_price": "3.5"},
        {"symbol": "MU261009P01070000", "current_price": "9.3"},
    ]
    report = round_trips(_fills(), positions, now=datetime(2026, 10, 8, 16, 30, tzinfo=timezone.utc))
    closed = {row["symbol"]: row for row in report["closed"]}
    assert len(closed) == 7
    assert closed["AGQ261016P00065000"]["invested"] == "4800.00"
    assert closed["AGQ261016P00065000"]["pnl"] == "-800.00"
    assert closed["AGQ261016P00065000"]["return_pct"] == "-16.67"
    assert closed["AGQ261016P00065000"]["held_minutes"] == 107
    assert closed["SMCI261009P00043500"]["pnl"] == "648.00"
    assert closed["SMCI261009P00043500"]["return_pct"] == "52.17"
    assert closed["HOOD261009P00110000"]["pnl"] == "-105.00"
    assert closed["HOOD261009P00110000"]["right"] == "PUT"
    opened = {row["symbol"]: row for row in report["open"]}
    assert opened["HOOD261009P00110000"]["entry_price"] == "2.25"
    assert opened["HOOD261009P00110000"]["unrealized_pnl"] == "630.00"
    assert opened["MSTR261009P00152500"]["unrealized_pnl"] == "1207.00"
    assert opened["MU261009P01070000"]["return_pct"] == "-22.82"
    summary = summarize(report["closed"], report["open"], Decimal("1.50"))
    assert summary["realized_pnl"] == "-852.00"
    assert summary["unrealized_pnl"] == "1562.00"
    assert summary["total_pnl"] == "710.00"
    assert summary["net_after_ai"] == "708.50"
    assert summary["wins"] == 2 and summary["losses"] == 5
    assert summary["win_rate_pct"] == "28.57"
    assert summary["worst"]["symbol"] == "KORU261016P00018000"


def test_a_sell_without_its_buy_in_the_history_is_not_a_trade():
    report = round_trips([{"execution_id": "s", "symbol": "SPY261009C00700000", "side": "sell", "qty": "1", "price": "2", "timestamp": "2026-10-08T15:00:00Z"}])
    assert report["closed"] == [] and report["orphan_sells"] == 1


def test_dashboard_ai_cost_counts_the_calls_the_worker_logged(tmp_path):
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "usage.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    moment = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    with session_scope() as session:
        store_ai_request(session, {"model": "gpt-6.1-sol", "input_tokens": 1000, "output_tokens": 100, "estimated_cost": "0.003"}, moment)
        store_ai_request(session, {"model": "gpt-6.1-sol", "input_tokens": 1000, "cached_tokens": None, "output_tokens": 100}, moment)
        session.commit()
        entries = load_usage(session, datetime(2026, 10, 1, tzinfo=timezone.utc), make_ledger(settings).cost_of)
    assert len(entries) == 2
    assert sum((entry.cost for entry in entries), Decimal("0")) == Decimal("0.006")
