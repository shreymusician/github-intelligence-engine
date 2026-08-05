"""GitHub API client package — Checkpoint 1.1.

Implements the REST client (1.1.c) and rate-limit interpretation
(1.1.d). GraphQL (1.1.f) and retry logic (1.1.e) are added in later
sub-checkpoints.
"""

from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    GitHubClientError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from github_client.rate_limiter import DEFAULT_SAFETY_MARGIN, RateLimitInfo, RateLimiter
from github_client.rest import GitHubRESTClient, OwnerSummary, RepositoryData

__all__ = [
    "GitHubRESTClient",
    "RepositoryData",
    "OwnerSummary",
    "RateLimitInfo",
    "RateLimiter",
    "DEFAULT_SAFETY_MARGIN",
    "GitHubClientError",
    "RepositoryNotFoundError",
    "AuthenticationError",
    "RateLimitExceededError",
    "GitHubAPIError",
]
