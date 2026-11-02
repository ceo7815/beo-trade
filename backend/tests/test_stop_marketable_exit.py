"""STOP exits sell one tick under the bid and reprice faster than other exits."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.config.settings import TradingConfig
from app.options.day_floor import marketable_sell_limit, option_tick


NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


def test_marketable_stop_is_one_tick_under_bid():
    assert option_tick(Decimal("3.00")) == Decimal("0.05")
    assert option_tick(Decimal("2.99")) == Decimal("0.01")
    assert marketable_sell_limit(Decimal("2.15"), "STOP") == Decimal("2.14")
    assert marketable_sell_limit(Decimal("3.55"), "STOP") == Decimal("3.50")
    assert marketable_sell_limit(Decimal("3.55"), "PROTECTED_STOP") == Decimal("3.50")
    # Trail / session / invalidation keep the bid.
    assert marketable_sell_limit(Decimal("3.55"), "TRAILING") == Decimal("3.55")
    assert marketable_sell_limit(Decimal("3.55"), "SESSION_CLOSE") == Decimal("3.55")
    assert marketable_sell_limit(Decimal("3.55"), "INVALIDATION") == Decimal("3.55")
    assert marketable_sell_limit(Decimal("0"), "STOP") is None


def test_stop_reprice_uses_shorter_delay_and_marketable_target(tmp_path):
    from app.models.db import configure_database, init_db, session_scope
    from app.config.settings import Settings
    from app.workers.loops import _reprice_exit

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "stop-reprice.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    calls = []
    adapter = SimpleNamespace(replace_order=lambda order_id, body: calls.append(body) or {})
    order = {
        "broker_order_id": "ord-stop",
        "limit_price": "2.20",
        "submitted_at": (NOW - timedelta(seconds=5)).isoformat(),
    }
    signal = SimpleNamespace(exit_price=Decimal("2.15"), reason="STOP")
    config = TradingConfig()
    with session_scope() as session:
        # Age 5s meets stop_exit_reprice_seconds=5; target is one penny under the bid.
        assert _reprice_exit(adapter, session, "t", order, signal, NOW, NOW + timedelta(hours=2), config) == "2.14"
        # Same age would not move a TRAILING exit (needs exit_reprice_seconds=15).
        trail = SimpleNamespace(exit_price=Decimal("2.15"), reason="TRAILING")
        assert _reprice_exit(adapter, session, "t", order, trail, NOW, NOW + timedelta(hours=2), config) is None
    assert calls == [{"limit_price": "2.14"}]


def test_stop_threshold_unchanged_in_exit_signal():
    from app.paper_trading.engine import PaperFill, exit_signal
    from app.schemas.domain import OptionRight, OptionSnapshot, UnderlyingSnapshot

    fill = PaperFill(
        "t",
        "r",
        "NVDA  261102P00205000",
        1,
        Decimal("1.00"),
        NOW,
        delta_at_entry=Decimal("-0.4"),
        underlying_at_entry=Decimal("100"),
    )
    # Bid exactly at -40% of entry → STOP. Signal still carries the live bid;
    # marketable sizing happens only when the order is priced.
    option = OptionSnapshot(
        "NVDA",
        "NVDA261102P00205000",
        OptionRight.PUT,
        Decimal("205"),
        NOW.date(),
        Decimal("0.60"),
        Decimal("0.62"),
        Decimal("0.61"),
        100,
        100,
        NOW,
        Decimal("-0.40"),
        Decimal("0.05"),
        Decimal("0.02"),
        Decimal("-0.02"),
        Decimal("0.30"),
    )
    underlying = UnderlyingSnapshot(
        "NVDA", Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"),
        2_000_000, Decimal("2"), Decimal("1"), Decimal("1"), Decimal("100"), NOW, True,
    )
    signal = exit_signal(fill, option, underlying, NOW, TradingConfig(), True)
    assert signal is not None
    assert signal.reason == "STOP"
    assert signal.exit_price == Decimal("0.60")
    assert marketable_sell_limit(signal.exit_price, signal.reason) == Decimal("0.59")
