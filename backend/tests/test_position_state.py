from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config.settings import TradingConfig
from app.paper_trading.engine import exit_signal
from app.positions.admission import admit_buy
from app.positions.state import apply_quote, load_open_state, open_state
from app.schemas.domain import OptionRight, OptionSnapshot, UnderlyingSnapshot
from app.broker.normalize import execution_pnl

NOW = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
EQUITY = Decimal("100000")


def _option(bid: str, ask: str, expiration=None) -> OptionSnapshot:
    return OptionSnapshot(
        "NVDA",
        "NVDA261002C00100000",
        OptionRight.CALL,
        Decimal("100"),
        expiration or NOW.date(),
        Decimal(bid),
        Decimal(ask),
        Decimal(ask),
        800,
        500,
        NOW,
        Decimal("0.4"),
        Decimal("0.4"),
        Decimal("0.02"),
        Decimal("-0.02"),
        Decimal("0.1"),
    )


def _underlying(price: str = "100") -> UnderlyingSnapshot:
    return UnderlyingSnapshot("NVDA", Decimal(price), Decimal(price), Decimal(price), Decimal(price), Decimal(price), 2_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal(price), NOW, True)


def _settings(tmp_path):
    from app.config.settings import Settings

    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "state.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )


def test_peak_never_resets_across_monitor_cycles():
    config = TradingConfig()
    from types import SimpleNamespace

    row = SimpleNamespace(
        peak_option_price=5,
        peak_time=NOW,
        trailing_peak=5,
        trailing_trigger=4.25,
        entry_fill_price=5,
        entry_fill_quantity=1,
        one_r_amount=200,
        entry_time=NOW,
        protected_mode=False,
        trailing_active=False,
        winner_run_mode=False,
        current_option_bid=None,
        current_option_ask=None,
        current_underlying_price=None,
        current_iv=None,
        current_spread=None,
        current_unrealized_pnl=None,
        current_holding_minutes=None,
    )
    apply_quote(row, bid=Decimal("5"), ask=Decimal("5.10"), underlying_price=Decimal("100"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.peak_option_price == 5
    apply_quote(row, bid=Decimal("6"), ask=Decimal("6.10"), underlying_price=Decimal("101"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.peak_option_price == 6
    apply_quote(row, bid=Decimal("5.50"), ask=Decimal("5.60"), underlying_price=Decimal("100"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.peak_option_price == 6
    apply_quote(row, bid=Decimal("7"), ask=Decimal("7.10"), underlying_price=Decimal("102"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.peak_option_price == 7


def test_r_modes_do_not_sell_only_because_of_two_r():
    config = TradingConfig()
    from types import SimpleNamespace

    row = SimpleNamespace(
        peak_option_price=5,
        peak_time=NOW,
        trailing_peak=5,
        trailing_trigger=4.25,
        entry_fill_price=5,
        entry_fill_quantity=1,
        one_r_amount=200,
        entry_time=NOW,
        protected_mode=False,
        trailing_active=False,
        winner_run_mode=False,
        current_option_bid=None,
        current_option_ask=None,
        current_underlying_price=None,
        current_iv=None,
        current_spread=None,
        current_unrealized_pnl=None,
        current_holding_minutes=None,
        trade_id="t",
        symbol="NVDA261002C00100000",
        entry_underlying_price=100,
        entry_delta=0.45,
        entry_iv=0.4,
        initial_stop_price=3,
        protected_stop_price=4.5,
    )
    apply_quote(row, bid=Decimal("7"), ask=Decimal("7.10"), underlying_price=Decimal("100"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.protected_mode is True
    apply_quote(row, bid=Decimal("8"), ask=Decimal("8.10"), underlying_price=Decimal("100"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.trailing_active is True
    apply_quote(row, bid=Decimal("9"), ask=Decimal("9.10"), underlying_price=Decimal("100"), iv=Decimal("0.4"), now=NOW, config=config)
    assert row.winner_run_mode is True
    from app.positions.state import fill_from_state, locked_levels

    option = _option("9.00", "9.10", expiration=NOW.date() + timedelta(days=3))
    signal = exit_signal(fill_from_state(row), option, _underlying(), NOW, config, True, locked=locked_levels(row))
    assert signal is None


def test_state_survives_a_new_session(tmp_path):
    from app.models.db import configure_database, init_db, session_scope

    settings = _settings(tmp_path)
    configure_database(settings)
    init_db(settings)
    config = TradingConfig()
    with session_scope() as session:
        row = open_state(
            session,
            trade_id="trade-1",
            symbol="NVDA261002C00100000",
            underlying="NVDA",
            entry_price=Decimal("5"),
            quantity=1,
            entry_time=NOW - timedelta(minutes=10),
            config=config,
            entry_underlying=Decimal("100"),
            entry_delta=Decimal("0.45"),
        )
        apply_quote(row, bid=Decimal("6"), ask=Decimal("6.10"), underlying_price=Decimal("101"), iv=Decimal("0.4"), now=NOW, config=config)
        session.commit()
        assert row.trailing_active is False
    with session_scope() as session:
        loaded = load_open_state(session, "NVDA261002C00100000")
        assert loaded.entry_time.replace(tzinfo=timezone.utc) == NOW - timedelta(minutes=10)
        assert loaded.peak_option_price == 6
        assert Decimal(str(loaded.one_r_amount)) == Decimal("200")
        assert loaded.entry_underlying_price == 100
        assert Decimal(str(loaded.entry_delta)) == Decimal("0.45")


def test_holding_time_and_invalidation_use_stored_entry(tmp_path):
    from app.models.db import configure_database, init_db, session_scope
    from app.positions.state import fill_from_state, locked_levels
    from app.workers.loops import monitor_once

    settings = _settings(tmp_path)
    configure_database(settings)
    init_db(settings)
    config = TradingConfig()
    symbol = "NVDA261002C00100000"
    with session_scope() as session:
        open_state(
            session,
            trade_id="trade-hold",
            symbol=symbol,
            underlying="NVDA",
            entry_price=Decimal("5"),
            quantity=1,
            entry_time=NOW - timedelta(minutes=91),
            config=config,
            entry_underlying=Decimal("100"),
            entry_delta=Decimal("0.45"),
        )
        session.commit()

    class Adapter:
        def sync_positions(self):
            return [{"symbol": symbol, "qty": "1", "avg_entry_price": "5", "trade_id": "trade-hold"}]

        def get_market_clock(self):
            return {"is_open": True}

        def close(self):
            return None

    held = monitor_once(settings, {symbol: {"option": _option("5.10", "5.20"), "underlying": _underlying()}}, Adapter(), NOW, submit=False)
    assert held["exits"][0]["reason"] == "TIME_STOP"
    later = "NVDA261003C00100000"
    with session_scope() as session:
        open_state(
            session,
            trade_id="trade-1dte",
            symbol=later,
            underlying="NVDA",
            entry_price=Decimal("5"),
            quantity=1,
            entry_time=NOW - timedelta(minutes=181),
            config=config,
            entry_underlying=Decimal("100"),
            entry_delta=Decimal("0.45"),
            expiration=(NOW + timedelta(days=1)).date().isoformat(),
            dte_at_entry=1,
        )
        session.commit()

    class Later:
        def sync_positions(self):
            return [{"symbol": later, "qty": "1", "avg_entry_price": "5", "trade_id": "trade-1dte"}]

        def get_market_clock(self):
            return {"is_open": True}

        def close(self):
            return None

    one = monitor_once(
        settings,
        {later: {"option": _option("5.10", "5.20", expiration=(NOW + timedelta(days=1)).date()), "underlying": _underlying()}},
        Later(),
        NOW,
        submit=False,
    )
    assert one["exits"][0]["reason"] == "TIME_STOP"
    with session_scope() as session:
        loaded = load_open_state(session, symbol)
        loaded.entry_time = NOW
        session.commit()
        fill = fill_from_state(loaded)
        locked = locked_levels(loaded)
    option = _option("5.10", "5.20", expiration=NOW.date() + timedelta(days=3))
    signal = exit_signal(fill, option, _underlying("98"), NOW, config, True, locked=locked)
    assert signal.reason == "INVALIDATION"


def test_aggregate_and_underlying_block_the_buy():
    config = TradingConfig()
    option = _option("4.90", "5.00", expiration=NOW.date() + timedelta(days=2))
    positions = [{"symbol": "NVDA261002C00100000", "qty": "1", "avg_entry_price": "5"}]
    blocked = admit_buy(option, EQUITY, 10, config, positions=positions, orders=[], state_risk={}, open_premium=Decimal("500"), sector_premium=Decimal("0"), buying_power=EQUITY, open_positions=1)
    assert blocked.quantity == 0
    assert blocked.reason == "UNDERLYING_POSITION_LIMIT"
    pending = admit_buy(
        option,
        EQUITY,
        10,
        config,
        positions=[],
        orders=[{"symbol": "NVDA261002P00100000", "qty": "1", "limit_price": "5", "position_intent": "buy_to_open", "internal_state": "SUBMITTED"}],
        state_risk={},
        open_premium=Decimal("0"),
        sector_premium=Decimal("0"),
        buying_power=EQUITY,
        open_positions=0,
    )
    assert pending.reason == "UNDERLYING_POSITION_LIMIT"
    capped = admit_buy(option, EQUITY, 10, config, positions=[], orders=[], state_risk={"OTHER": Decimal("15000")}, open_premium=Decimal("0"), sector_premium=Decimal("0"), buying_power=EQUITY, open_positions=1)
    assert capped.quantity == 0
    assert capped.reason == "AGGREGATE_RISK_LIMIT"


def test_admitted_quantity_stays_inside_every_cap():
    config = TradingConfig()
    for ask in (Decimal("1"), Decimal("2.50"), Decimal("5"), Decimal("8")):
        bid = ask - Decimal("0.05")
        option = _option(format(bid, "f"), format(ask, "f"), expiration=NOW.date() + timedelta(days=2))
        decision = admit_buy(option, EQUITY, 100, config, positions=[], orders=[], state_risk={}, open_premium=Decimal("10000"), sector_premium=Decimal("2000"), buying_power=Decimal("20000"), open_positions=3)
        premium = ask * Decimal("100")
        assert decision.quantity * premium * Decimal("0.40") <= EQUITY * Decimal("0.02")
        assert decision.quantity * premium <= EQUITY * Decimal("0.07")
        assert decision.quantity * premium <= EQUITY * Decimal("0.10")
        assert Decimal("10000") + decision.quantity * premium <= EQUITY * Decimal("0.35")
        assert Decimal("2000") + decision.quantity * premium <= EQUITY * Decimal("0.10")
        assert 3 + (1 if decision.quantity else 0) <= 10


def test_missing_fee_is_not_zero():
    missing = execution_pnl("5", "6", "1")
    assert missing["gross_pnl"] == "100.00"
    assert missing["fees"] is None
    assert missing["net_pnl"] is None
    assert missing["fees_note"] == "עמלות טרם זמינות"
    paid = execution_pnl("5", "6", "1", "1.30")
    assert paid["fees"] == "1.30"
    assert paid["net_pnl"] == "98.70"
