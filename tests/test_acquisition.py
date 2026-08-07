"""Test suite for acquisition.selection — Checkpoint 1.3.a.

Deterministic, mocked tests exercising RepositorySelector against a fake
GitHubRESTClient double (no real network, no live GitHub dependency for
normal test execution) — mirrors the style established in
tests/test_github_client.py (Checkpoint 1.1.g).
"""

from __future__ import annotations

import logging

import pytest

from acquisition.selection import RepositorySelector, SelectionCriteria
from github_client.rate_limiter import RateLimitInfo
from github_client.rest import OwnerSummary, RepositoryData, RepositorySearchPage


def make_repo(full_name: str, stars: int = 100) -> RepositoryData:
    owner_login, name = full_name.split("/")
    return RepositoryData(
        github_id=hash(full_name) & 0xFFFFFFFF,
        name=name,
        full_name=full_name,
        description=None,
        primary_language="Python",
        license_spdx_id="MIT",
        is_archived=False,
        is_fork=False,
        github_created_at="2020-01-01T00:00:00Z",
        pushed_at="2020-01-01T00:00:00Z",
        owner=OwnerSummary(github_id=1, login=owner_login, account_type="User", avatar_url=None),
        rate_limit=RateLimitInfo(limit=30, remaining=29, reset_at=0, used=1, resource="search"),
        stars_count=stars,
    )


class FakeSearchClient:
    """Fake GitHubRESTClient exposing only search_repositories(), driven by
    a query -> list-of-pages mapping. Records every call for assertions."""

    def __init__(self, pages_by_query: dict[str, list[RepositorySearchPage]]):
        self._pages_by_query = pages_by_query
        self.calls: list[dict] = []

    def search_repositories(self, query, sort="stars", order="desc", per_page=100, page=1):
        self.calls.append({"query": query, "sort": sort, "order": order, "per_page": per_page, "page": page})
        pages = self._pages_by_query[query]
        return pages[page - 1]


def single_page_client(query: str, repos: list[RepositoryData], total_count: int | None = None) -> FakeSearchClient:
    page = RepositorySearchPage(
        total_count=total_count if total_count is not None else len(repos),
        incomplete_results=False,
        items=tuple(repos),
    )
    return FakeSearchClient({query: [page]})


class TestSelectionCriteria:
    def test_defaults(self):
        criteria = SelectionCriteria()
        assert criteria.languages == ()
        assert criteria.min_stars == 0
        assert criteria.max_results == 100
        assert criteria.exclude_forks is True
        assert criteria.exclude_archived is True

    def test_negative_min_stars_rejected(self):
        with pytest.raises(ValueError):
            SelectionCriteria(min_stars=-1)

    @pytest.mark.parametrize("max_results", [0, -5, 1001])
    def test_out_of_range_max_results_rejected(self, max_results):
        with pytest.raises(ValueError):
            SelectionCriteria(max_results=max_results)

    def test_boundary_max_results_accepted(self):
        SelectionCriteria(max_results=1)
        SelectionCriteria(max_results=1000)


class TestQueryGeneration:
    def test_default_criteria_query(self):
        query = RepositorySelector._build_query(SelectionCriteria(), None)
        assert query == "stars:>=0 archived:false"

    def test_min_stars_in_query(self):
        query = RepositorySelector._build_query(SelectionCriteria(min_stars=500), None)
        assert "stars:>=500" in query

    def test_language_qualifier_added(self):
        query = RepositorySelector._build_query(SelectionCriteria(), "python")
        assert "language:python" in query

    def test_no_language_qualifier_when_none(self):
        query = RepositorySelector._build_query(SelectionCriteria(), None)
        assert "language:" not in query

    def test_archived_false_qualifier_by_default(self):
        query = RepositorySelector._build_query(SelectionCriteria(exclude_archived=True), None)
        assert "archived:false" in query

    def test_no_archived_qualifier_when_not_excluding(self):
        query = RepositorySelector._build_query(SelectionCriteria(exclude_archived=False), None)
        assert "archived:" not in query

    def test_no_fork_qualifier_when_excluding_forks(self):
        # GitHub excludes forks by default - no qualifier needed to exclude them.
        query = RepositorySelector._build_query(SelectionCriteria(exclude_forks=True), None)
        assert "fork:" not in query

    def test_fork_true_qualifier_when_including_forks(self):
        query = RepositorySelector._build_query(SelectionCriteria(exclude_forks=False), None)
        assert "fork:true" in query


