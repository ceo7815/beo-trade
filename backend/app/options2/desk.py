"""How the Options 2 process selects its profile.

``TRADING_CONFIG_PATH`` stays blocked. This module only accepts the id
``v2610_40k``, which maps to the file shipped in the repo. A path from the
environment is never opened.

The process also drops the main book's Alpaca keys and database URL before
settings are read. Nothing here prints a key or a secret.
"""

from __future__ import annotations

import os
from pathlib import Path

PROFILE_ID = "v2610_40k"
PROFILE_FILE = "trading_v2610_40k.toml"
PAPER_HOST = "https://paper-api.alpaca.markets"


def profile_file(profile_id: str) -> Path:
    """The allowlisted toml. Any other id is refused."""
    if str(profile_id or "").strip() != PROFILE_ID:
        raise ValueError("OPTIONS2_PROFILE")
    path = Path(__file__).resolve().parents[1] / "config" / PROFILE_FILE
    if not path.is_file():
        raise ValueError("OPTIONS2_PROFILE")
    return path


def prepare_process() -> None:
    """Swap in Options 2 credentials when this process is that desk. No-op otherwise."""
    if os.environ.get("DESK_ID", "").strip() != "options2":
        return
    os.environ["ALPACA_API_KEY"] = os.environ.get("OPTIONS2_ALPACA_API_KEY", "")
    os.environ["ALPACA_API_SECRET"] = os.environ.get("OPTIONS2_ALPACA_API_SECRET", "")
    requested = os.environ.get("OPTIONS2_ALPACA_BASE_URL", "").strip().rstrip("/")
    if requested and requested != PAPER_HOST:
        os.environ["OPTIONS2_HOST_REFUSED"] = "1"
        os.environ["ALPACA_BASE_URL"] = PAPER_HOST
    else:
        os.environ.pop("OPTIONS2_HOST_REFUSED", None)
        os.environ["ALPACA_BASE_URL"] = requested or PAPER_HOST
    # Own sqlite on this process's data volume. Never the main DATABASE_URL.
    os.environ["DATABASE_URL"] = ""
    if os.environ.get("TRADING_CONFIG_PATH", "").strip():
        os.environ["OPTIONS2_CONFIG_PATH_BLOCKED"] = "1"
        os.environ["TRADING_CONFIG_PATH"] = ""


def host_refused() -> bool:
    return os.environ.get("OPTIONS2_HOST_REFUSED") == "1"


def config_path_blocked() -> bool:
    return os.environ.get("OPTIONS2_CONFIG_PATH_BLOCKED") == "1"
