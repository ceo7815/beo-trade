from __future__ import annotations

import json
import re
from pathlib import Path

from app.config.settings import Settings

SECRET_NAMES = (
    "openai_api_key",
    "alpaca_api_key",
    "alpaca_api_secret",
    "thetadata_api_key",
    "thetadata_base_url",
    "benzinga_api_key",
    "fred_api_key",
    "database_url",
    "redis_url",
)


def secrets_path(settings: Settings) -> Path:
    path = Path(__file__).resolve().parents[2] / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path / "integration_secrets.json"


def _read_file(settings: Settings) -> dict[str, str]:
    path = secrets_path(settings)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if key in SECRET_NAMES and str(value)}


def _env_value(settings: Settings, name: str) -> str:
    return str(getattr(settings, name, "") or "")


def resolve_secret(settings: Settings, name: str) -> tuple[str, str]:
    env_value = _env_value(settings, name)
    if env_value:
        return env_value, "environment"
    file_value = _read_file(settings).get(name, "")
    if file_value:
        return file_value, "saved"
    return "", "missing"


def save_secrets(settings: Settings, values: dict[str, str]) -> None:
    current = _read_file(settings)
    for name, value in values.items():
        if name not in SECRET_NAMES:
            continue
        if _env_value(settings, name):
            continue
        cleaned = value.strip()
        if cleaned:
            current[name] = cleaned
    path = secrets_path(settings)
    path.write_text(json.dumps(current), encoding="utf-8")


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) < 8:
        return "••••"
    return f"{value[:3]}••••••••••••{value[-4:]}"


def scrub(text: str, secrets: list[str] | None = None) -> str:
    cleaned = re.sub(r"(token|api_key|apikey|secret)=([^&\s]+)", r"\1=••••", text, flags=re.IGNORECASE)
    for secret in secrets or []:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "••••")
    return cleaned[:400]
