"""New paper-account funding must not invent a day loss that trips the kill."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.analytics.finance import trading_day_pnl
from app.capital import effective_kill_usd, profile_is_20k
from app.config.settings import load_trading_config
from app.options.risk_limits import daily_loss_state, day_flatten_reason

CONFIG = Path(__file__).resolve().parents[1] / "app" / "config"
RATIO = Decimal(3500) / Decimal(30000)


@pytest.fixture(autouse=True)
def _freeze_fx(monkeypatch):
    monkeypatch.setattr("app.capital.fx.live_ils_per_usd", lambda force=False: None)


def test_new_account_same_day_funding_already_in_last_equity_is_flat():
    """Live 20K paper birth: equity == last_equity == JNLC on the create day."""
    pnl = trading_day_pnl(
        "6370",
        "6370",
        [{"activity_type": "JNLC", "net_amount": "6370", "date": "2026-11-04"}],
        date(2026, 11, 4),
    )
    assert pnl == Decimal("0")


def test_new_account_day1_trading_loss_still_shows():
    """Once the mark moves, day-1 trading is the raw movement — funding stays out."""
    pnl = trading_day_pnl(
        "6270",
        "6370",
        [{"activity_type": "JNLC", "net_amount": "6370", "date": "2026-11-04"}],
        date(2026, 11, 4),
    )
    assert pnl == Decimal("-100")


def test_existing_account_same_day_deposit_still_excluded():
    """Prior close in last_equity: a top-up must not look like profit (or hide a loss)."""
    flat = trading_day_pnl(
        "110000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "10000", "date": "2026-10-02"}],
        date(2026, 10, 2),
    )
    assert flat == Decimal("0")
    lost = trading_day_pnl(
        "100000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "10000", "date": "2026-10-02"}],
        date(2026, 10, 2),
    )
    assert lost == Decimal("-10000")


def test_no_funding_day_unchanged():
    pnl = trading_day_pnl("101000", "100000", [], date(2026, 10, 2))
    assert pnl == Decimal("1000")
    pnl_ignore_yesterday = trading_day_pnl(
        "101000",
        "100000",
        [{"activity_type": "CSD", "net_amount": "5000", "date": "2026-10-01"}],
        date(2026, 10, 2),
    )
    assert pnl_ignore_yesterday == Decimal("1000")


def test_new_account_funding_does_not_trip_20k_kill():
    cfg = load_trading_config()
    assert profile_is_20k(cfg)
    opened = Decimal("6370")
    pnl = trading_day_pnl(
        "6370",
        "6370",
        [{"activity_type": "JNLC", "net_amount": "6370", "date": "2026-11-04"}],
        date(2026, 11, 4),
    )
    assert pnl == Decimal("0")
    kill = effective_kill_usd(cfg, opened, open_equity=opened)
    assert kill == (opened * RATIO).quantize(Decimal("0.01"))
    assert day_flatten_reason(opened, pnl, cfg, open_equity=opened) is None
    assert daily_loss_state(opened, pnl, cfg) == "ok"


def test_zero_last_equity_new_fund_still_nets_to_zero():
    """If Alpaca left last_equity at 0, the ordinary subtract already yields 0."""
    pnl = trading_day_pnl(
        "6370",
        "0",
        [{"activity_type": "JNLC", "net_amount": "6370", "date": "2026-11-04"}],
        date(2026, 11, 4),
    )
    assert pnl == Decimal("0")
