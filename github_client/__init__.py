"""GitHub API client package — Checkpoint 1.1.

Implements the REST client (1.1.c), rate-limit interpretation (1.1.d),
retry/backoff (1.1.e), and the GraphQL client (1.1.f).
"""

from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    GitHubClientError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from github_client.graphql import GitHubGraphQLClient
from github_client.rate_limiter import DEFAULT_SAFETY_MARGIN, RateLimitInfo, RateLimiter
from github_client.rest import GitHubRESTClient, OwnerSummary, RepositoryData
from github_client.retry import RetryPolicy, with_retry

__all__ = [
    "GitHubRESTClient",
    "GitHubGraphQLClient",
    "RepositoryData",
    "OwnerSummary",
    "RateLimitInfo",
    "RateLimiter",
    "DEFAULT_SAFETY_MARGIN",
    "RetryPolicy",
    "with_retry",
    "GitHubClientError",
    "RepositoryNotFoundError",
    "AuthenticationError",
    "RateLimitExceededError",
    "GitHubAPIError",
]
