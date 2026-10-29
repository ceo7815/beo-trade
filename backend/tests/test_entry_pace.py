"""Entry pace: a rolling cap, no side mix, and an opposite-side pause after a loss.

The gates apply to new buys only. State is read from stored orders and closed trades.
"""

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config.settings import Settings, load_trading_config
from app.positions.entry_pace import (
    ENTRY_RATE_CAP,
    POST_LOSS_OPPOSITE_BLOCK,
    SIDE_MIX_BLOCK,
    EntryStamp,
    LossStamp,
    PaceBook,
    entry_pace_reason,
    load_pace_book,
)
from app.workers.loops import _submit_autonomous_buys, close_all_positions, monitor_once

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 16, 11, 0, tzinfo=NY)
CALL = "AMD261030C00100000"
PUT = "TSLA261030P00400000"


def _config(**overrides):
    config = Settings(app_env="local", trading_mode="PAPER", auth_required=False).trading()
    return replace(config, **overrides) if overrides else config


def _entry(side: str, minutes_ago: float) -> EntryStamp:
    return EntryStamp(NOW - timedelta(minutes=minutes_ago), side)


def _loss(side: str, minutes_ago: float) -> LossStamp:
    return LossStamp(NOW - timedelta(minutes=minutes_ago), side)


def _book(entries=(), losses=()) -> PaceBook:
    return PaceBook(list(entries), list(losses), True)


def test_pace_defaults_match_the_study_window():
    config = load_trading_config()
    assert config.entry_pace_enabled is True
    assert config.entry_pace_window_minutes == 5
    assert config.entry_pace_max_entries == 2
    assert config.entry_pace_opposite_block_minutes == 30
    assert config.entry_rules_version == "v10.29-pacing"


def test_a_third_entry_inside_five_minutes_is_the_rate_cap():
    config = _config()
    two = _book([_entry("CALL", 1), _entry("PUT", 3)])
    assert entry_pace_reason("CALL", NOW, two, config) == ENTRY_RATE_CAP
    assert entry_pace_reason("PUT", NOW, two, config) == ENTRY_RATE_CAP
    assert entry_pace_reason("CALL", NOW, _book([_entry("CALL", 1)]), config) is None


def test_an_entry_exactly_five_minutes_ago_has_left_the_window():
    config = _config()
    edge = _book([EntryStamp(NOW - timedelta(minutes=5), "CALL"), _entry("CALL", 1)])
    assert entry_pace_reason("PUT", NOW, edge, config) == SIDE_MIX_BLOCK
    stale = _book([EntryStamp(NOW - timedelta(minutes=5), "CALL")])
    assert entry_pace_reason("PUT", NOW, stale, config) is None
    assert entry_pace_reason("CALL", NOW, _book([_entry("CALL", 6), _entry("PUT", 6)]), config) is None


def test_the_opposite_side_is_blocked_while_the_window_holds_one_entry():
    config = _config()
    calls = _book([_entry("CALL", 2)])
    assert entry_pace_reason("PUT", NOW, calls, config) == SIDE_MIX_BLOCK
    assert entry_pace_reason("CALL", NOW, calls, config) is None
    puts = _book([_entry("PUT", 4)])
    assert entry_pace_reason("CALL", NOW, puts, config) == SIDE_MIX_BLOCK
    assert entry_pace_reason("PUT", NOW, puts, config) is None


def test_the_rate_cap_wins_when_the_window_is_already_full_and_mixed():
    config = _config()
    full = _book([_entry("CALL", 1), _entry("CALL", 2)])
    assert entry_pace_reason("PUT", NOW, full, config) == ENTRY_RATE_CAP


def test_a_losing_close_blocks_only_the_opposite_side_for_thirty_minutes():
    config = _config()
    call_loss = _book(losses=[_loss("CALL", 10)])
    assert entry_pace_reason("PUT", NOW, call_loss, config) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("CALL", NOW, call_loss, config) is None
    assert entry_pace_reason("PUT", NOW, _book(losses=[_loss("CALL", 30)]), config) is None
    assert entry_pace_reason("CALL", NOW, _book(losses=[_loss("PUT", 29)]), config) == POST_LOSS_OPPOSITE_BLOCK


