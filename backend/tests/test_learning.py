from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.analytics import learning
from app.analytics.learning import Point, Rules, current_rules, excursions, lab, simulate
from app.config.settings import Settings, TradingConfig

OPEN = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
CONFIG = TradingConfig()


def _trade(**overrides) -> dict:
    trade = {
        "symbol": "HOOD261009C00120000",
        "underlying": "HOOD",
        "right": "CALL",
        "strike": "120",
        "expiration": "2026-10-09",
        "qty": "1",
        "entry_price": "2",
        "exit_price": "3.3",
        "opened_at": OPEN.isoformat(),
        "closed_at": (OPEN + timedelta(minutes=25)).isoformat(),
        "pnl": "130",
        "plan": {"underlying_at_entry": "100"},
    }
    trade.update(overrides)
    return trade


def _path(bids: list[str], underlying: list[str] | None = None, step: int = 5) -> list[Point]:
    return [
        Point(OPEN + timedelta(minutes=step * index), Decimal(bid), None, Decimal(underlying[index]) if underlying else Decimal("100"), "monitor")
        for index, bid in enumerate(bids)
    ]


def test_trail_exits_fifteen_percent_off_the_peak_once_armed():
    outcome = simulate(_trade(), _path(["2.0", "2.4", "3.0", "4.0", "3.5", "3.3"]), current_rules(CONFIG), CONFIG)
    assert outcome["reason"] == "TRAILING"
    assert outcome["exit_price"] == "3.3"
    assert outcome["pnl"] == Decimal("130.0")
    assert outcome["complete"] is True


def test_initial_stop_fires_on_the_first_bid_below_it():
    outcome = simulate(_trade(), _path(["2.0", "1.5", "1.19", "3.0"]), current_rules(CONFIG), CONFIG)
    assert outcome["reason"] == "STOP"
    assert outcome["pnl"] == Decimal("-81.00")


def test_a_call_is_invalidated_when_the_underlying_falls_one_percent():
    outcome = simulate(_trade(), _path(["2.0", "1.9", "1.8"], ["100", "99.5", "98.9"]), current_rules(CONFIG), CONFIG)
    assert outcome["reason"] == "INVALIDATION"


def test_a_short_path_with_no_trigger_keeps_the_actual_exit_and_is_partial():
    trade = _trade(exit_price="2.15", closed_at=(OPEN + timedelta(minutes=15)).isoformat())
    outcome = simulate(trade, _path(["2.0", "2.1", "2.2"]), current_rules(CONFIG), CONFIG)
    assert outcome["reason"] == "AS_TRADED"
    assert outcome["complete"] is False
    assert outcome["pnl"] == Decimal("15.00")


def test_a_path_reaching_the_forced_exit_closes_at_that_bid():
    forced = learning.session_end(OPEN.date(), CONFIG.session_exit_minutes)
    points = _path(["2.0", "2.1"]) + [Point(forced + timedelta(minutes=1), Decimal("2.5"), None, Decimal("100"), "shadow")]
    outcome = simulate(_trade(), points, current_rules(CONFIG), CONFIG)
    assert outcome["reason"] == "SESSION_CLOSE"
    assert outcome["exit_price"] == "2.5"


def test_excursions_report_peak_trough_giveback_and_what_came_after_the_exit():
    points = _path(["2.0", "1.9", "3.0", "4.0", "3.5", "3.3", "5.0", "1.0"])
    body = excursions(_trade(), points, CONFIG)
    assert body["covered"] is True
    assert body["peak_price"] == "4.0"
    assert Decimal(body["peak_pct"]) == Decimal("100")
    assert body["peak_minutes"] == 15
    assert body["trough_price"] == "1.9"
    assert Decimal(body["giveback"]) == Decimal("70.0")
    assert Decimal(body["after_best_pct"]) == Decimal("150")
    assert Decimal(body["after_worst_pct"]) == Decimal("-50")


def test_without_marks_the_peak_comes_from_the_stored_position_state():
    body = excursions(_trade(plan={"peak_price": "3.6"}), [], CONFIG)
    assert body["covered"] is False
    assert body["peak_source"] == "position_state"
    assert Decimal(body["giveback"]) == Decimal("30.0")


def test_lab_ranks_rule_sets_and_marks_the_current_one():
    trades = [_trade(), _trade(symbol="MSTR261009C00300000", opened_at=(OPEN + timedelta(minutes=1)).isoformat())]
    paths = {
        learning._key(trades[0]): _path(["2.0", "2.4", "3.0", "4.0", "3.5", "3.3"]),
        learning._key(trades[1]): _path(["2.0", "1.5", "1.19"]),
    }
    loose = Rules(0.5, 0.5, -0.125, 0.8, 0.25, 0.0)
    result = lab(trades, paths, CONFIG, grid=[current_rules(CONFIG), loose])
    assert result["trades_covered"] == 2
    assert [row["rank"] for row in result["best"]] == [1, 2]
    assert result["current"]["current"] is True
    assert Decimal(result["current"]["pnl"]) == Decimal("49.00")


def test_backfill_stores_history_quotes_with_the_minute_underlying(tmp_path):
    from app.models.db import configure_database, init_db

    settings = Settings(app_env="local", database_url="sqlite:///" + (tmp_path / "learning.db").as_posix(), auto_create_tables=True, auth_required=False)
    configure_database(settings)
    init_db(settings)

    class Feed:
        def __init__(self):
            self.calls = []

        def _get(self, path, params, required=True):
            self.calls.append((path, params))
            if path == "/v3/option/history/quote":
                return [
                    {"timestamp": "2026-10-08T10:00:00.000", "bid": 2.0, "ask": 2.1},
                    {"timestamp": "2026-10-08T10:01:00.000", "bid": 2.2, "ask": 2.3},
                    {"timestamp": "2026-10-08T10:02:00.000", "bid": 0, "ask": 0.1},
                ]
            return [{"timestamp": "2026-10-08T10:00:00.000", "close": 100.5}]

    feed = Feed()
    learning.backfill(settings, [_trade()], feed=feed)
    option_call = feed.calls[0]
    assert option_call[1]["strike"] == "120.000"
    assert option_call[1]["right"] == "call"
    assert option_call[1]["expiration"] == "20261009"
    points = learning.load_paths([_trade()], CONFIG)[learning._key(_trade())]
    assert [str(point.bid) for point in points] == ["2.000000", "2.200000"]
    assert points[0].underlying == Decimal("100.500000")
    assert points[1].underlying is None
    assert {point.source for point in points} == {"history"}
