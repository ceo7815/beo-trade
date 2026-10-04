#!/usr/bin/env bash
# Fail if a committed file looks like a live credential. Does not print the match.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python - <<'PY'
import re
import sys
from pathlib import Path

root = Path(".").resolve()
skip = {".git", "node_modules", ".next", ".venv", "data", "__pycache__", ".pytest_cache"}
patterns = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]
bad = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    if any(part in skip for part in path.parts):
        continue
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for pattern in patterns:
        if pattern.search(text):
            bad.append(str(path.relative_to(root)))
            break
if bad:
    print("SECRET SCAN FAILED")
    for name in bad:
        print(name)
    sys.exit(1)
print("SECRET SCAN PASS")
PY
