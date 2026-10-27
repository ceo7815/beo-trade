import threading
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.analytics.read_cache import ReadCache, read_cache
from app.config.settings import Settings
from app.models.db import configure_database, init_db
from app.models.indexes import ensure_read_indexes


@pytest.fixture(autouse=True)
def _clear_read_cache():
    read_cache.clear()
    yield
    read_cache.clear()


def test_ttl_cache_returns_a_copy_until_it_expires(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("app.analytics.read_cache.time.monotonic", lambda: clock["now"])
    cache = ReadCache()
    cache.set(("trades", "all"), {"available": True, "closed": [1]}, 45)
    first = cache.get(("trades", "all"))
    first["closed"].append(2)
    assert cache.get(("trades", "all"))["closed"] == [1]
    clock["now"] = 146
    assert cache.get(("trades", "all")) is None


def test_a_failed_report_is_not_cached():
    cache = ReadCache()
    cache.set(("trades", "today"), {"available": False, "detail": "down"}, 45)
    assert cache.get(("trades", "today")) is None


def test_concurrent_callers_share_one_computation():
    cache = ReadCache()
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        started.set()
        assert release.wait(2)
        return {"available": True, "n": calls["n"]}

    results = []

    def run():
        results.append(cache.get_or_compute(("trades", "all"), 45, produce))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    assert started.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert calls["n"] == 1
    assert len(results) == 4
    assert all(row["n"] == 1 for row in results)


def test_a_new_fill_drops_the_heavy_read_cache(tmp_path):
    from app.broker.store import remember_fill
    from app.models.db import session_scope

    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "fills.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    read_cache.set(("trades", "all"), {"available": True, "closed": []}, 45)
    read_cache.set(("broker", "positions"), {"items": []}, 8)
    with session_scope() as session:
        assert remember_fill(
            session,
            {
                "execution_id": "fill-1",
                "symbol": "SPY261009C00700000",
                "side": "buy",
                "qty": "1",
                "price": "1.00",
                "timestamp": "2026-10-27T14:00:00Z",
            },
        )
        session.commit()
    assert read_cache.get(("trades", "all")) is None
    assert read_cache.get(("broker", "positions")) is None


def test_commit_if_unlocked_does_not_leave_a_short_sqlite_timeout():
    from app.broker.store import commit_if_unlocked

    class Connection:
        def __init__(self):
            self.pragmas = []

        def exec_driver_sql(self, sql):
            self.pragmas.append(sql)

    class Session:
        def __init__(self):
            self.connection_obj = Connection()
            self.rolled = False

        def get_bind(self):
            class Bind:
                url = "sqlite:///ignored.db"

            return Bind()

        def connection(self):
            return self.connection_obj

        def commit(self):
            raise RuntimeError("database is locked")

        def rollback(self):
            self.rolled = True

    session = Session()
    assert commit_if_unlocked(session) is False
    assert session.rolled is True
    assert session.connection_obj.pragmas == ["PRAGMA busy_timeout=50", "PRAGMA busy_timeout=30000"]


def test_init_db_creates_the_report_indexes(tmp_path):
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "fresh.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    init_db(settings)
    from app.models.db import _engine

    _assert_one_each(_engine)
    assert ensure_read_indexes(_engine).count("ix_broker_orders_broker_order_id") == 1
    _assert_one_each(_engine)


def test_migration_adds_indexes_that_create_all_missed(tmp_path):
    url = "sqlite:///" + (tmp_path / "old.db").as_posix()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE broker_orders (id VARCHAR(36) PRIMARY KEY, broker_order_id VARCHAR(64) DEFAULT '')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE recommendations (id VARCHAR(36) PRIMARY KEY, underlying VARCHAR(32), decision VARCHAR(16), created_at DATETIME)"
        )
    settings = Settings(
        app_env="local",
        database_url=url,
        auto_create_tables=False,
        auth_required=False,
        trading_mode="PAPER",
    )
    configure_database(settings)
    from app.models.db import _engine

    assert "ix_broker_orders_broker_order_id" not in _index_names(_engine)
    ensure_read_indexes(_engine)
    ensure_read_indexes(_engine)
    _assert_one_each(_engine)


def test_today_asks_alpaca_for_a_short_window_and_the_second_read_is_cached(tmp_path, monkeypatch):
    from app.main import create_app
    from tests.test_trades_report import _fills

    seen = {"calls": 0, "after": None}

    class Adapter:
        def get_all_fills(self, max_pages: int = 20, after: str = ""):
            seen["calls"] += 1
            seen["after"] = after
            return _fills()

        def get_positions(self):
            return []

        def close(self):
            return None

    monkeypatch.setattr("app.analytics.routes.open_paper_broker", lambda settings: Adapter())
    settings = Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / "report.db").as_posix(),
        auto_create_tables=True,
        auth_required=False,
        trading_mode="PAPER",
    )
    with TestClient(create_app(settings)) as client:
        first = client.get("/api/v1/report/trades", params={"period": "today"})
        second = client.get("/api/v1/report/trades", params={"period": "today"})
    assert first.status_code == 200
    body = first.json()
    assert body["available"] is True
    assert second.json()["summary"] == body["summary"]
    assert seen["calls"] == 1
    start = date.fromisoformat(body["window"]["start"])
    assert date.fromisoformat(seen["after"]) == start - timedelta(days=21)


def _index_names(engine) -> list[str]:
    with engine.connect() as connection:
        return [row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")]


def _assert_one_each(engine) -> None:
    names = _index_names(engine)
    assert names.count("ix_broker_orders_broker_order_id") == 1
    assert names.count("ix_recommendations_underlying_decision") == 1
