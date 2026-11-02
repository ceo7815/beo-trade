from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings, TradingConfig
from app.options.risk_limits import sector_exposure_reason, sector_key
from app.universe.builder import filter_optionable_equities, is_leveraged_fund
from app.workers.loops import entry_window_open

NOW = datetime(2026, 10, 8, 19, 0, tzinfo=timezone.utc)


def _settings() -> Settings:
    return Settings(app_env="local", trading_mode="PAPER", auth_required=False)


def test_names_without_a_sector_do_not_share_one_bucket():
    config = TradingConfig()
    equity = Decimal("108000")
    book = {
        sector_key("HOOD"): Decimal("7770"),
        sector_key("MSTR"): Decimal("9435"),
        sector_key("MU"): Decimal("3560"),
    }
    assert sector_exposure_reason(equity, sector_key("PLTR"), book, Decimal("3000"), config) is None
    assert sector_exposure_reason(equity, sector_key("MSTR"), book, Decimal("3000"), config) == "SECTOR_EXPOSURE_LIMIT"


def test_a_known_sector_still_groups_its_names():
    config = TradingConfig()
    sectors = {"NVDA": "SEMIS", "AMD": "SEMIS"}
    book = {sector_key("NVDA", sectors): Decimal("10000")}
    assert sector_key("AMD", sectors) == "SEMIS"
    assert sector_exposure_reason(Decimal("108000"), sector_key("AMD", sectors), book, Decimal("1000"), config) == "SECTOR_EXPOSURE_LIMIT"


@pytest.mark.parametrize(
    ("minutes_left", "is_open", "allowed"),
    [(120, True, True), (81, True, True), (80, True, False), (50, True, False), (5, True, False), (120, False, False)],
)
def test_no_new_entry_without_an_hour_before_the_forced_exit(minutes_left, is_open, allowed):
    clock = {"is_open": is_open, "next_close": (NOW + timedelta(minutes=minutes_left)).isoformat().replace("+00:00", "Z")}
    assert entry_window_open(_settings(), clock, NOW) is allowed


def test_recent_buys_are_counted_by_direction_inside_the_window(tmp_path):
    from app.models.db import configure_database, init_db, session_scope
    from app.models.tables import BrokerOrderRecord
    from app.workers.loops import recent_entries_by_right

    settings = Settings(app_env="local", trading_mode="PAPER", auth_required=False, database_url="sqlite:///" + (tmp_path / "beo.db").as_posix(), auto_create_tables=True)
    configure_database(settings)
    init_db(settings)
    rows = [
        ("QQQ261009P00745000", "buy_to_open", "filled", 5),
        ("NVDA261009P00230000", "buy_to_open", "accepted", 10),
        ("TSLA261009P00370000", "buy_to_open", "canceled", 12),
        ("PLTR261009C00197500", "buy_to_open", "filled", 20),
        ("MU261009P01070000", "buy_to_open", "filled", 45),
        ("HOOD261009P00110000", "sell_to_close", "filled", 3),
    ]
    with session_scope() as session:
        for symbol, intent, status, minutes_ago in rows:
            session.add(BrokerOrderRecord(symbol=symbol, side="buy", position_intent=intent, status=status, created_at=NOW - timedelta(minutes=minutes_ago)))
        session.commit()
    assert recent_entries_by_right(NOW, 30) == {"PUT": 2, "CALL": 1}
    assert _settings().trading().max_same_direction_entries == 0


def test_two_buys_fill_the_opening_and_the_third_waits_until_ten(tmp_path):
    from zoneinfo import ZoneInfo

    from app.models.db import configure_database, init_db, session_scope
    from app.models.tables import BrokerOrderRecord
    from app.workers.loops import opening_cap_reached, opening_entries_taken

    ny = ZoneInfo("America/New_York")
    morning = datetime(2026, 10, 15, 9, 50, tzinfo=ny)
    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "opening.db").as_posix(),
        auto_create_tables=True,
    )
    configure_database(settings)
    init_db(settings)
    placed = [
        (datetime(2026, 10, 15, 9, 36, tzinfo=ny), "filled"),
        (datetime(2026, 10, 15, 9, 39, tzinfo=ny), "accepted"),
        (datetime(2026, 10, 15, 9, 44, tzinfo=ny), "canceled"),
        (datetime(2026, 10, 15, 10, 5, tzinfo=ny), "filled"),
        (datetime(2026, 10, 14, 9, 36, tzinfo=ny), "filled"),
    ]
    with session_scope() as session:
        for moment, status in placed:
            session.add(
                BrokerOrderRecord(
                    symbol="QQQ261016P00734000",
                    side="buy",
                    position_intent="buy_to_open",
                    status=status,
                    created_at=moment.astimezone(timezone.utc),
                )
            )
        session.commit()
    taken = opening_entries_taken(morning, 600)
    assert taken == 2
    assert opening_cap_reached(morning, taken, 2, 600) is True
    assert opening_cap_reached(morning, 1, 2, 600) is False
    assert opening_cap_reached(datetime(2026, 10, 15, 10, 0, tzinfo=ny), 6, 2, 600) is False
    assert opening_cap_reached(morning, None, 2, 600) is True
    assert opening_cap_reached(morning, 6, 0, 600) is False
    assert settings.trading().opening_entry_cap == 2


