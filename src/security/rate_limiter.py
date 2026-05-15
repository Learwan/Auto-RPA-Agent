from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


@dataclass
class RateLimitEntry:
    timestamps: list[float] = field(default_factory=list)


class RateLimiter:
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._entries: dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        entry = self._entries[key]
        cutoff = now - self._window_seconds
        entry.timestamps = [ts for ts in entry.timestamps if ts > cutoff]
        if len(entry.timestamps) >= self._max_requests:
            return False
        entry.timestamps.append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.time()
        entry = self._entries[key]
        cutoff = now - self._window_seconds
        current = len([ts for ts in entry.timestamps if ts > cutoff])
        return max(0, self._max_requests - current)

    def reset(self, key: str | None = None) -> None:
        if key:
            self._entries.pop(key, None)
        else:
            self._entries.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self._limiter = RateLimiter(max_requests, window_seconds)

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"

        if not self._limiter.is_allowed(key):
            return Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self._limiter._window_seconds)},
            )

        response = await call_next(request)
        remaining = self._limiter.remaining(key)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
