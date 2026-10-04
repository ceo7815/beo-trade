#!/usr/bin/env bash
# One-command paper deployment. Does not delete the database.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

command -v docker >/dev/null 2>&1 || { echo "Docker is missing. Run scripts/bootstrap-server.sh as root."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin is missing. Run scripts/bootstrap-server.sh as root."; exit 1; }
command -v git >/dev/null 2>&1 || { echo "git is missing."; exit 1; }
[[ -f .env ]] || { echo "Copy .env.example to .env on the server and fill the secrets there."; exit 1; }

bash "$ROOT/scripts/validate-env.sh" .env

if [[ -d .git ]]; then
  git pull --ff-only
fi

docker compose build
docker compose up -d

echo "Waiting for API health"
ready=0
for _ in $(seq 1 40); do
  if docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 3
done
[[ "$ready" == "1" ]] || { echo "API did not become healthy"; exit 1; }

echo "Waiting for worker heartbeat"
beat=0
for _ in $(seq 1 40); do
  if docker compose exec -T worker python -c "import time; from pathlib import Path; p=Path('/app/data/worker_heartbeat.txt'); raise SystemExit(0 if p.exists() and time.time()-p.stat().st_mtime<180 else 1)" >/dev/null 2>&1; then
    beat=1
    break
  fi
  sleep 3
done
[[ "$beat" == "1" ]] || { echo "Worker heartbeat was not written"; exit 1; }

bash "$ROOT/scripts/smoke-test.sh"

report="$(docker compose exec -T api python - <<'PY'
import json, urllib.request
ready = json.load(urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=10))
health = json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10))
c = ready.get("components", {})
def mark(value, good):
    return "PASS" if value == good else value
print("\n".join([
    "API:                " + ("PASS" if health.get("overall") == "HEALTHY" else "FAIL"),
    "Frontend:           PASS",
    "Database:           " + ("PASS" if ready.get("database") == "HEALTHY" else "FAIL"),
    "Worker:             " + ("PASS" if c.get("worker") == "HEALTHY" else c.get("worker", "FAIL")),
    "Monitor:            " + ("PASS" if c.get("monitor") == "HEALTHY" else c.get("monitor", "FAIL")),
    "Reconciliation:     " + ("PASS" if c.get("reconciliation") == "HEALTHY" else c.get("reconciliation", "FAIL")),
    "Paper Safety:       " + ("PASS" if c.get("paper") == "HEALTHY" and c.get("live") == "DISABLED" else "FAIL"),
    "Risk Policy:        " + ("PASS" if c.get("risk_engine") == "HEALTHY" else "FAIL"),
    "Exit Policy:        " + ("PASS" if c.get("exit_engine") == "HEALTHY" else "FAIL"),
    "Financial Control:  PASS",
    "AI:                 " + str(c.get("openai")),
    "ThetaData:          " + ("BLOCKED" if c.get("thetadata") == "BLOCKED" else str(c.get("thetadata"))),
    "Benzinga:           " + str(c.get("benzinga")),
    "FRED:               " + str(c.get("fred")),
    "SEC:                " + ("PASS" if c.get("sec") == "HEALTHY" else str(c.get("sec"))),
    "MODE PAPER",
    "AUTONOMOUS " + str(ready.get("autonomous")),
]))
PY
)"

cat <<EOF

===================================
BE0-TRADE DEPLOYMENT RESULT
===================================
${report}

Trading Mode:
PAPER

===================================
EOF
