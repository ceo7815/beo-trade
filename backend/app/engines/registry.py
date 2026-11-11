"""Desk switcher. Options, Eli, and Options 2. Avi stays as a read-only archive route."""

from __future__ import annotations

from fastapi import FastAPI

SYSTEMS = (
    {
        "id": "options",
        "label": "אופציות",
        "label_en": "Options",
        "status_path": "/api/v1/system",
        "ready": True,
    },
    {
        "id": "eli",
        "label": "מסחר יומי אלי",
        "label_en": "Eli intraday",
        "status_path": "/api/v1/spy/status",
        "engine": "spy",
        "ready": True,
    },
    {
        "id": "options2",
        "label": "אופציות 2",
        "label_en": "Options 2",
        "status_path": "/api/v1/system",
        "engine": "options2",
        "ready": False,
    },
)


def avi_row() -> dict:
    """Archive row. Not part of the switcher. The status path stays the Avi route."""
    from app.avi.api import ready

    return {
        "id": "avi",
        "label": "מסחר אופציות אבי",
        "label_en": "Avi options",
        "status_path": "/api/v1/avi/status",
        "engine": "avi",
        "ready": ready(),
        "archived": True,
    }


def register(app: FastAPI) -> None:
    @app.get("/api/v1/systems")
    def systems():
        from app.config.settings import get_settings

        here = get_settings().desk_id.strip() or "options"
        rows = []
        for row in SYSTEMS:
            item = dict(row)
            if item["id"] == "options2" and here != "options2":
                item["ready"] = False
                item["remote"] = True
                item["status_path"] = None
            elif item["id"] == "options" and here == "options2":
                item["ready"] = False
                item["remote"] = True
                item["status_path"] = None
            elif item["id"] == "options2":
                item["ready"] = True
            rows.append(item)
        return {"systems": rows}
