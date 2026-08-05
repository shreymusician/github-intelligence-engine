"""GitHub REST rate-limit interpretation — Checkpoint 1.1.d.

Owns RateLimitInfo (the parsed X-RateLimit-* headers) and the logic to
decide whether the caller should proactively wait before making another
request. Consumes RateLimitInfo objects that github_client.rest already
produces per response — this module makes no HTTP requests itself.

Per SYSTEM_ARCHITECTURE.md Sec7 Challenge 1 and the approved Checkpoint 1.1
plan's rate-limit strategy: GitHub's primary rate limit (5,000/hour,
authenticated) is a known, deterministic quota, not an unpredictable
failure - the correct response is proactive waiting, not retrying.
Retry-after-transient-failure logic is a separate concern (Checkpoint 1.1.e).

Deliberately excludes: retries, HTTP requests, PostgreSQL writes,
automatic wiring into github_client.rest's request path (Checkpoint 1.3's
orchestration layer calls this explicitly, between fetches, in a loop).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_SAFETY_MARGIN = 50


@dataclass(frozen=True)
class RateLimitInfo:
    """Parsed X-RateLimit-* response headers.

    Moved here from github_client.rest (Checkpoint 1.1.c) as a
    behavior-preserving refactor for Checkpoint 1.1.d: fields, parsing
    logic, and semantics are unchanged. github_client.rest now imports
    this class from here instead of defining it.
    """

    limit: int
    remaining: int
    reset_at: int  # epoch seconds
    used: int
    resource: str

    @classmethod
    def from_headers(cls, headers) -> "RateLimitInfo | None":
        required = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-RateLimit-Used")
        if not all(h in headers for h in required):
            return None
        return cls(
            limit=int(headers["X-RateLimit-Limit"]),
            remaining=int(headers["X-RateLimit-Remaining"]),
            reset_at=int(headers["X-RateLimit-Reset"]),
            used=int(headers["X-RateLimit-Used"]),
            resource=headers.get("X-RateLimit-Resource", "core"),
        )


class RateLimiter:
    """Decides whether a caller should proactively wait before its next request.

    Holds no per-request state — each method takes a fresh RateLimitInfo
    (from the most recent response) and answers a question about it.
    `safety_margin`, `sleep_fn`, and `time_fn` are constructor-injectable
    so callers (and tests) can control waiting behavior precisely.
    """

    def __init__(
        self,
        safety_margin: int = DEFAULT_SAFETY_MARGIN,
        sleep_fn: Callable[[float], None] = time.sleep,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        if safety_margin < 0:
            raise ValueError(f"safety_margin must be >= 0, got {safety_margin}")
        self._safety_margin = safety_margin
        self._sleep_fn = sleep_fn
        self._time_fn = time_fn

    def is_exhausted(self, rate_limit: RateLimitInfo) -> bool:
        """True if remaining quota is at or below the configured safety margin."""
        return rate_limit.remaining <= self._safety_margin

    def seconds_until_reset(self, rate_limit: RateLimitInfo) -> float:
        """Seconds until GitHub's quota window resets, floored at 0."""
        return max(0.0, rate_limit.reset_at - self._time_fn())

    def wait_if_needed(self, rate_limit: RateLimitInfo | None) -> bool:
        """Block until quota resets if remaining quota is at/below the safety margin.

        Returns True if it waited, False otherwise (including when
        `rate_limit` is None, e.g. an error response with no rate-limit
        headers). Does not retry any request - only blocks the calling
        thread. Retrying after the wait is the caller's responsibility
        (Checkpoint 1.3's orchestration, or 1.1.e for transient errors).
        """
        if rate_limit is None:
            log.debug("rate_limiter_skip reason=no_rate_limit_info")
            return False

        if not self.is_exhausted(rate_limit):
            return False

        wait_seconds = self.seconds_until_reset(rate_limit)
        log.warning(
            "rate_limiter_waiting remaining=%s safety_margin=%s wait_seconds=%.1f reset_at=%s",
            rate_limit.remaining,
            self._safety_margin,
            wait_seconds,
            rate_limit.reset_at,
        )
        if wait_seconds > 0:
            self._sleep_fn(wait_seconds)
        return True
