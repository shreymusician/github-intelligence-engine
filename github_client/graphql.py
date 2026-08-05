"""GitHub GraphQL API client — Checkpoint 1.1.f.

Implements a generic, reusable query-execution method plus a
get_repository() convenience method mirroring github_client.rest's
REST equivalent (reusing its RepositoryData/OwnerSummary types, not
duplicating them). No acquisition pipeline, no repository crawling,
no multi-repo batching orchestration - those remain Checkpoint 1.3's
responsibility, once a real caller exists to decide the batch shape.

GraphQL differs from REST in two ways this module must handle:
1. Errors mostly arrive as HTTP 200 with a top-level "errors" array in
   the response body, not via HTTP status codes.
2. Rate-limit accounting is point-based, reported via a `rateLimit`
   field you must include in the query yourself - not via
   X-RateLimit-* headers (see _RATE_LIMIT_FIELD below).

Reuses config/settings.py, logging_setup.py, github_client.retry, and
github_client.rate_limiter without modifying any of them - RateLimitInfo
is constructed directly here (it's a plain dataclass with public
fields) rather than adding a new classmethod to rate_limiter.py.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from config.settings import Settings, get_settings
from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    RepositoryNotFoundError,
)
from github_client.rate_limiter import RateLimitInfo
from github_client.rest import OwnerSummary, RepositoryData
from logging_setup import get_logger

log = get_logger(__name__)

_API_URL = "https://api.github.com/graphql"
_TIMEOUT_SECONDS = 10

# Included in every query issued by this client so a RateLimitInfo can
# always be constructed from the response body, per GraphQL's
# point-based accounting (see module docstring).
_RATE_LIMIT_FIELD = """
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
"""

_REPOSITORY_QUERY = (
    """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    databaseId
    name
    nameWithOwner
    description
    primaryLanguage { name }
    licenseInfo { spdxId }
    isArchived
    isFork
    createdAt
    pushedAt
    owner {
      login
      __typename
      ... on User { databaseId avatarUrl }
      ... on Organization { databaseId avatarUrl }
    }
  }
"""
    + _RATE_LIMIT_FIELD
    + "}\n"
)


def _parse_iso8601_to_epoch(value: str) -> int:
    """Parse GitHub's ISO-8601 resetAt (e.g. '2026-08-06T01:23:45Z') to epoch seconds."""
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def _rate_limit_from_payload(rate_limit_payload: dict | None) -> RateLimitInfo | None:
    """Build a RateLimitInfo from a GraphQL response's `rateLimit` field.

    `used` is derived as limit - remaining, the same relationship that
    defines REST's X-RateLimit-Used header - not an invented shortcut.
    `resource` is hardcoded to "graphql", GitHub's separate point budget
    bucket, distinct from REST's "core".
    """
    if rate_limit_payload is None:
        return None
    limit = rate_limit_payload["limit"]
    remaining = rate_limit_payload["remaining"]
    return RateLimitInfo(
        limit=limit,
        remaining=remaining,
        reset_at=_parse_iso8601_to_epoch(rate_limit_payload["resetAt"]),
        used=limit - remaining,
        resource="graphql",
    )


