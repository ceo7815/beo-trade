#!/usr/bin/env bash
# Switch the running code to an older Git revision and recreate containers.
# The database volume is not deleted and schema is not downgraded.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REV="${1:-}"

if [[ -z "$REV" ]]; then
  echo "Usage: scripts/rollback.sh <git-revision>"
  exit 1
fi

if [[ ! -d .git ]]; then
  echo "This directory is not a Git checkout."
  exit 1
fi

bash "$ROOT/scripts/validate-env.sh" .env
current="$(git rev-parse --short HEAD)"
git fetch --tags --force || true
git switch --detach "$REV"
docker compose build
docker compose up -d

cat <<EOF
Rolled application code from ${current} to $(git rev-parse --short HEAD).
The beo_data volume was not removed.
SQLite migrations only create missing tables. They do not drop columns or rows.
If the older code cannot read a newer table, restore the previous Git revision. Do not delete the database.
EOF
