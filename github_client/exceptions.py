"""Typed exceptions for the GitHub REST client — Checkpoint 1.1.c.

Callers (Checkpoint 1.1.d's rate limiter, 1.1.e's retry logic, and
eventually Checkpoint 1.3's acquisition pipeline) need to distinguish
these failure modes programmatically rather than by parsing message
strings or HTTP status codes themselves.
"""

from __future__ import annotations


class GitHubClientError(Exception):
    """Base class for all errors raised by github_client."""


class RepositoryNotFoundError(GitHubClientError):
    """The requested repository does not exist, is private, or was renamed (404)."""

    def __init__(self, owner: str, repo: str) -> None:
        self.owner = owner
        self.repo = repo
        super().__init__(f"Repository not found or inaccessible: {owner}/{repo}")


class AuthenticationError(GitHubClientError):
    """The configured token was rejected by GitHub (401)."""

    def __init__(self, message: str = "GitHub rejected the configured token (401)") -> None:
        super().__init__(message)


class RateLimitExceededError(GitHubClientError):
    """The primary rate limit has been exhausted (403 with rate-limit signal).

    This client does not retry or wait on this condition (Checkpoint 1.1.d's
    responsibility) — it only classifies the failure so a future caller can.
    """

    def __init__(self, reset_at: int | None) -> None:
        self.reset_at = reset_at
        super().__init__(f"GitHub rate limit exceeded; resets at epoch {reset_at}")


class GitHubAPIError(GitHubClientError):
    """Any other API failure: unexpected status code, malformed response,
    or a network-level failure (timeout, connection error, DNS failure).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)
