from __future__ import annotations

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
        key = request.client.host if request.client else "local"
        recent = [stamp for stamp in self.hits[key] if now - stamp < self.window]
        if len(recent) >= self.limit:
            return JSONResponse({"detail": "יותר מדי בקשות"}, status_code=429)
        recent.append(now)
        self.hits[key] = recent
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - started) * 1000))
        return response
