"""Three desk systems. Options and Eli are live. Avi is a slot for a later engine."""

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
        "label": "מסחר יומי אבי",
        "label_en": "Avi intraday",
        "status_path": None,
        "engine": None,
        "ready": False,
        "note": "בפיתוח",
    },
)


def register(app: FastAPI) -> None:
    @app.get("/api/v1/systems")
    def systems():
        return {"systems": [dict(row) for row in SYSTEMS]}