def test_a_later_loss_starts_its_own_thirty_minutes():
    config = _config()
    expired = _book(losses=[_loss("CALL", 40)])
    assert entry_pace_reason("PUT", NOW, expired, config) is None
    refreshed = _book(losses=[_loss("CALL", 40), _loss("CALL", 10)])
    assert entry_pace_reason("PUT", NOW, refreshed, config) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("CALL", NOW, refreshed, config) is None


def test_a_win_and_a_flat_close_do_not_block():
    from app.positions.entry_pace import losses_from_closed

    rows = [
        {"symbol": CALL, "right": "CALL", "pnl": "100.00", "closed_at": (NOW - timedelta(minutes=5)).isoformat()},
        {"symbol": PUT, "right": "PUT", "pnl": "0.00", "closed_at": (NOW - timedelta(minutes=6)).isoformat()},
        {"symbol": CALL, "right": "CALL", "pnl": "-0.01", "closed_at": (NOW - timedelta(minutes=4)).isoformat()},
    ]
    losses = losses_from_closed(rows, NOW, 30)
    assert len(losses) == 1
    assert losses[0].right == "CALL"
    assert entry_pace_reason("PUT", NOW, PaceBook([], losses), _config()) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("CALL", NOW, PaceBook([], losses), _config()) is None


def test_losses_on_both_sides_block_both_sides_until_each_window_passes():
    config = _config()
    both = _book(losses=[_loss("CALL", 5), _loss("PUT", 8)])
    assert entry_pace_reason("CALL", NOW, both, config) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("PUT", NOW, both, config) == POST_LOSS_OPPOSITE_BLOCK


def test_mix_and_post_loss_apply_to_different_sides_under_the_cap():
    config = _config()
    # One fresh CALL, and a PUT that lost ten minutes ago.
    book = PaceBook([_entry("CALL", 1)], [_loss("PUT", 10)])
    assert entry_pace_reason("PUT", NOW, book, config) == SIDE_MIX_BLOCK
    assert entry_pace_reason("CALL", NOW, book, config) == POST_LOSS_OPPOSITE_BLOCK


def test_the_same_scan_counts_a_buy_it_just_submitted():
    config = _config()
    live = PaceBook()
    assert entry_pace_reason("CALL", NOW, live, config) is None
    live.note_entry("CALL", NOW)
    assert entry_pace_reason("CALL", NOW, live, config) is None
    assert entry_pace_reason("PUT", NOW, live, config) == SIDE_MIX_BLOCK
    live.note_entry("CALL", NOW)
    assert entry_pace_reason("CALL", NOW, live, config) == ENTRY_RATE_CAP


def test_yesterday_and_the_other_side_of_midnight_do_not_count():
    config = _config()
    early = datetime(2026, 10, 16, 0, 2, tzinfo=NY)
    before_midnight = EntryStamp(datetime(2026, 10, 15, 23, 59, tzinfo=NY), "CALL")
    assert entry_pace_reason("PUT", early, PaceBook([before_midnight, before_midnight], []), config) is None
    late = datetime(2026, 10, 16, 21, 2, tzinfo=NY)
    same_evening = EntryStamp(datetime(2026, 10, 16, 20, 58, tzinfo=NY), "PUT")
    assert entry_pace_reason("CALL", late, PaceBook([same_evening], []), config) == SIDE_MIX_BLOCK
    old_loss = LossStamp(datetime(2026, 10, 15, 23, 50, tzinfo=NY), "CALL")
    assert entry_pace_reason("PUT", datetime(2026, 10, 16, 0, 10, tzinfo=NY), PaceBook([], [old_loss]), config) is None