def test_the_entry_cutoff_leaves_an_hour_before_every_forced_exit():
    config = _settings().trading()
    forced = max(config.session_exit_minutes, config.exit_before_expiration_minutes)
    assert config.entry_cutoff_minutes - forced >= 60


@pytest.mark.parametrize(
    ("name", "leveraged"),
    [
        ("ProShares Ultra Silver", True),
        ("Direxion Daily South Korea Bull 3X Shares", True),
        ("ProShares UltraPro QQQ", True),
        ("Direxion Daily Semiconductor Bear -3X Shares", True),
        ("T-Rex 2X Long MSTR Daily Target ETF", True),
        ("MicroSectors FANG+ Index 3X Leveraged ETN", True),
        ("10x Genomics, Inc. Class A Common Stock", False),
        ("Ultra Clean Holdings, Inc. Common Stock", False),
        ("SPDR S&P 500 ETF Trust", False),
        ("Robinhood Markets, Inc. Class A Common Stock", False),
    ],
)
def test_leveraged_funds_are_recognised_by_name(name, leveraged):
    assert is_leveraged_fund(name) is leveraged


def test_leveraged_funds_never_enter_the_universe():
    rows = [
        {"symbol": "AGQ", "name": "ProShares Ultra Silver", "status": "active", "tradable": True, "class": "us_equity", "attributes": ["has_options"]},
        {"symbol": "KORU", "name": "Direxion Daily South Korea Bull 3X Shares", "status": "active", "tradable": True, "class": "us_equity", "attributes": ["has_options"]},
        {"symbol": "HOOD", "name": "Robinhood Markets, Inc. Class A Common Stock", "status": "active", "tradable": True, "class": "us_equity", "attributes": ["has_options"]},
    ]
    symbols, discovered = filter_optionable_equities(rows)
    assert symbols == ("HOOD",)
    assert discovered == 3


def test_a_universe_cached_before_the_leveraged_filter_is_read_again(tmp_path, monkeypatch):
    import json

    from app.universe.builder import load_universe, reset_universe_state

    reset_universe_state()
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({"symbols": ["AGQ", "HOOD"], "refreshed_at": NOW.isoformat(), "status": "READY"}), encoding="utf-8")
    monkeypatch.setattr(
        "app.universe.builder.fetch_assets",
        lambda _settings: [
            {"symbol": "AGQ", "name": "ProShares Ultra Silver", "status": "active", "tradable": True, "class": "us_equity", "attributes": ["has_options"]},
            {"symbol": "HOOD", "name": "Robinhood Markets", "status": "active", "tradable": True, "class": "us_equity", "attributes": ["has_options"]},
        ],
    )
    monkeypatch.setattr("app.universe.builder.fetch_activity_scores", lambda _settings: {})
    settings = _settings()
    book = load_universe(settings, settings.trading(), NOW, path)
    assert book.symbols == ("HOOD",)
    reset_universe_state()


def test_opening_call_block_before_ten_leaves_puts_alone():
    from zoneinfo import ZoneInfo

    from app.positions.entry_pace import opening_call_blocked

    ny = ZoneInfo("America/New_York")
    morning = datetime(2026, 11, 2, 9, 45, tzinfo=ny)
    at_ten = datetime(2026, 11, 2, 10, 0, tzinfo=ny)
    assert opening_call_blocked("CALL", morning, 600) is True
    assert opening_call_blocked("PUT", morning, 600) is False
    assert opening_call_blocked("CALL", at_ten, 600) is False
    assert opening_call_blocked("CALL", morning, 0) is False
    assert _settings().trading().opening_call_block_until_minutes == 600


def test_premium_band_uses_half_to_eight_dollars():
    from datetime import date
    from decimal import Decimal

    from app.quant.filters import reject_option
    from app.schemas.domain import OptionRight, OptionSnapshot, UnderlyingSnapshot

    config = _settings().trading()
    assert config.min_option_price == 0.5
    assert config.max_option_price == 8.0
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    underlying = UnderlyingSnapshot(
        "SPY", Decimal("500"), Decimal("500"), Decimal("500"), Decimal("500"), Decimal("500"),
        2_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal("500"), now, True,
    )

    def option(bid: str, ask: str) -> OptionSnapshot:
        mid = (Decimal(bid) + Decimal(ask)) / 2
        return OptionSnapshot(
            "SPY",
            "SPY261106C00500000",
            OptionRight.CALL,
            Decimal("500"),
            date(2026, 11, 6),
            Decimal(bid),
            Decimal(ask),
            mid,
            500,
            500,
            now,
            Decimal("0.25"),
            Decimal("0.45"),
            Decimal("0.02"),
            Decimal("-0.05"),
            Decimal("0.10"),
        )

    assert reject_option(option("0.40", "0.44"), underlying, config, now, date(2026, 11, 2)) == "premium_band"
    assert reject_option(option("0.50", "0.54"), underlying, config, now, date(2026, 11, 2)) is None
    assert reject_option(option("8.00", "8.10"), underlying, config, now, date(2026, 11, 2)) == "premium_band"
    assert reject_option(option("7.80", "7.90"), underlying, config, now, date(2026, 11, 2)) is None
