"""Submit must not read the whole account or the whole order history.

The October 29 worker died after worker.submit_started with no shutdown line.
These checks bound that path to the New York session and record how much
resident memory the old snapshot load used.
"""

from __future__ import annotations

import tracemalloc
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.broker.adapter import AlpacaAdapter
from app.config.settings import Settings
from app.integrations.alpaca import AlpacaPaperClient
from app.ops.memory import read_cgroup, rss_mb
from app.workers.loops import _submit_autonomous_buys
from tests.test_entry_pace import CALL, NOW, _config, _database

ROOT = Path(__file__).resolve().parents[2]


def test_a_client_timeout_is_a_submit_timeout():
    from app.workers.loops import _is_timeout

    try:
        raise httpx.ReadTimeout("slow")
    except httpx.ReadTimeout as exc:
        assert _is_timeout(exc) is True
    assert _is_timeout(TimeoutError("broker snapshot refresh timed out")) is True
    assert _is_timeout(RuntimeError("no")) is False


def test_cgroup_files_are_optional(tmp_path):
    assert read_cgroup([tmp_path])["cgroup"] is None
    assert read_cgroup([tmp_path])["oom_kill"] is None
    missing = tmp_path / "absent"
    assert rss_mb(missing) is None


def test_cgroup_v2_and_v1_report_oom_kill(tmp_path):
    v2 = tmp_path / "v2"
    v2.mkdir()
    (v2 / "memory.current").write_text("1610612736\n", encoding="utf-8")
    (v2 / "memory.max").write_text("max\n", encoding="utf-8")
    (v2 / "memory.peak").write_text("1700000000\n", encoding="utf-8")
    (v2 / "memory.events").write_text("oom 1\noom_kill 3\n", encoding="utf-8")
    fields = read_cgroup([v2])
    assert fields["cgroup"] == "v2"
    assert fields["memory_max"] == "max"
    assert fields["memory_current"] == 1610612736
    assert fields["cgroup_current_mb"] == 1536
    assert fields["oom_kill"] == 3

    v1 = tmp_path / "v1"
    v1.mkdir()
    (v1 / "memory.usage_in_bytes").write_text("1048576\n", encoding="utf-8")
    (v1 / "memory.limit_in_bytes").write_text(str(1 << 62), encoding="utf-8")
    (v1 / "memory.max_usage_in_bytes").write_text("2097152\n", encoding="utf-8")
    (v1 / "memory.oom_control").write_text("oom_kill_disable 0\noom_kill 1\n", encoding="utf-8")
    legacy = read_cgroup([v1])
    assert legacy["cgroup"] == "v1"
    assert legacy["memory_max"] is None
    assert legacy["oom_kill"] == 1
    assert legacy["cgroup_current_mb"] == 1


def test_vmrss_is_kibibytes(tmp_path):
    path = tmp_path / "status"
    path.write_text("Name:\tpython\nVmRSS:\t 2048 kB\n", encoding="utf-8")
    assert rss_mb(path) == 2


def test_session_activities_stay_inside_two_dates_and_stop_paging():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        if request.url.params.get("page_token"):
            return httpx.Response(200, json=[{"id": "only", "activity_type": "CSD", "net_amount": "1", "date": "2026-10-29"}])
        page = [{"id": f"id-{index}", "activity_type": "FILL", "net_amount": "1", "date": "2026-10-29"} for index in range(100)]
        return httpx.Response(200, json=page)

    adapter = AlpacaAdapter(AlpacaPaperClient("PKPAPERKEY1234", "secret-value-9999", "PAPER", httpx.MockTransport(handler)))
    rows = adapter.get_session_activities(date(2026, 10, 29))
    assert len(seen) == 2
    assert seen[0]["after"] == "2026-10-28"
    assert seen[0]["until"] == "2026-10-30"
    assert "CSD" in seen[0]["activity_types"]
    assert "FILL" not in seen[0]["activity_types"]
    assert seen[1]["page_token"] == "id-99"
    assert len(rows) == 101
    assert adapter.client._client.timeout is not None
    assert adapter.client._client.timeout.read == 8.0


def test_a_mock_without_the_bounded_reader_still_uses_get_activities():
    from app.analytics.finance import read_session_funding

    class Book:
        def get_activities(self):
            return [{"activity_type": "CSD", "net_amount": "5", "date": "2026-10-29"}]

    assert read_session_funding(Book(), date(2026, 10, 29))[0]["net_amount"] == "5"