def test_disabling_the_flag_lets_every_candidate_through():
    config = _config(entry_pace_enabled=False)
    crowded = PaceBook(
        [_entry("CALL", 1), _entry("PUT", 1), _entry("CALL", 2)],
        [_loss("CALL", 1), _loss("PUT", 1)],
        True,
    )
    assert entry_pace_reason("PUT", NOW, crowded, config) is None
    assert entry_pace_reason("CALL", NOW, None, config) is None


def test_each_knob_turns_off_only_its_rule():
    full = _book([_entry("CALL", 1), _entry("CALL", 2)])
    assert entry_pace_reason("CALL", NOW, full, _config(entry_pace_max_entries=0)) is None
    assert entry_pace_reason("PUT", NOW, _book([_entry("CALL", 1)]), _config(entry_pace_max_entries=0)) == SIDE_MIX_BLOCK
    assert entry_pace_reason("PUT", NOW, _book([_entry("CALL", 4)]), _config(entry_pace_window_minutes=3)) is None
    assert entry_pace_reason("CALL", NOW, _book([_entry("PUT", 1)]), _config(entry_pace_window_minutes=0)) is None
    assert entry_pace_reason("PUT", NOW, _book(losses=[_loss("CALL", 20)]), _config(entry_pace_opposite_block_minutes=15)) is None
    assert entry_pace_reason("PUT", NOW, _book(losses=[_loss("CALL", 10)]), _config(entry_pace_opposite_block_minutes=15)) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("PUT", NOW, _book(losses=[_loss("CALL", 1)]), _config(entry_pace_opposite_block_minutes=0)) is None


def test_a_missing_read_blocks_the_buy_and_a_disabled_gate_does_not():
    assert entry_pace_reason("CALL", NOW, None, _config()) == ENTRY_RATE_CAP
    assert entry_pace_reason("CALL", NOW, PaceBook(readable=False), _config()) == ENTRY_RATE_CAP
    assert entry_pace_reason("CALL", NOW, None, _config(entry_pace_enabled=False)) is None


def test_the_buy_path_records_the_pace_and_exits_do_not_read_it():
    source = inspect.getsource(_submit_autonomous_buys)
    assert "entry_pace_reason" in source
    assert "load_pace_book" in source
    assert "note_entry" in source
    exits = inspect.getsource(monitor_once) + inspect.getsource(close_all_positions)
    assert "entry_pace" not in exits
    assert "ENTRY_RATE_CAP" not in exits


def _database(tmp_path):
    from app.models.db import configure_database, init_db

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "pace.db").as_posix(),
        auto_create_tables=True,
    )
    configure_database(settings)
    init_db(settings)
    return settings