class GitHubGraphQLClient:
    """Thin, synchronous wrapper around the GitHub GraphQL API (v4).

    Deliberately narrow: generic query execution plus one convenience
    method. No retry or rate-limit *waiting* logic lives here - callers
    compose github_client.retry.with_retry / github_client.rate_limiter.RateLimiter
    around this client's methods, exactly as they would around
    github_client.rest.GitHubRESTClient.
    """

    def __init__(self, settings: Settings | None = None, session: requests.Session | None = None) -> None:
        self._settings = settings or get_settings()
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a raw GraphQL query. Returns the response's `data` object.

        Raises AuthenticationError (HTTP 401), GitHubAPIError (any other
        unexpected HTTP status, network failure, or malformed response),
        or a mapped error from the response body's `errors` array (e.g.
        RepositoryNotFoundError for a NOT_FOUND-typed error).
        """
        body = {"query": query, "variables": variables or {}}
        started = time.monotonic()

        try:
            response = self._session.post(
                _API_URL, json=body, headers=self._headers(), timeout=_TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            log.error("github_graphql_request_failed error=%s", exc.__class__.__name__)
            raise GitHubAPIError(f"Network failure requesting {_API_URL}: {exc}") from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        if response.status_code == 401:
            log.info("github_graphql_response status=401 duration_ms=%s", duration_ms)
            raise AuthenticationError()

        if response.status_code != 200:
            log.info("github_graphql_response status=%s duration_ms=%s", response.status_code, duration_ms)
            raise GitHubAPIError(
                f"Unexpected status {response.status_code} for GraphQL request",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubAPIError(f"GraphQL response was not valid JSON: {exc}") from exc

        errors = payload.get("errors")
        if errors:
            self._raise_for_graphql_errors(errors)

        data = payload.get("data")
        if data is None:
            raise GitHubAPIError("GraphQL response had no 'data' and no 'errors' field")

        rate_limit = _rate_limit_from_payload(self._extract_rate_limit(data))
        log.info(
            "github_graphql_response status=200 duration_ms=%s rate_remaining=%s",
            duration_ms,
            rate_limit.remaining if rate_limit else "unknown",
        )

        return data

    @staticmethod
    def _extract_rate_limit(data: dict) -> dict | None:
        return data.get("rateLimit")

    @staticmethod
    def _raise_for_graphql_errors(errors: list[dict]) -> None:
        for error in errors:
            if error.get("type") == "NOT_FOUND":
                # Best-effort extraction of owner/name from the error path,
                # falling back to a generic message if the shape is unexpected.
                path = error.get("path") or []
                raise RepositoryNotFoundError(
                    owner=str(path[0]) if path else "unknown", repo=str(path[1]) if len(path) > 1 else "unknown"
                )
        raise GitHubAPIError(f"GraphQL query returned errors: {errors}")

    def get_repository(self, owner: str, repo: str) -> RepositoryData:
        """Fetch repository metadata via GraphQL, returning the same
        RepositoryData/OwnerSummary types github_client.rest.GitHubRESTClient
        returns - reused, not duplicated, so callers can treat REST and
        GraphQL results identically regardless of which path fetched them.
        """
        data = self.execute(_REPOSITORY_QUERY, {"owner": owner, "name": repo})
        repository = data.get("repository")
        if repository is None:
            raise RepositoryNotFoundError(owner, repo)

        rate_limit = _rate_limit_from_payload(data.get("rateLimit"))
        return self._parse_repository(repository, rate_limit)

    @staticmethod
    def _parse_repository(payload: dict, rate_limit: RateLimitInfo | None) -> RepositoryData:
        try:
            owner_payload = payload["owner"]
            owner = OwnerSummary(
                github_id=owner_payload["databaseId"],
                login=owner_payload["login"],
                account_type=owner_payload["__typename"],  # "User" or "Organization"
                avatar_url=owner_payload.get("avatarUrl"),
            )
            language = payload.get("primaryLanguage")
            license_info = payload.get("licenseInfo")
            return RepositoryData(
                github_id=payload["databaseId"],
                name=payload["name"],
                full_name=payload["nameWithOwner"],
                description=payload.get("description"),
                primary_language=language["name"] if language else None,
                license_spdx_id=license_info["spdxId"] if license_info else None,
                is_archived=payload["isArchived"],
                is_fork=payload["isFork"],
                github_created_at=payload["createdAt"],
                pushed_at=payload.get("pushedAt"),
                owner=owner,
                rate_limit=rate_limit,
            )
        except KeyError as exc:
            raise GitHubAPIError(f"GraphQL response missing expected field: {exc}") from exc