def test_stopped_names_and_closed_pnl_ignore_the_previous_session(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerTrade, BrokerTradeEvent, PaperTrade, PositionState
    from app.positions.admission import realized_session_pnl, underlyings_stopped_today

    settings = _database(tmp_path)
    today = (NOW - timedelta(minutes=30)).astimezone(timezone.utc)
    yesterday = (NOW - timedelta(days=1)).astimezone(timezone.utc)

    def position(trade_id: str, underlying: str, when: datetime) -> PositionState:
        return PositionState(
            trade_id=trade_id,
            symbol="NVDA261030C00100000",
            underlying=underlying,
            status="closed",
            entry_fill_price=1,
            entry_fill_quantity=1,
            entry_time=when,
            entry_option_price=1,
            peak_option_price=1,
            peak_time=when,
            planned_risk=40,
            one_r_amount=40,
            initial_stop_price=0.6,
            protected_stop_price=0.9,
            target_1=1.5,
            target_2=2,
            trailing_peak=1,
            trailing_trigger=0.8,
        )

    with session_scope() as session:
        for trade_id, when in (("today-stop", today), ("old-stop", yesterday), ("today-trail", today)):
            session.add(BrokerTrade(id=trade_id, state="closed", source="test"))
        session.add(position("today-stop", "NVDA", today))
        session.add(position("old-stop", "AAPL", yesterday))
        session.add(position("today-trail", "AMD", today))
        session.add(BrokerTradeEvent(trade_id="today-stop", state="EXIT_TRIGGERED", source="test", reason="STOP 1.2", created_at=today))
        session.add(BrokerTradeEvent(trade_id="old-stop", state="EXIT_TRIGGERED", source="test", reason="STOP 1.2", created_at=yesterday))
        session.add(BrokerTradeEvent(trade_id="today-trail", state="EXIT_TRIGGERED", source="test", reason="TRAILING 2", created_at=today))
        for trade_id, when, pnl in (("loss-today", today, -40), ("loss-old", yesterday, -500)):
            session.add(
                PaperTrade(
                    id=trade_id,
                    user_id=settings.dev_user_id,
                    recommendation_id=trade_id,
                    option_symbol=CALL,
                    quantity=1,
                    entry_price=2,
                    entry_at=when,
                    exit_price=1,
                    exit_at=when,
                    pnl_dollars=pnl,
                    bid=2,
                    ask=2.1,
                    iv=0.4,
                    delta=0.4,
                    gamma=0.01,
                    theta=-0.02,
                    vega=0.1,
                    underlying_at_entry=100,
                )
            )
        session.commit()
    with session_scope() as session:
        assert underlyings_stopped_today(session, NOW) == {"NVDA"}
        assert realized_session_pnl(session, NOW) == -40


def test_pace_book_does_not_materialize_old_snapshot_payloads(tmp_path):
    from app.models.db import session_scope
    from app.models.tables import BrokerOrderRecord, BrokerOrderSnapshot
    from app.positions.entry_pace import load_pace_book
    from sqlalchemy import select

    _database(tmp_path)
    fat = {"blob": "x" * 32000}
    old = (NOW - timedelta(days=10)).astimezone(timezone.utc)
    recent = (NOW - timedelta(minutes=1)).astimezone(timezone.utc)
    with session_scope() as session:
        session.add(
            BrokerOrderRecord(
                symbol=CALL,
                side="buy",
                position_intent="buy_to_open",
                status="filled",
                broker_order_id="today",
                created_at=recent,
            )
        )
        session.add(
            BrokerOrderSnapshot(
                broker_order_id="today",
                symbol=CALL,
                internal_state="FILLED",
                payload={"state": "FILLED"},
                observed_at=recent,
            )
        )
        for index in range(200):
            session.add(
                BrokerOrderSnapshot(
                    broker_order_id=f"old-{index}",
                    symbol=CALL,
                    internal_state="FILLED",
                    payload=fat,
                    observed_at=old,
                )
            )
        session.commit()

    tracemalloc.start()
    book = load_pace_book(NOW, _config())
    _current, bounded = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    tracemalloc.start()
    with session_scope() as session:
        loaded = list(session.scalars(select(BrokerOrderSnapshot)))
        assert len(loaded) == 201
        assert sum(len(str(row.payload)) for row in loaded) > 6_000_000
    _current, full = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert book is not None and [item.right for item in book.entries] == ["CALL"]
    assert bounded * 4 < full, f"bounded {bounded} bytes, full snapshot load {full} bytes"


def test_submit_logs_every_step_and_the_worker_limit_is_compose_v2():
    from app.providers.benzinga import REQUEST_TIMEOUT_SECONDS as benzinga_timeout
    from app.providers.thetadata import REQUEST_TIMEOUT_SECONDS as theta_timeout

    source = Path(_submit_autonomous_buys.__code__.co_filename).read_text(encoding="utf-8")
    for step in (
        "entry_block_reason",
        "build_providers",
        "_revalidation_quotes",
        "news",
        "_cash",
        "_entry_pnl",
        "_measured_sectors",
        "open_paper_broker",
        "clock",
        "positions_orders",
        "position_state",
        "get_account",
        "get_activities",
        "trading_day_pnl",
        "day_floor",
        "load_pace_book",
        "row_gate",
        "place_order",
    ):
        assert f'"{step}"' in source
    assert "worker.submit_step" in source
    assert "SUBMIT_TIMEOUT" in source
    assert 0 < theta_timeout <= 30
    assert 0 < benzinga_timeout <= 30
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    worker = compose.split("  worker:", 1)[1].split("\n  theta:", 1)[0]
    assert "mem_limit: 1536m" in worker
    assert "memswap_limit: 1536m" in worker
    assert "memory: 1536M" in worker
    assert 'PYTHONUNBUFFERED: "1"' in worker
    assert "worker.cgroup" in (ROOT / "backend/app/workers/runner.py").read_text(encoding="utf-8")
