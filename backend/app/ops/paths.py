from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
HEARTBEAT = DATA_DIR / "worker_heartbeat.txt"
MONITOR_HEARTBEAT = DATA_DIR / "monitor_heartbeat.txt"
RECONCILE_HEARTBEAT = DATA_DIR / "reconcile_heartbeat.txt"
WORKER_LOCK = DATA_DIR / "worker.lock"
LAST_SCAN = DATA_DIR / "last_scan.json"
