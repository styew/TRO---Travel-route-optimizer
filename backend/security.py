"""Small, dependency-free production safeguards for costly public endpoints."""

from __future__ import annotations

from collections import defaultdict, deque
from time import monotonic


class SlidingWindowRateLimiter:
    """Limit a request key in one process without storing personal data persistently.

    This is deliberately a local development and small-deployment protection.
    A multi-instance production deployment should replace it with a shared
    Redis or gateway limiter, because each application process has its own
    in-memory window.
    """

    def __init__(self, requests_per_minute: int) -> None:
        """Store a positive per-client budget and create empty time windows."""
        self._limit = max(1, requests_per_minute)
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allowed(self, key: str) -> bool:
        """Accept a request only when its one-minute window has capacity."""
        now = monotonic()
        window = self._events[key]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True
