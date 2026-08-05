"""Exponential backoff with jitter — Checkpoint 1.1.e.

Retries transient failures (network errors, 5xx, secondary rate-limit
403s) that github_client.rest raises as GitHubAPIError. Never retries
RepositoryNotFoundError (404), AuthenticationError (401), or
RateLimitExceededError (primary rate-limit exhaustion - Checkpoint
1.1.d's RateLimiter is the correct response to that, not a retry loop).

No HTTP logic, no database code, no GraphQL code. Not wired into
github_client.rest's source - same pattern as 1.1.d's RateLimiter:
a caller (Checkpoint 1.3's orchestration, or ad hoc use) applies this
decorator/helper explicitly.

Known, documented limitations (see CHECKPOINT_1_1E_REPORT.md Sec on
ambiguities): GitHubAPIError does not distinguish "malformed response"
from "network failure" (both retried uniformly), and does not expose
GitHub's Retry-After header (this module's own computed exponential
delay is used for every retryable failure instead).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Callable, TypeVar

from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from logging_setup import get_logger

log = get_logger(__name__)

T = TypeVar("T")

_NEVER_RETRY = (RepositoryNotFoundError, AuthenticationError, RateLimitExceededError)


def _is_retryable(exc: Exception) -> bool:
    """GitHubAPIError is retried; the three named exceptions above and
    anything else (unexpected bugs) are not."""
    if isinstance(exc, _NEVER_RETRY):
        return False
    return isinstance(exc, GitHubAPIError)


@dataclass(frozen=True)
class RetryPolicy:
    """Configurable exponential-backoff-with-jitter policy.

    `sleep_fn`/`time_fn`/`random_fn` are constructor-injectable so tests
    can exercise multi-attempt backoff sequences deterministically,
    without real sleeping or real randomness.
    """

    max_attempts: int = 5
    initial_delay: float = 1.0
    multiplier: float = 2.0
    jitter: float = 0.5  # fraction of the computed delay added as random jitter, [0, 1]
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.initial_delay < 0:
            raise ValueError(f"initial_delay must be >= 0, got {self.initial_delay}")
        if self.multiplier < 1:
            raise ValueError(f"multiplier must be >= 1, got {self.multiplier}")
        if not (0 <= self.jitter <= 1):
            raise ValueError(f"jitter must be in [0, 1], got {self.jitter}")

    def delay_for_attempt(self, attempt_index: int) -> float:
        """Backoff delay before retry attempt `attempt_index` (0-based: the
        delay before the *second* overall attempt is `delay_for_attempt(0)`).

        base = initial_delay * multiplier ** attempt_index
        final = base + random_fn() * jitter * base   (equal-jitter style:
        the random component is bounded by `jitter` fraction of `base`,
        never exceeding it, so the sequence still grows monotonically in
        expectation while remaining non-deterministic per attempt).
        """
        base = self.initial_delay * (self.multiplier**attempt_index)
        return base + self.random_fn() * self.jitter * base

    def call(self, func: Callable[[], T]) -> T:
        """Invoke `func` (a zero-argument callable), retrying on retryable
        failures up to `max_attempts` total tries. Re-raises the final
        exception if all attempts are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return func()
            except Exception as exc:  # noqa: BLE001 - classification happens below, not here
                if not _is_retryable(exc):
                    raise
                last_exc = exc
                if attempt == self.max_attempts:
                    log.error(
                        "retry_exhausted attempts=%s max_attempts=%s error=%s",
                        attempt,
                        self.max_attempts,
                        exc.__class__.__name__,
                    )
                    raise
                delay = self.delay_for_attempt(attempt - 1)
                log.warning(
                    "retry_attempt attempt=%s max_attempts=%s delay_s=%.2f reason=%s",
                    attempt,
                    self.max_attempts,
                    delay,
                    exc.__class__.__name__,
                )
                self.sleep_fn(delay)

        # Unreachable: the loop above always returns or raises. Kept only
        # to satisfy static type checkers that last_exc could be re-raised.
        assert last_exc is not None
        raise last_exc


def with_retry(policy: RetryPolicy | None = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form of RetryPolicy.call, for wrapping methods/functions
    that take arbitrary arguments (e.g. GitHubRESTClient.get_repository).

    Usage:
        limiter_policy = RetryPolicy(max_attempts=3)

        @with_retry(limiter_policy)
        def fetch(owner, repo):
            return client.get_repository(owner, repo)
    """
    active_policy = policy or RetryPolicy()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            return active_policy.call(lambda: func(*args, **kwargs))

        return wrapper

    return decorator
