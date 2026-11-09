"""Three desk systems. Options, Eli, and Avi each have their own status path."""

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
        "id": "avi",
        "label": "מסחר אופציות אבי",
        "label_en": "Avi options",
        "status_path": "/api/v1/avi/status",
        "engine": "avi",
        "ready": False,
    },
)


def avi_row() -> dict:
    """Ready follows the engine flag. The status path is always the Avi route."""
    from app.avi.api import ready

    return {
        "id": "avi",
        "label": "מסחר אופציות אבי",
        "label_en": "Avi options",
        "status_path": "/api/v1/avi/status",
        "engine": "avi",
        "ready": ready(),
    }


def register(app: FastAPI) -> None:
    @app.get("/api/v1/systems")
    def systems():
        rows = []
        for row in SYSTEMS:
            rows.append(avi_row() if row["id"] == "avi" else dict(row))
        return {"systems": rows}
