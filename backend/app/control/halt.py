from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

HALT_PATH = Path(__file__).resolve().parents[2] / "data" / "trading_halt.txt"


def is_halted() -> bool:
    try:
        text = HALT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return text.startswith("1")


def set_halted(halted: bool) -> dict:
    HALT_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    HALT_PATH.write_text(("1" if halted else "0") + "\n" + stamp + "\n", encoding="utf-8")
    return {"halted": halted, "updated_at": stamp}


def order_blocked_by_halt(intent: str, halted: bool) -> str | None:
    """A halt stops new buys. A protective sell stays available."""
    if halted and intent == "buy_to_open":
        return "כניסות חדשות כבויות. לא נשלחת פקודת קנייה."
    return None


def halt_view() -> dict:
    updated_at = None
    if HALT_PATH.exists():
        lines = HALT_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > 1:
            updated_at = lines[1]
    return {"halted": is_halted(), "updated_at": updated_at}
