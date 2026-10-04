import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.config.settings import Settings, load_trading_config
from app.core.sessions import OPEN
from app.ops.lock import acquire_single_worker_lock
from app.ops.safety import assert_boot_safe, policy_errors
from app.workers.runner import cycle_plan


def test_official_policy_is_intact():
    assert policy_errors(load_trading_config()) == []
    assert_boot_safe(Settings())


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
