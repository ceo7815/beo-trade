"""Local copy stays bounded, one run at a time, and a dead process does not leave running forever."""

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.archive.job import _pull_local_source, recover_stale_runs
from app.archive.tables import ArchiveLocal, ArchiveRun
from app.config.settings import Settings
from app.main import create_app
from app.models.db import session_scope
from app.models.tables import BrokerFillRecord

ADMIN = "admin-key-0123456789abcdef"
READ = "read-key-0123456789abcdef"


def _settings(tmp_path, name: str) -> Settings:
    return Settings(
        app_env="local",
        database_url="sqlite:///" + (tmp_path / name).as_posix(),
        auto_create_tables=True,
        auth_required=False,
        openai_price_input_per_million=Decimal("0"),
        openai_price_output_per_million=Decimal("0"),
        api_admin_key=ADMIN,
        api_read_key=READ,
    )


def _fill(index: int) -> BrokerFillRecord:
    return BrokerFillRecord(
        id=f"fill-{index:04d}",
        environment="PAPER",
        execution_id=f"exec-{index:04d}",
        trade_id="",
        broker_order_id="",
        symbol="SMCI",
        payload={"execution_id": f"exec-{index:04d}", "symbol": "SMCI", "timestamp": "2026-10-16T15:00:00Z"},
        observed_at=datetime(2026, 10, 16, 15, 0),
    )


def test_local_rows_are_copied_in_batches_and_not_duplicated(tmp_path):
    with TestClient(create_app(_settings(tmp_path, "batch.db"))) as client:
        with session_scope() as session:
            session.add_all(_fill(index) for index in range(5))
            session.commit()
            session.expunge_all()
            sizes: list[int] = []
            real_commit = session.commit

            def watching():
                sizes.append(len(session.identity_map))
                return real_commit()

            session.commit = watching
            kept = _pull_local_source(
                session,
                (BrokerFillRecord, "broker_fills", "observed_at", "id"),
                datetime.now(timezone.utc),
                batch_size=2,
            )
            assert kept == 5
            assert len(sizes) >= 3
            assert max(sizes) < 8
            again = _pull_local_source(
                session,
                (BrokerFillRecord, "broker_fills", "observed_at", "id"),
                datetime.now(timezone.utc),
                batch_size=2,
            )
            assert again == 5
            assert session.query(ArchiveLocal).count() == 5
        listed = client.get("/api/v1/archive/paper_100k/trades", headers={"X-Beo-Key": READ})
        assert listed.status_code == 200
        assert listed.json()["total"] == 5


def test_a_second_post_returns_the_run_already_in_progress(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def hold(_settings, _run_id):
        started.set()
        release.wait(5)

    monkeypatch.setattr("app.archive.job.finish_archive_run", hold)
    try:
        with TestClient(create_app(_settings(tmp_path, "once.db"))) as client:
            headers = {"X-Beo-Key": ADMIN}
            first = client.post("/api/v1/archive/paper_100k/run", headers=headers)
            assert first.status_code == 202, first.text
            assert started.wait(2)
            health = client.get("/health")
            assert health.status_code == 200
            second = client.post("/api/v1/archive/paper_100k/run", headers=headers)
            assert second.status_code == 200, second.text
            assert second.json()["already_running"] is True
            assert second.json()["run"]["id"] == first.json()["run"]["id"]
    finally:
        release.set()


def test_startup_and_a_new_run_close_an_interrupted_job(tmp_path):
    now = datetime.now(timezone.utc)
    app = create_app(_settings(tmp_path, "stale.db"))
    with TestClient(app):
        with session_scope() as session:
            session.add(ArchiveRun(id="stuck-old", started_at=now - timedelta(minutes=16), status="running", detail=""))
            session.add(ArchiveRun(id="stuck-fresh", started_at=now - timedelta(minutes=1), status="running", detail=""))
            session.commit()
    with TestClient(app):
        with session_scope() as session:
            old = session.get(ArchiveRun, "stuck-old")
            fresh = session.get(ArchiveRun, "stuck-fresh")
            assert old.status == "failed"
            assert old.detail == "interrupted"
            assert old.finished_at is not None
            assert fresh.status == "failed"
            assert fresh.detail == "interrupted"
            assert fresh.finished_at is not None
            alive = ArchiveRun(id="alive", started_at=now - timedelta(minutes=1), status="running", detail="")
            session.add(alive)
            session.commit()
            assert recover_stale_runs(session, now=now, own_ids={"alive"}) == 0
            assert session.get(ArchiveRun, "alive").status == "running"
            assert recover_stale_runs(session, now=now + timedelta(minutes=16), own_ids={"alive"}) == 1
            closed = session.get(ArchiveRun, "alive")
            assert closed.status == "failed"
            assert closed.detail == "interrupted"
            assert closed.finished_at is not None
