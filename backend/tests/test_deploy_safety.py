import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import pytest

from app.config.settings import Settings, load_trading_config
from app.core.sessions import OPEN
from app.ops.lock import acquire_single_worker_lock
from app.ops.safety import OFFICIAL_POLICY, assert_boot_safe, policy_errors
from app.workers.runner import cycle_plan


def test_official_policy_is_intact():
    assert policy_errors(load_trading_config()) == []
    assert_boot_safe(Settings())


def _smoke_policy_source() -> str:
    script = (Path(__file__).resolve().parents[2] / "scripts" / "smoke-test.sh").read_text(encoding="utf-8")
    return script.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]


def _run_smoke_policy(monkeypatch, *, official_exit: str, reported_exit: str, official_risk: str, reported_risk: str) -> None:
    monkeypatch.setitem(OFFICIAL_POLICY, "exit_policy_version", official_exit)
    monkeypatch.setitem(OFFICIAL_POLICY, "risk_policy_version", official_risk)
    ready = {
        "components": {"thetadata": "BLOCKED", "risk_engine": "HEALTHY", "exit_engine": "HEALTHY"},
        "trading_mode": "PAPER",
        "paper_only": True,
        "risk_policy_version": reported_risk,
        "exit_policy_version": reported_exit,
        "autonomous": False,
        "database": "ok",
    }
    health = {"components": {"paper": "HEALTHY", "live": "DISABLED"}}
    bodies = iter([ready, health])

    def urlopen(url, timeout=10):
        return BytesIO(json.dumps(next(bodies)).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    exec(compile(_smoke_policy_source(), "scripts/smoke-test.sh", "exec"), {"__name__": "smoke"})


def test_smoke_script_reads_policy_versions_from_the_official_source(monkeypatch):
    _run_smoke_policy(
        monkeypatch,
        official_exit="aggressive-exit-9",
        reported_exit="aggressive-exit-9",
        official_risk="risk-from-source",
        reported_risk="risk-from-source",
    )


def test_smoke_script_rejects_a_stale_exit_policy(monkeypatch):
    with pytest.raises(SystemExit, match="exit version"):
        _run_smoke_policy(
            monkeypatch,
            official_exit="aggressive-exit-9",
            reported_exit="aggressive-exit-1",
            official_risk="risk-from-source",
            reported_risk="risk-from-source",
        )


def test_the_release_ledger_starts_with_the_current_version():
    from app.ops.releases import load_releases

    body = load_releases()
    assert body["current"] == body["releases"][0]["version"]
    assert body["releases"][0]["changes"]


def test_live_alpaca_host_is_rejected():
    with pytest.raises(ValueError):
        Settings(alpaca_base_url="https://api.alpaca.markets")


def test_paper_only_false_is_rejected():
    with pytest.raises(ValueError):
        Settings(paper_only=False)


def test_non_paper_mode_is_rejected():
    with pytest.raises(ValueError):
        Settings(trading_mode="LIVE")


def test_theta_host_builds_base_url_without_assuming_localhost():
    settings = Settings(theta_terminal_host="10.0.0.8", theta_terminal_port=25503)
    assert settings.thetadata_base_url == "http://10.0.0.8:25503"


def test_explicit_theta_url_wins():
    settings = Settings(thetadata_base_url="http://theta.internal:25510", theta_terminal_host="10.0.0.8")
    assert settings.thetadata_base_url == "http://theta.internal:25510"


def test_shutdown_stops_new_entries_and_keeps_monitor():
    plan = cycle_plan(stop=True, halted=False, phase=OPEN, scan_due=True, monitor_due=False, reconcile_due=True)
    assert plan == {"reconcile": False, "scan": False, "monitor": True}


def test_halt_stops_scan_and_keeps_monitor():
    plan = cycle_plan(stop=False, halted=True, phase=OPEN, scan_due=True, monitor_due=True, reconcile_due=True)
    assert plan["scan"] is False
    assert plan["monitor"] is True


def test_second_worker_cannot_take_the_lock(tmp_path):
    lock = tmp_path / "worker.lock"
    code = (
        "import time\n"
        "from pathlib import Path\n"
        "from app.ops.lock import acquire_single_worker_lock\n"
        f"held = acquire_single_worker_lock(Path({str(lock)!r}))\n"
        "time.sleep(20)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.time() + 8
        while time.time() < deadline and proc.poll() is None and not lock.exists():
            time.sleep(0.05)
        time.sleep(0.4)
        assert proc.poll() is None
        with pytest.raises(SystemExit):
            acquire_single_worker_lock(lock)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