class TestRepositorySelectorSelectCandidates:
    def test_returns_full_names_only(self):
        repos = [make_repo("octocat/Hello-World"), make_repo("octocat/Spoon-Knife")]
        query = "stars:>=0 archived:false"
        client = single_page_client(query, repos)
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria())

        assert candidates == ["octocat/Hello-World", "octocat/Spoon-Knife"]
        assert all(isinstance(c, str) for c in candidates)

    def test_min_stars_reflected_in_query_not_client_side_filtering(self):
        # RepositorySelector doesn't re-filter by stars itself - it trusts
        # the query it built. This test locks in that the criteria reach
        # the client via the query string, matching _build_query's own tests.
        repos = [make_repo("octocat/Hello-World", stars=5000)]
        query = "stars:>=1000 archived:false"
        client = single_page_client(query, repos)
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(min_stars=1000))

        assert candidates == ["octocat/Hello-World"]
        assert client.calls[0]["query"] == "stars:>=1000 archived:false"

    def test_max_results_enforced_within_a_page(self):
        repos = [make_repo(f"owner/repo{i}") for i in range(10)]
        query = "stars:>=0 archived:false"
        client = single_page_client(query, repos, total_count=10)
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(max_results=3))

        assert len(candidates) == 3
        assert candidates == ["owner/repo0", "owner/repo1", "owner/repo2"]

    def test_pagination_across_multiple_pages(self):
        query = "stars:>=0 archived:false"
        page1 = RepositorySearchPage(
            total_count=150, incomplete_results=False, items=tuple(make_repo(f"owner/repo{i}") for i in range(100))
        )
        page2 = RepositorySearchPage(
            total_count=150,
            incomplete_results=False,
            items=tuple(make_repo(f"owner/repo{i}") for i in range(100, 150)),
        )
        client = FakeSearchClient({query: [page1, page2]})
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(max_results=120))

        assert len(candidates) == 120
        assert candidates[0] == "owner/repo0"
        assert candidates[-1] == "owner/repo119"
        assert [c["page"] for c in client.calls] == [1, 2]

    def test_pagination_stops_when_total_count_exhausted(self):
        query = "stars:>=0 archived:false"
        page1 = RepositorySearchPage(
            total_count=5, incomplete_results=False, items=tuple(make_repo(f"owner/repo{i}") for i in range(5))
        )
        client = FakeSearchClient({query: [page1]})
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(max_results=100))

        assert len(candidates) == 5
        assert len(client.calls) == 1  # never requests page 2 - total_count already exhausted

    def test_duplicate_candidates_not_returned(self):
        # Defensive guard against ranking drift between paginated requests
        # (a documented GitHub Search API caveat: results can shift slightly
        # between calls to the same query, occasionally repeating an item at
        # a page boundary) - not a substitute for GitHub's own within-page
        # deduplication. Uses a realistic 2-full-page scenario (every
        # non-final page returns exactly per_page items in real pagination),
        # with one item ("owner/repo99") deliberately repeated at the
        # page 1/page 2 boundary to simulate that drift.
        query = "stars:>=0 archived:false"
        page1_items = tuple(make_repo(f"owner/repo{i}") for i in range(100))
        page2_items = (make_repo("owner/repo99"),) + tuple(make_repo(f"owner/repo{i}") for i in range(100, 149))
        page1 = RepositorySearchPage(total_count=150, incomplete_results=False, items=page1_items)
        page2 = RepositorySearchPage(total_count=150, incomplete_results=False, items=page2_items)
        client = FakeSearchClient({query: [page1, page2]})
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(max_results=200))

        assert len(candidates) == 149  # 150 declared, minus the 1 repeated item
        assert len(candidates) == len(set(candidates))
        assert candidates.count("owner/repo99") == 1

    def test_multiple_languages_run_separate_queries_and_merge(self):
        py_query = "stars:>=0 archived:false language:python"
        go_query = "stars:>=0 archived:false language:go"
        client = FakeSearchClient(
            {
                py_query: [
                    RepositorySearchPage(total_count=1, incomplete_results=False, items=(make_repo("a/py-repo"),))
                ],
                go_query: [
                    RepositorySearchPage(total_count=1, incomplete_results=False, items=(make_repo("b/go-repo"),))
                ],
            }
        )
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(languages=("python", "go"), max_results=100))

        assert candidates == ["a/py-repo", "b/go-repo"]

    def test_max_results_stops_before_second_language_queried(self):
        py_query = "stars:>=0 archived:false language:python"
        client = FakeSearchClient(
            {
                py_query: [
                    RepositorySearchPage(
                        total_count=5,
                        incomplete_results=False,
                        items=tuple(make_repo(f"a/py{i}") for i in range(5)),
                    )
                ]
            }
        )
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(languages=("python", "go"), max_results=5))

        assert len(candidates) == 5
        assert all(q["query"] == py_query for q in client.calls)  # "go" query never issued

    def test_empty_search_results_returns_empty_list(self):
        query = "stars:>=1000000 archived:false"
        client = single_page_client(query, [], total_count=0)
        selector = RepositorySelector(client)

        candidates = selector.select_candidates(SelectionCriteria(min_stars=1000000))

        assert candidates == []

    def test_logging_produced(self, caplog):
        caplog.set_level(logging.INFO)
        repos = [make_repo("octocat/Hello-World")]
        query = "stars:>=0 archived:false"
        client = single_page_client(query, repos)
        selector = RepositorySelector(client)

        selector.select_candidates(SelectionCriteria())

        assert "selection_page_fetched" in caplog.text
        assert "selection_completed" in caplog.text
        assert "candidates=1" in caplog.text


