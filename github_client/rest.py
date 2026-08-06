"""GitHub REST API client — Checkpoint 1.1.c.

Implements exactly one endpoint (GET /repos/{owner}/{repo}), per
IMPLEMENTATION_ROADMAP.md Checkpoint 1.1's Definition of Done. Returns
validated Python objects only — no database writes, no retries, no
throttling, no GraphQL. Those are 1.1.d (rate limiter), 1.1.e (retry),
1.1.f (GraphQL), and Checkpoint 1.3 (storage) respectively.

2026-08-06 additive extension (Checkpoint 1.3 impact analysis): RepositoryData
gained topics/license_name/stars_count/forks_count/watchers_count/
open_issues_count/size_kb, all parsed from the same single endpoint above -
no new endpoint added. See CHECKPOINT_1_1_FINAL_REPORT.md's addendum for the
full rationale (RepositoryData stays a flat API DTO; repository_snapshot
remains a database-only concern, not a Python object).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from config.settings import Settings, get_settings
from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from github_client.rate_limiter import RateLimitInfo
from logging_setup import get_logger

log = get_logger(__name__)

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class OwnerSummary:
    """Owner fields available on the embedded `owner` object of a repo response.

    Note: GitHub's repository payload embeds an abbreviated owner object
    that does NOT include the owner's account creation date. Fetching
    that would require a separate GET /users/{login} call, which is out
    of scope for Checkpoint 1.1 (single-endpoint requirement) — the
    `github_created_at` column on the `owners` table (Migration 002) is
    therefore not populated by this client. Documented, not a defect.
    """

    github_id: int
    login: str
    account_type: str  # "User" or "Organization", as returned by GitHub (not lowercased here)
    avatar_url: str | None


@dataclass(frozen=True)
class RepositoryData:
    """Validated repository metadata, field-named to align with the
    `repositories` table (MIGRATION_002_REPORT.md) so Checkpoint 1.3 can
    map this object to a row with minimal translation. This client does
    not write to the database — mapping/storage remains 1.3's job.

    `topics`/`license_name`/`stars_count`/`forks_count`/`watchers_count`/
    `open_issues_count`/`size_kb` are an additive extension (Checkpoint
    1.3 impact analysis, 2026-08-06) for the `topics`/`repository_topics`/
    `licenses`/`repository_snapshot` tables. All carry defaults so this
    remains backward compatible with any existing construction. Deliberately
    flat, not a nested RepositorySnapshot object - RepositoryData is an API
    transport DTO, not the domain model; repository_snapshot is a distinct
    database table (a persistence concern), not a distinct Python shape, per
    the explicit architecture decision preceding this change. Populated from
    the same single GET /repos/{owner}/{repo} response already fetched - no
    new endpoint.
    """

    github_id: int
    name: str
    full_name: str
    description: str | None
    primary_language: str | None
    license_spdx_id: str | None
    is_archived: bool
    is_fork: bool
    github_created_at: str  # ISO-8601 string, as returned by GitHub
    pushed_at: str | None
    owner: OwnerSummary
    rate_limit: RateLimitInfo | None
    topics: tuple[str, ...] = ()
    license_name: str | None = None
    stars_count: int = 0
    forks_count: int = 0
    watchers_count: int = 0
    open_issues_count: int = 0
    size_kb: int = 0


class GitHubRESTClient:
    """Thin, synchronous wrapper around the GitHub REST API.

    Deliberately narrow: one method, one endpoint. Rate limiting and
    retries are explicitly NOT implemented here (Checkpoints 1.1.d/1.1.e).
    """

    def __init__(self, settings: Settings | None = None, session: requests.Session | None = None) -> None:
        self._settings = settings or get_settings()
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def get_repository(self, owner: str, repo: str) -> RepositoryData:
        """Fetch repository metadata via GET /repos/{owner}/{repo}.

        Raises RepositoryNotFoundError (404), AuthenticationError (401),
        RateLimitExceededError (403 + exhausted quota), or GitHubAPIError
        (any other non-200 status, malformed response, or network failure).
        """
        url = f"{_API_BASE}/repos/{owner}/{repo}"
        started = time.monotonic()

        try:
            response = self._session.get(url, headers=self._headers(), timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            log.error("github_api_request_failed method=GET url=%s error=%s", url, exc.__class__.__name__)
            raise GitHubAPIError(f"Network failure requesting {url}: {exc}") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        rate_limit = RateLimitInfo.from_headers(response.headers)
        log.info(
            "github_api_response method=GET url=%s status=%s duration_ms=%s rate_remaining=%s",
            url,
            response.status_code,
            duration_ms,
            rate_limit.remaining if rate_limit else "unknown",
        )

        if response.status_code == 404:
            raise RepositoryNotFoundError(owner, repo)

        if response.status_code == 401:
            raise AuthenticationError()

        if response.status_code == 403:
            if rate_limit is not None and rate_limit.remaining == 0:
                raise RateLimitExceededError(rate_limit.reset_at)
            raise GitHubAPIError(
                f"GitHub returned 403 for {url} (not a rate-limit exhaustion)", status_code=403
            )

        if response.status_code != 200:
            raise GitHubAPIError(
                f"Unexpected status {response.status_code} for {url}", status_code=response.status_code
            )

        return self._parse_repository(response.json(), rate_limit)

    @staticmethod
    def _parse_repository(payload: dict, rate_limit: RateLimitInfo | None) -> RepositoryData:
        try:
            owner_payload = payload["owner"]
            owner = OwnerSummary(
                github_id=owner_payload["id"],
                login=owner_payload["login"],
                account_type=owner_payload["type"],
                avatar_url=owner_payload.get("avatar_url"),
            )
            license_payload = payload.get("license")
            return RepositoryData(
                github_id=payload["id"],
                name=payload["name"],
                full_name=payload["full_name"],
                description=payload.get("description"),
                primary_language=payload.get("language"),
                license_spdx_id=license_payload.get("spdx_id") if license_payload else None,
                is_archived=payload["archived"],
                is_fork=payload["fork"],
                github_created_at=payload["created_at"],
                pushed_at=payload.get("pushed_at"),
                owner=owner,
                rate_limit=rate_limit,
                topics=tuple(payload.get("topics") or ()),
                license_name=license_payload.get("name") if license_payload else None,
                stars_count=payload["stargazers_count"],
                forks_count=payload["forks_count"],
                watchers_count=payload["watchers_count"],
                open_issues_count=payload["open_issues_count"],
                size_kb=payload["size"],
            )
        except KeyError as exc:
            raise GitHubAPIError(f"GitHub response missing expected field: {exc}") from exc
