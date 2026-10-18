from __future__ import annotations

import json
from pathlib import Path

RELEASES_PATH = Path(__file__).resolve().parent.parent / "config" / "releases.json"


def load_releases() -> dict:
    raw = json.loads(RELEASES_PATH.read_text(encoding="utf-8"))
    releases = list(raw.get("releases") or [])
    current = str(releases[0]["version"]) if releases else ""
    return {"current": current, "releases": releases}