class TestSelectorReusesExistingInfrastructure:
    """Confirms RepositorySelector composes with the real, unmodified
    RateLimiter/RetryPolicy from Checkpoint 1.1 - no new retry/rate-limit
    logic exists in acquisition/selection.py itself."""

    def test_selector_only_calls_search_repositories_method(self):
        # RepositorySelector's only interaction with its client is
        # search_repositories() - confirms no other GitHubRESTClient method
        # (get_repository, etc.) or GraphQL client is touched.
        query = "stars:>=0 archived:false"
        client = single_page_client(query, [make_repo("octocat/Hello-World")])
        selector = RepositorySelector(client)

        selector.select_candidates(SelectionCriteria())

        assert all(set(call.keys()) == {"query", "sort", "order", "per_page", "page"} for call in client.calls)

    def test_real_retry_policy_composes_around_select_candidates(self):
        from github_client.exceptions import GitHubAPIError
        from github_client.retry import RetryPolicy

        query = "stars:>=0 archived:false"
        attempts = {"n": 0}

        class FlakyThenSucceedsClient:
            def search_repositories(self, query, sort="stars", order="desc", per_page=100, page=1):
                attempts["n"] += 1
                if attempts["n"] < 2:
                    raise GitHubAPIError("transient search failure")
                return RepositorySearchPage(
                    total_count=1, incomplete_results=False, items=(make_repo("octocat/Hello-World"),)
                )

        selector = RepositorySelector(FlakyThenSucceedsClient())
        policy = RetryPolicy(max_attempts=3, initial_delay=0, sleep_fn=lambda s: None)

        candidates = policy.call(lambda: selector.select_candidates(SelectionCriteria()))

        assert candidates == ["octocat/Hello-World"]
        assert attempts["n"] == 2
