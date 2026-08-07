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

2026-08-07 additive extension (Checkpoint 1.3.a): added search_repositories(),
a second endpoint (GET /search/repositories), for acquisition/selection.py's
candidate-selection use. get_repository() is unchanged. Search API items share
the same response shape as get_repository()'s single-repo response, so
_parse_repository() is reused, not duplicated. Search responses carry their
own, stricter rate-limit bucket (X-RateLimit-Resource: search, ~30/min vs the
core 5,000/hour) - RateLimitInfo.from_headers()/RateLimiter already handle
this generically via the existing `resource` field, no new logic needed.
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


@dataclass(frozen=True)
class RepositorySearchPage:
    """One page of GET /search/repositories results, mirroring that
    endpoint's response shape (total_count/incomplete_results/items) the
    same way RepositoryData mirrors GET /repos/{owner}/{repo}'s. Pagination
    across pages is the caller's responsibility (acquisition/selection.py,
    Checkpoint 1.3.a) - this is one page, faithfully parsed, nothing more.
    """

    total_count: int
    incomplete_results: bool
    items: tuple[RepositoryData, ...]


class GitHubRESTClient:
    """Thin, synchronous wrapper around the GitHub REST API.

    Two endpoints: GET /repos/{owner}/{repo} (1.1.c) and GET
    /search/repositories (1.3.a, additive). Rate limiting and retries are
    explicitly NOT implemented here (Checkpoints 1.1.d/1.1.e) - callers
    compose github_client.retry/rate_limiter around either method.
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

    def search_repositories(
        self,
        query: str,
        sort: str = "stars",
        order: str = "desc",
        per_page: int = 100,
        page: int = 1,
    ) -> RepositorySearchPage:
        """Fetch one page of GET /search/repositories results.

        Raises AuthenticationError (401), RateLimitExceededError (403 with
        the search-specific rate-limit bucket exhausted), GitHubAPIError (422
        invalid query syntax, any other non-200 status, malformed response,
        or network failure). Does not paginate or filter - one request, one
        page, faithfully parsed; a caller (acquisition/selection.py) composes
        multiple calls for pagination and result-count enforcement.
        """
        url = f"{_API_BASE}/search/repositories"
        params = {"q": query, "sort": sort, "order": order, "per_page": per_page, "page": page}
        started = time.monotonic()

        try:
            response = self._session.get(url, headers=self._headers(), params=params, timeout=_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            log.error("github_search_request_failed url=%s error=%s", url, exc.__class__.__name__)
            raise GitHubAPIError(f"Network failure requesting {url}: {exc}") from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        rate_limit = RateLimitInfo.from_headers(response.headers)
        log.info(
            "github_search_response url=%s status=%s duration_ms=%s rate_remaining=%s query=%s page=%s",
            url,
            response.status_code,
            duration_ms,
            rate_limit.remaining if rate_limit else "unknown",
            query,
            page,
        )

        if response.status_code == 401:
            raise AuthenticationError()

        if response.status_code == 403:
            if rate_limit is not None and rate_limit.remaining == 0:
                raise RateLimitExceededError(rate_limit.reset_at)
            raise GitHubAPIError(
                f"GitHub returned 403 for search query {query!r} (not a rate-limit exhaustion)",
                status_code=403,
            )

        if response.status_code == 422:
            raise GitHubAPIError(f"Invalid search query: {query!r}", status_code=422)

        if response.status_code != 200:
            raise GitHubAPIError(
                f"Unexpected status {response.status_code} for search query {query!r}",
                status_code=response.status_code,
            )

        body = response.json()
        try:
            items = tuple(self._parse_repository(item, rate_limit) for item in body["items"])
            return RepositorySearchPage(
                total_count=body["total_count"],
                incomplete_results=body["incomplete_results"],
                items=items,
            )
        except KeyError as exc:
            raise GitHubAPIError(f"GitHub search response missing expected field: {exc}") from exc

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
