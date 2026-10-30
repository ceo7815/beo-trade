"""Entry gating must use the same bug-adjusted day P&L as /risk and the kill.

On 2026-10-30 the raw Alpaca mark is the three excluded lots (−4150). The scan
was suppressing every name with DAILY_LOSS_LIMIT_REACHED before a buy existed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from app.config.settings import Settings, TradingConfig
from app.options.risk_limits import daily_loss_reason, day_flatten_reason
from app.recommendations.pipeline import run_scan
from app.schemas.domain import DecisionKind
from tests.test_engine import AS_OF, ledger, loose_config, sample_market

BUG_DAY = datetime(2026, 10, 30, 15, 22, tzinfo=timezone.utc)
NORMAL_DAY = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
EQUITY = "97984.25"  # 4% entry stop = 3919.37
LAST = "102134.25"  # raw mark −4150.00
RAW = Decimal("-4150.00")
EXCLUDED = Decimal("-4150.00")  # META −1827 + NVDA −1408 + SPY −915


class _Clock(datetime):
    fixed = BUG_DAY

    @classmethod
    def now(cls, tz=None):
        moment = cls.fixed
        if tz is None:
            return moment
        return moment.astimezone(tz)


class Book:
    def __init__(self, equity: str, last: str, *, fail: str = "") -> None:
        self.equity = equity
        self.last = last
        self.fail = fail
        self.positions: list[dict] = []
        self.writes: list[str] = []

    def get_account(self):
        if self.fail == "account":
            raise RuntimeError("account unread")
        return {"equity": self.equity, "last_equity": self.last}

    def get_activities(self, *args, **kwargs):
        if self.fail == "activities":
            raise RuntimeError("activities unread")
        return []

    def get_session_activities(self, session_date, **kwargs):
        if self.fail == "activities":
            raise RuntimeError("activities unread")
        return [{"activity_type": "CSD", "net_amount": "0", "date": session_date.isoformat()}]

    def get_positions(self):
        if self.fail == "positions":
            raise RuntimeError("positions unread")
        return list(self.positions)

    def close(self):
        return None

    def submit_entry_order(self, payload):
        self.writes.append("submit")
        return payload


def _database(tmp_path):
    from app.models.db import configure_database, init_db

    settings = Settings(
        app_env="local",
        trading_mode="PAPER",
        auth_required=False,
        database_url="sqlite:///" + (tmp_path / "entry.db").as_posix(),
        auto_create_tables=True,
    )
    configure_database(settings)
    init_db(settings)
    return settings


def _trade(settings, trade_id: str, symbol: str, qty: int, entry: str, opened: str, closed: str, pnl: str):
    from app.models.tables import PaperTrade

    return PaperTrade(
        id=trade_id,
        user_id=settings.dev_user_id,
        recommendation_id=trade_id,
        option_symbol=symbol,
        quantity=qty,
        entry_price=float(entry),
        entry_at=datetime.fromisoformat(opened),
        exit_price=1,
        exit_at=datetime.fromisoformat(closed),
        pnl_dollars=float(pnl),
        bid=1,
        ask=1.1,
        iv=0.4,
        delta=0.4,
        gamma=0.01,
        theta=-0.02,
        vega=0.1,
        underlying_at_entry=100,
    )


def _bug_lots(settings):
    return [
        _trade(
            settings,
            "meta-bug",
            "META261030C00630000",
            7,
            "5.55",
            "2026-10-29T17:47:00+00:00",
            "2026-10-30T13:30:22+00:00",
            "-1827.00",
        ),
        _trade(
            settings,
            "nvda-bug",
            "NVDA261102P00205000",
            32,
            "1.51",
            "2026-10-30T13:52:29+00:00",
            "2026-10-30T13:59:52+00:00",
            "-1408.00",
        ),
        _trade(
            settings,
            "spy-bug",
            "SPY261102P00760000",
            15,
            "3.19",
            "2026-10-30T13:52:26+00:00",
            "2026-10-30T13:59:53+00:00",
            "-915.00",
        ),
    ]


def _freeze(monkeypatch, moment: datetime) -> None:
    _Clock.fixed = moment
    monkeypatch.setattr("app.workers.loops.datetime", _Clock)


def _bind_book(monkeypatch, book: Book) -> None:
    monkeypatch.setattr("app.broker.runtime.open_paper_broker", lambda _settings: book)


def _stored_pnl(settings, moment: datetime) -> tuple[int, int, Decimal]:
    from sqlalchemy import select

    from app.models.db import session_scope
    from app.models.tables import PaperTrade, TradeExclusion
    from app.positions.admission import realized_session_pnl

    with session_scope() as session:
        rows = list(session.scalars(select(PaperTrade)))
        exclusions = list(session.scalars(select(TradeExclusion)))
        realized = realized_session_pnl(session, moment)
    return len(rows), len(exclusions), realized


def test_raw_minus_4150_is_daily_loss_limit_and_adjusted_entry_is_allowed(tmp_path, monkeypatch):
    """The suppress reason is DAILY_LOSS_LIMIT_REACHED on the raw mark. After exclusion, entry is open."""
    from app.workers.loops import _entry_pnl

    settings = _database(tmp_path)
    from app.models.db import session_scope

    with session_scope() as session:
        for row in _bug_lots(settings):
            session.add(row)
        session.commit()
    before_rows, before_rules, raw_realized = _stored_pnl(settings, BUG_DAY)
    assert before_rows == 3
    assert before_rules == 3
    assert raw_realized == RAW == EXCLUDED

    equity = Decimal(EQUITY)
    trading = TradingConfig()
    assert daily_loss_reason(equity, RAW, trading) == "DAILY_LOSS_LIMIT_REACHED"
    assert str((equity * Decimal(str(trading.daily_entry_stop_pct))).quantize(Decimal("0.01"))) == "3919.37"

    book = Book(EQUITY, LAST)
    _freeze(monkeypatch, BUG_DAY)
    _bind_book(monkeypatch, book)
    adjusted = _entry_pnl(settings)
    assert adjusted == Decimal("0.00")
    assert daily_loss_reason(equity, adjusted, trading) is None
    assert day_flatten_reason(equity, adjusted, trading) is None
    assert book.writes == []

    after_rows, after_rules, still_raw = _stored_pnl(settings, BUG_DAY)
    assert after_rows == before_rows
    assert after_rules == before_rules
    assert still_raw == RAW

    underlying, option, _news = sample_market()
    config = replace(loose_config(), allow_quant_entry=True, min_events=1)
    counts: dict = {}
    blocked = run_scan(
        [underlying],
        [option],
        [],
        config,
        None,
        ledger(),
        AS_OF,
        AS_OF.date(),
        equity,
        set(),
        daily_pnl=RAW,
        enforce_loss=True,
        counts=counts,
    )
    assert counts["risk_passed"] >= 1
    assert counts["buys"] == 0
    assert blocked[0].decision is DecisionKind.SUPPRESS
    assert blocked[0].suppress_reason == "DAILY_LOSS_LIMIT_REACHED"
    assert counts["suppress_reasons"] == {"DAILY_LOSS_LIMIT_REACHED": 1}

    opened = run_scan(
        [underlying],
        [option],
        [],
        config,
        None,
        ledger(),
        AS_OF,
        AS_OF.date(),
        equity,
        set(),
        daily_pnl=adjusted,
        enforce_loss=True,
    )
    assert opened[0].decision is DecisionKind.BUY
    assert opened[0].suppress_reason == ""


def test_scan_stages_count_suppress_reasons():
    from app.providers.registry import ProviderSet
    from app.providers.replay import ReplayMarket, ReplayNews, ReplayOptions
    from app.recommendations.live_scan import execute_scan

    underlying, option, _news = sample_market()
    providers = ProviderSet(
        market=ReplayMarket([underlying]),
        options=ReplayOptions([option]),
        news=ReplayNews([]),
    )
    config = replace(loose_config(), allow_quant_entry=True, min_events=1)
    result = execute_scan(
        providers,
        config,
        None,
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal(EQUITY),
        set(),
        daily_pnl=RAW,
        enforce_loss=True,
    )
    assert result.stages["risk_passed"] >= 1
    assert result.stages["buys"] == 0
    assert result.stages["suppress_reasons"]["DAILY_LOSS_LIMIT_REACHED"] >= 1


def test_unreadable_account_or_activities_block_instead_of_opening(tmp_path, monkeypatch):
    from app.workers.loops import _entry_pnl

    settings = _database(tmp_path)
    _freeze(monkeypatch, BUG_DAY)
    for kind in ("account", "activities", "positions"):
        _bind_book(monkeypatch, Book(EQUITY, LAST, fail=kind))
        assert _entry_pnl(settings) is None

    blank = Book("", "")
    blank.get_account = lambda: {"equity": None, "last_equity": LAST}
    _bind_book(monkeypatch, blank)
    assert _entry_pnl(settings) is None

    config = replace(loose_config(), allow_quant_entry=True, min_events=1)
    underlying, option, _news = sample_market()
    rows = run_scan(
        [underlying],
        [option],
        [],
        config,
        None,
        ledger(),
        AS_OF,
        AS_OF.date(),
        Decimal(EQUITY),
        set(),
        daily_pnl=None,
        enforce_loss=True,
    )
    assert rows[0].decision is DecisionKind.SUPPRESS
    assert rows[0].suppress_reason == "DAILY_LOSS_LIMIT_REACHED"


def test_worse_of_keeps_a_real_closed_loss_after_bug_lots_are_removed(tmp_path, monkeypatch):
    from app.workers.loops import _entry_pnl

    settings = _database(tmp_path)
    from app.models.db import session_scope

    with session_scope() as session:
        for row in _bug_lots(settings):
            session.add(row)
        session.add(
            _trade(
                settings,
                "amd-real",
                "AMD261030C00100000",
                1,
                "2.00",
                "2026-10-30T14:10:00+00:00",
                "2026-10-30T15:00:00+00:00",
                "-4000.00",
            )
        )
        session.commit()
    _freeze(monkeypatch, BUG_DAY)
    _bind_book(monkeypatch, Book(EQUITY, LAST))
    pnl = _entry_pnl(settings)
    equity = Decimal(EQUITY)
    trading = TradingConfig()
    assert pnl == Decimal("-4000.00")
    assert daily_loss_reason(equity, pnl, trading) == "DAILY_LOSS_LIMIT_REACHED"
    assert day_flatten_reason(equity, Decimal("0.00"), trading) is None
    _rows, _rules, raw = _stored_pnl(settings, BUG_DAY)
    assert raw == RAW + Decimal("-4000.00")


def test_normal_day_real_loss_still_blocks_entry_and_kill(tmp_path, monkeypatch):
    """A session with no excluded impact keeps the 4% entry stop and the dollar kill."""
    from app.broker.execution import _live_day_kill
    from app.options.day_floor import public_from_store
    from app.workers import loops

    monkeypatch.setattr("app.options.day_floor.store_path", lambda: tmp_path / "day_floor.json")
    monkeypatch.setattr("app.options.day_floor._release_marker_path", lambda: tmp_path / "release.marker")
    monkeypatch.setattr("app.options.account_read.store_path", lambda: tmp_path / "account_read.json")
    settings = _database(tmp_path)
    trading = TradingConfig()
    _freeze(monkeypatch, NORMAL_DAY)

    entry_book = Book("50000.00", "52100.00")
    _bind_book(monkeypatch, entry_book)
    entry_pnl = loops._entry_pnl(settings)
    assert entry_pnl == Decimal("-2100.00")
    assert daily_loss_reason(Decimal("50000.00"), entry_pnl, trading) == "DAILY_LOSS_LIMIT_REACHED"
    assert day_flatten_reason(Decimal("50000.00"), entry_pnl, trading) is None
    assert _live_day_kill(settings, entry_book, {"equity": "50000.00", "last_equity": "52100.00"}, NORMAL_DAY) is None
    assert public_from_store(settings, NORMAL_DAY)["closed"] is False

    kill_book = Book("100000.00", "103600.00")
    account, tracked, ok = loops._load_tracked_day(settings, kill_book, NORMAL_DAY, [])
    assert ok is True
    assert tracked == Decimal("-3600.00")
    assert account["equity"] == "100000.00"
    reason = _live_day_kill(settings, kill_book, {"equity": "100000.00", "last_equity": "103600.00"}, NORMAL_DAY)
    assert reason == "DAILY_HARD_STOP"
    assert public_from_store(settings, NORMAL_DAY)["closed"] is True
    assert public_from_store(settings, NORMAL_DAY)["reason"] == "DAILY_HARD_STOP"
    assert entry_book.writes == []
    assert kill_book.writes == []
