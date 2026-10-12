from __future__ import annotations

import hmac
import re
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex"
        return response


OPEN_PATHS = {"/health", "/ready"}
READ_PATHS = (
    "/api/v1/system",
    "/api/v1/desk",
    "/api/v1/decisions",
    "/api/v1/finance",
    "/api/v1/risk",
    "/api/v1/report/trades",
    "/api/v1/learning/trades",
    "/api/v1/learning/path",
    "/api/v1/learning/lab",
    "/api/v1/ai/summary",
    "/api/v1/audit",
    "/api/v1/policy",
    "/api/v1/alerts",
    "/api/v1/recommendations",
    re.compile(r"^/api/v1/recommendations/[\w-]+$"),
    "/api/v1/broker/alpaca/positions",
    "/api/v1/broker/alpaca/orders",
    "/api/v1/broker/alpaca/fills",
    "/api/v1/broker/alpaca/clock",
)


def presented_key(request: Request) -> str:
    header = request.headers.get("x-beo-key", "").strip()
    if header:
        return header
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.query_params.get("key", "").strip()


def readable(path: str) -> bool:
    return any(path == rule if isinstance(rule, str) else rule.match(path) for rule in READ_PATHS)


def _same(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """With API_ADMIN_KEY set, every call needs a key. The read key only reads, and only from READ_PATHS."""

    def __init__(self, app, admin_key: str, read_key: str = "") -> None:
        super().__init__(app)
        self.admin_key = admin_key
        self.read_key = read_key

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if not self.admin_key or path in OPEN_PATHS or request.method == "OPTIONS":
            return await call_next(request)
        key = presented_key(request)
        if _same(key, self.admin_key):
            return await call_next(request)
        if _same(key, self.read_key):
            if request.method in {"GET", "HEAD"} and readable(path):
                return await call_next(request)
            return JSONResponse({"detail": "מפתח הצפייה מאפשר קריאה בלבד"}, status_code=403)
        return JSONResponse({"detail": "נדרש מפתח גישה"}, status_code=401)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int = 120, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in {"/health", "/ready", "/metrics"}:
            return await call_next(request)
        now = time.time()
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        key = forwarded or (request.client.host if request.client else "local")
        recent = [stamp for stamp in self.hits[key] if now - stamp < self.window]
        if len(recent) >= self.limit:
            return JSONResponse({"detail": "יותר מדי בקשות"}, status_code=429)
        recent.append(now)
        self.hits[key] = recent
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - started) * 1000))
        return response
