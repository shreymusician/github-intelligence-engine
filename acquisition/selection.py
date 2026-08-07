"""Repository candidate selection — Checkpoint 1.3.a.

Selects candidate repositories via GitHub's Search API
(GET /search/repositories, github_client.rest.GitHubRESTClient.search_repositories)
and returns only their `owner/repo` full-name identifiers - not repository
detail. Fetching full detail for a selected candidate remains Checkpoint
1.1's job (GitHubRESTClient.get_repository / GitHubGraphQLClient.get_repository);
storing anything remains Checkpoint 1.3.b's job; tying selection, fetch, and
storage into one run remains Checkpoint 1.3.c's job. This module does none
of those things.

Per ADR_004_DEFER_BIGQUERY_FOR_V1.md: GitHub's Search API returns up to
1,000 results per query, comfortably covering V1's 100-1,000 repository
target without GitHub Archive/BigQuery.

Reuses config (via GitHubRESTClient's own Settings handling), logging,
and github_client's existing retry/rate-limiter compatibility (the same
RateLimitInfo/RateLimiter/RetryPolicy from Checkpoint 1.1 work unmodified
against search_repositories(), since it reuses the same RateLimitInfo
parsing) - no new retry or rate-limit logic is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass

from github_client.rest import GitHubRESTClient
from logging_setup import get_logger

log = get_logger(__name__)

# GitHub's own documented ceiling for a single Search API query (100 per
# page x 10 pages) - not a value this module invented.
_MAX_RESULTS_PER_QUERY = 1000
_MAX_PER_PAGE = 100
_MAX_PAGES_PER_QUERY = _MAX_RESULTS_PER_QUERY // _MAX_PER_PAGE


@dataclass(frozen=True)
class SelectionCriteria:
    """Repository selection criteria for GitHub's Search API.

    `languages=()` (the default) means no language qualifier - any language.
    Multiple languages run as separate searches (one per language) and are
    merged, since GitHub's `language:` qualifier has no OR syntax for
    multiple values within one query.
    """

    languages: tuple[str, ...] = ()
    min_stars: int = 0
    max_results: int = 100
    exclude_forks: bool = True
    exclude_archived: bool = True

    def __post_init__(self) -> None:
        if self.min_stars < 0:
            raise ValueError(f"min_stars must be >= 0, got {self.min_stars}")
        if not (1 <= self.max_results <= _MAX_RESULTS_PER_QUERY):
            raise ValueError(
                f"max_results must be between 1 and {_MAX_RESULTS_PER_QUERY} "
                f"(GitHub Search API's per-query ceiling), got {self.max_results}"
            )


class RepositorySelector:
    """Selects candidate repository full names via GitHub's Search API.

    Holds no state of its own beyond the client it was given - each call
    to select_candidates() is independent. Reuses GitHubRESTClient
    (Checkpoint 1.1) for all HTTP, authentication, and response parsing;
    this class only builds query strings, paginates, and trims to
    max_results.
    """

    def __init__(self, client: GitHubRESTClient) -> None:
        self._client = client

    def select_candidates(self, criteria: SelectionCriteria) -> list[str]:
        """Return up to `criteria.max_results` distinct `owner/repo` full
        names matching the given criteria, ranked by stars (descending).
        """
        languages: tuple[str | None, ...] = criteria.languages or (None,)
        candidates: list[str] = []
        seen: set[str] = set()

        for language in languages:
            if len(candidates) >= criteria.max_results:
                break
            query = self._build_query(criteria, language)
            self._collect_for_query(query, criteria.max_results, candidates, seen)

        log.info(
            "selection_completed candidates=%s max_results=%s languages=%s min_stars=%s",
            len(candidates),
            criteria.max_results,
            criteria.languages or "any",
            criteria.min_stars,
        )
        return candidates

    def _collect_for_query(
        self, query: str, max_results: int, candidates: list[str], seen: set[str]
    ) -> None:
        for page in range(1, _MAX_PAGES_PER_QUERY + 1):
            if len(candidates) >= max_results:
                return

            result = self._client.search_repositories(
                query=query, sort="stars", order="desc", per_page=_MAX_PER_PAGE, page=page
            )
            log.info(
                "selection_page_fetched query=%s page=%s total_count=%s items=%s",
                query,
                page,
                result.total_count,
                len(result.items),
            )

            if not result.items:
                return

            for repo in result.items:
                if len(candidates) >= max_results:
                    return
                if repo.full_name in seen:
                    continue
                seen.add(repo.full_name)
                candidates.append(repo.full_name)

            if page * _MAX_PER_PAGE >= result.total_count:
                return

    @staticmethod
    def _build_query(criteria: SelectionCriteria, language: str | None) -> str:
        """Build a GitHub Search API query string from selection criteria.

        Qualifier semantics confirmed against GitHub's own documentation
        (docs.github.com/en/search-github/searching-on-github/searching-for-repositories),
        not assumed: forks are excluded by default (no qualifier needed when
        exclude_forks=True; `fork:true` is required to include them),
        `archived:false`/`archived:true` filters explicitly, `stars:>=N`
        supports the comparison operator used here, `language:X` takes
        exactly one value per occurrence.
        """
        parts = [f"stars:>={criteria.min_stars}"]
        if criteria.exclude_archived:
            parts.append("archived:false")
        if not criteria.exclude_forks:
            parts.append("fork:true")
        if language:
            parts.append(f"language:{language}")
        return " ".join(parts)
