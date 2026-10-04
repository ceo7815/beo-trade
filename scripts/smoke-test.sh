#!/usr/bin/env bash
# Readiness check. Does not submit an order.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
WEB_PORT="${WEB_PORT:-3000}"

echo "SMOKE: waiting for API and worker"
ready=0
for _ in $(seq 1 40); do
  if docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" >/dev/null 2>&1 \
    && docker compose exec -T worker python -c "import time; from pathlib import Path; p=Path('/app/data/worker_heartbeat.txt'); raise SystemExit(0 if p.exists() and time.time()-p.stat().st_mtime<180 else 1)" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 3
done
[[ "$ready" == "1" ]] || { echo "SMOKE FAILED: API or worker did not become ready"; exit 1; }

echo "SMOKE: api health"
docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read()" >/dev/null

echo "SMOKE: paper safety and readiness"
docker compose exec -T api python - <<'PY'
import json
import urllib.request

ready = json.load(urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=10))
health = json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10))
errors = []
if health.get("components", {}).get("paper") != "HEALTHY":
    errors.append("paper")
if health.get("components", {}).get("live") != "DISABLED":
    errors.append("live")
if ready.get("trading_mode") != "PAPER" or ready.get("paper_only") is not True:
    errors.append("mode")
if ready.get("components", {}).get("thetadata") == "HEALTHY":
    errors.append("theta claimed healthy without a quote")
if ready.get("components", {}).get("risk_engine") != "HEALTHY":
    errors.append("risk")
if ready.get("components", {}).get("exit_engine") != "HEALTHY":
    errors.append("exit")
if ready.get("risk_policy_version") != "aggressive-1":
    errors.append("risk version")
if ready.get("exit_policy_version") != "aggressive-exit-1":
    errors.append("exit version")
if errors:
    raise SystemExit("SMOKE FAILED: " + ", ".join(errors))
print("AUTONOMOUS=" + str(ready.get("autonomous")))
print("THETADATA=" + str(ready.get("components", {}).get("thetadata")))
print("BENZINGA=" + str(ready.get("components", {}).get("benzinga")))
print("OPENAI=" + str(ready.get("components", {}).get("openai")))
print("WORKER=" + str(ready.get("components", {}).get("worker")))
print("MONITOR=" + str(ready.get("components", {}).get("monitor")))
print("RECONCILIATION=" + str(ready.get("components", {}).get("reconciliation")))
print("DATABASE=" + str(ready.get("database")))
PY

echo "SMOKE: worker heartbeat"
docker compose exec -T worker python -c "import time; from pathlib import Path; p=Path('/app/data/worker_heartbeat.txt'); raise SystemExit(0 if p.exists() and time.time()-p.stat().st_mtime<180 else 1)"

echo "SMOKE: web"
web_ok=0
for _ in $(seq 1 20); do
  if command -v curl >/dev/null 2>&1; then
    if curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${WEB_PORT}" >/dev/null; then
      web_ok=1
      break
    fi
  elif docker compose exec -T web node -e "fetch('http://127.0.0.1:3000').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"; then
    web_ok=1
    break
  fi
  sleep 3
done
[[ "$web_ok" == "1" ]] || { echo "SMOKE FAILED: web did not answer"; exit 1; }

echo "SMOKE PASS"
echo "No order was submitted."
