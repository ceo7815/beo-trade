from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app.analytics.routes import write_last_scan
from app.config.settings import Settings
from app.positions.entry_pace import load_pace_book
from app.providers.thetadata import ThetaFeed, reset_theta_state
from app.schemas.domain import OptionRight
from app.workers import loops
from tests.test_engine import AS_OF
from tests.test_entry_pace import CALL, NOW, PUT, _config, _database
from tests.test_thetadata import FRESH, _chain, _feed, _handler, _settings

EXCHANGE = ZoneInfo("America/New_York")


def test_write_last_scan_uses_json_default_str(tmp_path, monkeypatch):
    monkeypatch.setattr("app.analytics.routes.LAST_SCAN", tmp_path / "last_scan.json")
    write_last_scan({"observed_at": datetime.now(timezone.utc), "ratio": Decimal("1.5")})
    body = (tmp_path / "last_scan.json").read_text(encoding="utf-8")
    assert "1.5" in body
    assert "observed_at" in body


def test_open_minute_skips_equal_ohlc_window(monkeypatch):
    reset_theta_state()
    # 09:31 New York -> completed minute is 09:30, which must not call history/ohlc with start == end.
    as_of = datetime(2026, 10, 1, 13, 31, tzinfo=timezone.utc)
    bad: list[tuple[str, str, str]] = []
    base, _seen = _handler(stamp=FRESH)

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v3/stock/history/ohlc":
            start = request.url.params.get("start_time")
            end = request.url.params.get("end_time")
            if start == end:
                bad.append((start or "", end or "", request.url.params.get("symbol") or ""))
        return base(request)

    feed = _feed(handle)
    from app.providers.thetadata import ThetaMarketProvider

    ThetaMarketProvider(feed).load_underlyings(as_of)
    assert bad == []


def test_load_contract_options_targets_named_contracts_only():
    reset_theta_state()
    chain = _chain(FRESH)
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        path = request.url.path
        if path.endswith("/quote"):
            return httpx.Response(200, json=[chain["quote"]])
        if path.endswith("/ohlc"):
            return httpx.Response(200, json=[chain["ohlc"]])
        if path.endswith("/open_interest"):
            return httpx.Response(200, json=[chain["interest"]])
        if path.endswith("/greeks/all"):
            return httpx.Response(200, json=[chain["greeks"]])
        base, _seen = _handler()
        return base(request)

    feed = _feed(handle)
    rows = feed.load_contract_options(
        AS_OF,
        [("NVDA", date(2026, 10, 3), Decimal("100"), OptionRight.CALL)],
    )
    assert len(rows) == 1
    assert rows[0].option_symbol.replace(" ", "").startswith("NVDA")
    assert paths.count("/v3/option/snapshot/quote") == 1
    assert all("max_dte" not in str(p) for p in paths)


def test_revalidation_quotes_do_not_reload_full_option_chain(monkeypatch):
    reset_theta_state()
    chain = _chain(FRESH)
    loads = {"full": 0}
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        path = request.url.path
        if path.endswith("/quote") and request.url.params.get("expiration") != "*":
            return httpx.Response(200, json=[chain["quote"]])
        if path.endswith("/ohlc") and request.url.params.get("expiration") != "*":
            return httpx.Response(200, json=[chain["ohlc"]])
        if path.endswith("/open_interest") and request.url.params.get("expiration") != "*":
            return httpx.Response(200, json=[chain["interest"]])
        if path.endswith("/greeks/all") and request.url.params.get("expiration") != "*":
            return httpx.Response(200, json=[chain["greeks"]])
        base, _seen = _handler()
        return base(request)

    class CountingOptions:
        name = "thetadata"

        def __init__(self, feed):
            self.feed = feed

        def load_options(self, as_of):
            loads["full"] += 1
            return []

    def fresh_providers(settings):
        feed = ThetaFeed(settings, transport=httpx.MockTransport(handle))
        from app.providers.thetadata import ThetaMarketProvider

        return SimpleNamespace(
            market=ThetaMarketProvider(feed),
            options=CountingOptions(feed),
            news=SimpleNamespace(name="unconfigured"),
        )

    monkeypatch.setattr(loops, "build_providers", fresh_providers)
    buy = SimpleNamespace(
        underlying="NVDA",
        option_symbol="NVDA  261003C00100000",
        expiration=date(2026, 10, 3),
        strike=100,
        call_put=OptionRight.CALL,
        bid=1.0,
        ask=1.1,
        option_price=1.05,
        volume=100,
        open_interest=500,
        timestamp=AS_OF,
        iv=0.35,
        delta=0.4,
        gamma=0.02,
        theta=-0.05,
        vega=0.1,
    )
    quotes = loops._revalidation_quotes(_settings(), AS_OF, [buy])
    assert loads["full"] == 0
    assert len(quotes) == 1
    assert not any("/history/" in path for path in paths)
    assert "/v3/stock/snapshot/trade" in paths


def test_stored_losses_ignore_fills_before_the_ny_session(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerFillRecord

    settings = _database(tmp_path)
    yesterday = (NOW - timedelta(days=1)).astimezone(timezone.utc)
    opened = (NOW - timedelta(minutes=40)).astimezone(timezone.utc)
    closed = (NOW - timedelta(minutes=12)).astimezone(timezone.utc)
    with session_scope() as session:
        session.add(
            BrokerFillRecord(
                execution_id="old-buy",
                symbol=CALL,
                payload={
                    "execution_id": "old-buy",
                    "symbol": CALL,
                    "side": "buy",
                    "qty": "1",
                    "price": "2.00",
                    "timestamp": yesterday.isoformat(),
                },
                observed_at=yesterday,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="old-sell",
                symbol=CALL,
                payload={
                    "execution_id": "old-sell",
                    "symbol": CALL,
                    "side": "sell",
                    "qty": "1",
                    "price": "1.00",
                    "timestamp": yesterday.isoformat(),
                },
                observed_at=yesterday,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="today-buy",
                symbol=PUT,
                payload={
                    "execution_id": "today-buy",
                    "symbol": PUT,
                    "side": "buy",
                    "qty": "1",
                    "price": "2.00",
                    "timestamp": opened.isoformat(),
                },
                observed_at=opened,
            )
        )
        session.add(
            BrokerFillRecord(
                execution_id="today-sell",
                symbol=PUT,
                payload={
                    "execution_id": "today-sell",
                    "symbol": PUT,
                    "side": "sell",
                    "qty": "1",
                    "price": "1.00",
                    "timestamp": closed.isoformat(),
                },
                observed_at=closed,
            )
        )
        session.commit()
    book = load_pace_book(NOW, _config())
    assert book is not None and book.readable
    assert len(book.losses) == 1
    assert book.losses[0].right == "PUT"


def test_blocked_all_source_records_trade_events_and_logs():
    import inspect

    source = inspect.getsource(loops._submit_autonomous_buys)
    assert "worker.submit_started" in source
    assert "worker.submit_blocked_all" in source
    assert 'record_trade_event(session, row.recommendation_id, "BLOCKED"' in source