def test_stored_orders_survive_a_restart_including_pending_and_canceled(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerOrderRecord, BrokerOrderSnapshot

    _database(tmp_path)
    with session_scope() as session:
        session.add(BrokerOrderRecord(symbol=CALL, side="buy", position_intent="buy_to_open", status="filled", broker_order_id="filled-call", created_at=(NOW - timedelta(minutes=2)).astimezone(timezone.utc)))
        session.add(BrokerOrderRecord(symbol=PUT, side="buy", position_intent="buy_to_open", status="accepted", broker_order_id="pending-put", created_at=(NOW - timedelta(minutes=1)).astimezone(timezone.utc)))
        session.add(BrokerOrderRecord(symbol=CALL, side="buy", position_intent="buy_to_open", status="canceled", broker_order_id="dead-call", created_at=(NOW - timedelta(minutes=1)).astimezone(timezone.utc)))
        session.add(BrokerOrderRecord(symbol=PUT, side="sell", position_intent="sell_to_close", status="filled", broker_order_id="exit-put", created_at=(NOW - timedelta(minutes=1)).astimezone(timezone.utc)))
        session.add(BrokerOrderRecord(symbol=CALL, side="buy", position_intent="buy_to_open", status="filled", broker_order_id="stale-cancel", created_at=(NOW - timedelta(minutes=3)).astimezone(timezone.utc)))
        session.add(BrokerOrderSnapshot(broker_order_id="stale-cancel", symbol=CALL, internal_state="CANCELLED", payload={}, observed_at=NOW.astimezone(timezone.utc)))
        session.add(BrokerOrderRecord(symbol=PUT, side="buy", position_intent="buy_to_open", status="canceled", broker_order_id="revived", created_at=(NOW - timedelta(minutes=2)).astimezone(timezone.utc)))
        session.add(BrokerOrderSnapshot(broker_order_id="revived", symbol=PUT, internal_state="CANCELLED", payload={}, observed_at=(NOW - timedelta(minutes=2)).astimezone(timezone.utc)))
        session.add(BrokerOrderSnapshot(broker_order_id="revived", symbol=PUT, internal_state="FILLED", payload={}, observed_at=(NOW - timedelta(minutes=1)).astimezone(timezone.utc)))
        session.commit()
    book = load_pace_book(NOW, _config())
    assert book is not None and book.readable
    rights = sorted(item.right for item in book.entries)
    # filled CALL, pending PUT, and the revived PUT whose latest snapshot is FILLED.
    assert rights == ["CALL", "PUT", "PUT"]
    assert entry_pace_reason("CALL", NOW, book, _config()) == ENTRY_RATE_CAP


def test_a_losing_fill_and_a_losing_paper_trade_are_read_back(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerFillRecord, PaperTrade

    settings = _database(tmp_path)
    opened = (NOW - timedelta(minutes=40)).astimezone(timezone.utc)
    closed = (NOW - timedelta(minutes=12)).astimezone(timezone.utc)
    with session_scope() as session:
        session.add(
            BrokerFillRecord(
                execution_id="amd-buy",
                symbol=CALL,
                payload={"execution_id": "amd-buy", "symbol": CALL, "side": "buy", "qty": "1", "price": "2.00", "timestamp": opened.isoformat()},
                observed_at=opened,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="amd-sell",
                symbol=CALL,
                payload={"execution_id": "amd-sell", "symbol": CALL, "side": "sell", "qty": "1", "price": "1.00", "timestamp": closed.isoformat()},
                observed_at=closed,
            )
        )
        session.add(
            PaperTrade(
                id="paper-put",
                user_id=settings.dev_user_id,
                recommendation_id="rec-put",
                option_symbol=PUT,
                quantity=1,
                entry_price=2,
                entry_at=opened,
                exit_price=3,
                exit_at=(NOW - timedelta(minutes=9)).astimezone(timezone.utc),
                pnl_dollars=100,
                bid=2,
                ask=2.1,
                iv=0.4,
                delta=-0.4,
                gamma=0.01,
                theta=-0.02,
                vega=0.1,
                underlying_at_entry=400,
            )
        )
        session.add(
            PaperTrade(
                id="paper-call",
                user_id=settings.dev_user_id,
                recommendation_id="rec-call",
                option_symbol="QQQ261030C00600000",
                quantity=1,
                entry_price=1,
                entry_at=opened,
                exit_price=0.5,
                exit_at=(NOW - timedelta(minutes=8)).astimezone(timezone.utc),
                pnl_dollars=-50,
                bid=1,
                ask=1.1,
                iv=0.3,
                delta=0.4,
                gamma=0.01,
                theta=-0.02,
                vega=0.1,
                underlying_at_entry=600,
            )
        )
        session.commit()
    book = load_pace_book(NOW, _config())
    assert book is not None
    rights = sorted(item.right for item in book.losses)
    assert rights == ["CALL", "CALL"]
    assert entry_pace_reason("PUT", NOW, book, _config()) == POST_LOSS_OPPOSITE_BLOCK
    assert entry_pace_reason("CALL", NOW, book, _config()) is None


def test_an_unreadable_database_does_not_invent_an_empty_book(monkeypatch):
    monkeypatch.setattr("app.models.db.database_ready", lambda: False)
    assert load_pace_book(NOW, _config()) is None
