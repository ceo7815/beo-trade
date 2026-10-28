"""Fields for a caught worker failure. No query string and no secrets."""

from __future__ import annotations

import re
import traceback
from urllib.parse import urlsplit

import structlog

log = structlog.get_logger()

_QUERY = re.compile(r"(https?://[^\s?'\"]+)\?[^\s'\"]*")
_SECRET = re.compile(
    r"(api[_-]?key|secret|token|apca-api-[a-z-]+|authorization)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def failure_fields(exc: BaseException) -> dict:
    request, status_code = _http_parts(exc)
    host = ""
    method = ""
    path = ""
    if request is not None:
        url = getattr(request, "url", None)
        method = str(getattr(request, "method", "") or "")
        if url is not None:
            split = urlsplit(str(url))
            host = split.hostname or ""
            path = split.path or ""
    return {
        "error": type(exc).__name__,
        "message": _safe_message(exc),
        "upstream": _upstream(host, path),
        "host": host or None,
        "method": method or None,
        "path": path or None,
        "status_code": status_code,
        "traceback": _summary(exc),
    }


def log_failure(event: str, exc: BaseException, **fields) -> None:
    log.warning(event, **failure_fields(exc), **fields)


def _http_parts(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        request = getattr(current, "request", None)
        response = getattr(current, "response", None)
        if request is not None or response is not None:
            status = getattr(response, "status_code", None)
            return request, status if isinstance(status, int) else None
        current = current.__cause__ or current.__context__
    return None, None


def _upstream(host: str, path: str) -> str | None:
    lowered = host.lower()
    if "alpaca" in lowered:
        return "alpaca"
    if "benzinga" in lowered:
        return "benzinga"
    if "openai" in lowered:
        return "openai"
    if "theta" in lowered or path.startswith("/v3/"):
        return "thetadata"
    return None


def _safe_message(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    text = _QUERY.sub(r"\1", text)
    text = _SECRET.sub(r"\1=••••", text)
    return text[:400]


def _summary(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)[-4:]
    if not frames:
        return ""
    return " | ".join(f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno}:{frame.name}" for frame in frames)
