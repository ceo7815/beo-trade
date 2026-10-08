from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.options.risk_limits import sector_exposure_reason, sector_key
from app.universe.builder import filter_optionable_equities, is_leveraged_fund
from app.workers.loops import entry_window_open

NOW = datetime(2026, 10, 8, 19, 0, tzinfo=timezone.utc)


def _settings() -> Settings:
    return Settings(app_env="local", trading_mode="PAPER", auth_required=False)


def test_names_without_a_sector_do_not_share_one_bucket():
    config = _settings().trading()
    equity = Decimal("108000")
    book = {
        sector_key("HOOD"): Decimal("7770"),
        sector_key("MSTR"): Decimal("9435"),
        sector_key("MU"): Decimal("3560"),
    }
    assert sector_exposure_reason(equity, sector_key("PLTR"), book, Decimal("3000"), config) is None
    assert sector_exposure_reason(equity, sector_key("MSTR"), book, Decimal("3000"), config) == "SECTOR_EXPOSURE_LIMIT"


def test_a_known_sector_still_groups_its_names():
    config = _settings().trading()
    sectors = {"NVDA": "SEMIS", "AMD": "SEMIS"}
    book = {sector_key("NVDA", sectors): Decimal("10000")}
    assert sector_key("AMD", sectors) == "SEMIS"
    assert sector_exposure_reason(Decimal("108000"), sector_key("AMD", sectors), book, Decimal("1000"), config) == "SECTOR_EXPOSURE_LIMIT"


@pytest.mark.parametrize(
    ("minutes_left", "is_open", "allowed"),
    [(45, True, True), (31, True, True), (30, True, False), (20, True, False), (5, True, False), (120, False, False)],
)
def test_no_new_entry_in_the_last_half_hour(minutes_left, is_open, allowed):
    clock = {"is_open": is_open, "next_close": (NOW + timedelta(minutes=minutes_left)).isoformat().replace("+00:00", "Z")}
    assert entry_window_open(_settings(), clock, NOW) is allowed


def test_the_entry_cutoff_covers_the_exit_window_and_the_minimum_hold():
    config = _settings().trading()
    assert config.entry_cutoff_minutes >= config.session_exit_minutes + config.holding_window_min_minutes


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
