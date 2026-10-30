"""Worker/entry adjusted day P&L must match /risk when funding activities have no FILL sells."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.config.settings import Settings
from app.trades.exclusion import SEEDS, compute_adjusted_day_pnl

BUG_DAY = date(2026, 10, 30)
# Raw mark ≈ sum of three seeded day impacts + leftover -2.52
RAW = Decimal("-4152.52")
ADJUSTED = Decimal("-2.52")
EQUITY = "97984.19"
LAST = "102136.71"  # 97984.19 - 102136.71 = -4152.52


def _rules():
    return [{**seed, "active": True} for seed in SEEDS]


def test_funding_only_activities_still_strip_seeded_bug_lots():
    """read_session_funding returns funding rows only — no FILL sells to evidence closures."""
    funding_only = [
        {
            "activity_type": "CSD",
            "net_amount": "0",
            "date": "2026-10-30",
        }
    ]
    # With funding-only list, closed_rows_from_activities finds nothing; seeds must still apply.
    out = compute_adjusted_day_pnl(
        EQUITY,
        LAST,
        [],
        BUG_DAY,
        activities=funding_only,
        exclusions=_rules(),
    )
    assert out == ADJUSTED


def test_other_session_date_seeds_have_no_effect():
    out = compute_adjusted_day_pnl(
        "100000",
        "100100",  # raw -100
        [],
        date(2026, 10, 29),
        activities=[],
        exclusions=_rules(),
    )
    assert out == Decimal("-100")


def test_open_excluded_lot_not_double_subtracted_with_seeds():
    """Open bug lot: unrealized only; seed closed row skipped via open_excluded."""
    open_meta = {
        "symbol": "META261030C00630000",
        "qty": 7,
        "avg_entry_price": "5.55",
        "opened_at": "2026-10-29T18:00:00+00:00",
        "unrealized_intraday_pl": "-500.00",
    }
    # Measured -600 = META unrealized -500 + other -100. META-only rule strips unrealized once.
    meta_only = [_rules()[0]]
    out = compute_adjusted_day_pnl(
        "100000",
        "100600",
        [open_meta],
        BUG_DAY,
        activities=[],
        exclusions=meta_only,
    )
    assert out == Decimal("-100")




def test_worker_load_tracked_day_funding_only_releases_false_hard_stop(tmp_path, monkeypatch):
    from app.options.day_floor import latch_hard_stop, public_from_store
    from app.workers import loops

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    monkeypatch.setattr("app.options.account_read.store_path", lambda: tmp_path / "account_read.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "adj.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 10, 30, 15, 5, tzinfo=timezone.utc)
    latch_hard_stop(settings, moment)
    assert public_from_store(settings, moment)["closed"] is True

    class Adapter:
        def get_account(self):
            return {"equity": EQUITY, "last_equity": LAST}

        def get_activities(self, *a, **k):
            return []

        def get_session_activities(self, session_date, **k):
            # Funding only — the live worker path. No FILL sells.
            return [{"activity_type": "CSD", "net_amount": "0", "date": "2026-10-30"}]

    monkeypatch.setattr(
        "app.trades.exclusion.active_exclusions",
        lambda session=None: _rules(),
    )
    account, pnl, ok = loops._load_tracked_day(settings, Adapter(), moment, [])
    assert ok is True
    assert pnl == ADJUSTED
    view = loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert view["closed"] is False
    assert public_from_store(settings, moment)["closed"] is False


def test_entry_gate_does_not_latch_on_funding_only_bug_day(tmp_path, monkeypatch):
    from app.broker.execution import _live_day_kill
    from app.options.day_floor import public_from_store

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "gate.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 10, 30, 15, 5, tzinfo=timezone.utc)

    class Adapter:
        def get_activities(self, *a, **k):
            return []

        def get_session_activities(self, session_date, **k):
            return [{"activity_type": "CSD", "net_amount": "0", "date": "2026-10-30"}]

        def get_positions(self):
            return []

    monkeypatch.setattr(
        "app.trades.exclusion.active_exclusions",
        lambda session=None: _rules(),
    )
    reason = _live_day_kill(
        settings,
        Adapter(),
        {"equity": EQUITY, "last_equity": LAST},
        moment,
    )
    assert reason is None
    assert public_from_store(settings, moment)["closed"] is False


def test_real_loss_still_latches_via_worker_and_entry_gate(tmp_path, monkeypatch):
    from app.broker.execution import _live_day_kill
    from app.options.day_floor import public_from_store
    from app.workers import loops

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    monkeypatch.setattr("app.options.account_read.store_path", lambda: tmp_path / "account_read.json")
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "real.db").as_posix(),
        auth_required=False,
        trading_mode="PAPER",
    )
    moment = datetime(2026, 10, 30, 15, 5, tzinfo=timezone.utc)
    # After stripping seeds (-4150), a further -3600 real loss remains → still hard stop.
    # Use a raw mark of -7752.52 so adjusted = -3602.52.
    raw_equity = "94384.19"  # 102136.71 - 7752.52
    last = LAST

    class Adapter:
        def get_account(self):
            return {"equity": raw_equity, "last_equity": last}

        def get_activities(self, *a, **k):
            return []

        def get_session_activities(self, session_date, **k):
            return []

        def get_positions(self):
            return []

    monkeypatch.setattr(
        "app.trades.exclusion.active_exclusions",
        lambda session=None: _rules(),
    )
    _account, pnl, ok = loops._load_tracked_day(settings, Adapter(), moment, [])
    assert ok is True
    assert pnl == Decimal("-3602.52")
    loops._account_day_floor(settings, Adapter(), moment, True, [])
    assert public_from_store(settings, moment)["closed"] is True
    assert public_from_store(settings, moment)["reason"] == "DAILY_HARD_STOP"

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor_gate.json")
    reason = _live_day_kill(settings, Adapter(), {"equity": raw_equity, "last_equity": last}, moment)
    assert reason == "DAILY_HARD_STOP"


def test_theta_timeout_does_not_raise_out_of_monitor_once(tmp_path, monkeypatch):
    """A stuck/timed-out Theta load must not abort the protective pass."""
    from app.models.db import configure_database, init_db
    from app.workers import loops

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "mon.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    moment = datetime(2026, 10, 30, 15, 5, tzinfo=timezone.utc)

    class Adapter:
        def sync_positions(self):
            return [
                {
                    "symbol": "AAPL261030C00200000",
                    "qty": 1,
                    "avg_entry_price": "1.00",
                    "current_price": "1.10",
                    "trade_id": "t-timeout",
                }
            ]

        def get_market_clock(self):
            return {"is_open": True}

        def get_account(self):
            return {"equity": "100000", "last_equity": "100000"}

        def get_activities(self, *a, **k):
            return []

        def get_session_activities(self, *a, **k):
            return []

        def get_orders(self, *a, **k):
            return []

        def close(self):
            pass

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.account_read.store_path", lambda: tmp_path / "account_read.json")

    def boom(*_a, **_k):
        import httpx

        raise httpx.ReadTimeout("stuck", request=httpx.Request("GET", "http://theta/test"))

    monkeypatch.setattr(loops, "_loaded_bundles", boom)
    monkeypatch.setattr(loops, "_retire_closed_states", lambda *_a, **_k: None)
    monkeypatch.setattr(
        loops,
        "_account_day_floor",
        lambda *_a, **_k: {"closed": False, "reason": "", "cleared": False, "armed": False, "arm": "0", "floor": "0"},
    )
    monkeypatch.setattr(loops, "_held_underlyings", lambda _p: ("AAPL",))
    # Force shadow path to also hit the timed-out loader; real _shadow_marks swallows it.
    monkeypatch.setattr("app.analytics.learning.shadow_due", lambda _m: True)
    monkeypatch.setattr("app.analytics.learning.shadow_symbols", lambda _m, _h: ["AAPL261030C00200000"])

    result = loops.monitor_once(settings, quotes=None, adapter=Adapter(), now=moment, submit=False)
    assert result["positions"] == 1


def test_thetadata_uses_explicit_httpx_timeout():
    import httpx

    from app.providers import thetadata

    assert isinstance(thetadata.REQUEST_TIMEOUT, httpx.Timeout)
    assert thetadata.REQUEST_TIMEOUT.read == 15.0
    assert thetadata.REQUEST_TIMEOUT.connect == 5.0
