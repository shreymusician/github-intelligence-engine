"""Test suite for acquisition.pipeline — Checkpoint 1.3.c.

Deterministic, mocked tests exercising AcquisitionPipeline against fake
RepositorySelector/GitHubRESTClient/RepositoryWriter doubles - no real
network, no live GitHub or PostgreSQL dependency for normal test
execution. Mirrors the style established in tests/test_acquisition.py
(Checkpoint 1.3.a) and tests/test_github_client.py (Checkpoint 1.1.g).
"""

from __future__ import annotations

import logging
import uuid

from acquisition.exceptions import RepositoryPersistenceError
from acquisition.pipeline import AcquisitionPipeline, AcquisitionStats
from acquisition.selection import SelectionCriteria
from github_client.exceptions import GitHubAPIError, RepositoryNotFoundError
from github_client.rate_limiter import RateLimitInfo, RateLimiter
from github_client.retry import RetryPolicy
from github_client.rest import OwnerSummary, RepositoryData


def make_repo(full_name: str, rate_limit: RateLimitInfo | None = None) -> RepositoryData:
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
        rate_limit=rate_limit
        if rate_limit is not None
        else RateLimitInfo(limit=5000, remaining=4999, reset_at=0, used=1, resource="core"),
    )


class FakeSelector:
    """Fake RepositorySelector - returns a fixed candidate list, ignoring
    the criteria object's fields other than recording it was called with."""

    def __init__(self, candidates: list[str]):
        self._candidates = candidates
        self.calls: list[SelectionCriteria] = []

    def select_candidates(self, criteria: SelectionCriteria) -> list[str]:
        self.calls.append(criteria)
        return self._candidates


class FakeClient:
    """Fake GitHubRESTClient exposing only get_repository(), driven by a
    full_name -> RepositoryData-or-exception mapping. Records every call."""

    def __init__(self, repos_by_full_name: dict[str, object]):
        self._repos = repos_by_full_name
        self.calls: list[str] = []

    def get_repository(self, owner: str, repo: str):
        full_name = f"{owner}/{repo}"
        self.calls.append(full_name)
        result = self._repos[full_name]
        if isinstance(result, Exception):
            raise result
        return result


class FakeWriter:
    """Fake RepositoryWriter - returns a fixed UUID per full_name, or
    raises a given exception. Records every call, in order."""

    def __init__(self, results_by_full_name: dict[str, object] | None = None):
        self._results = results_by_full_name or {}
        self.calls: list[str] = []

    def upsert_repository(self, data: RepositoryData) -> uuid.UUID:
        self.calls.append(data.full_name)
        result = self._results.get(data.full_name, uuid.uuid4())
        if isinstance(result, Exception):
            raise result
        return result


def no_wait_rate_limiter() -> RateLimiter:
    return RateLimiter(sleep_fn=lambda s: (_ for _ in ()).throw(AssertionError("should not sleep")))


def instant_retry_policy(max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(max_attempts=max_attempts, initial_delay=0, sleep_fn=lambda s: None)


class TestEmptyCandidateList:
    def test_empty_candidates_produces_empty_result(self):
        selector = FakeSelector([])
        client = FakeClient({})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria())

        assert result.stats == AcquisitionStats(
            candidates_selected=0, attempted=0, succeeded=0, failed=0, skipped_duplicates=0
        )
        assert result.repository_ids == ()
        assert result.failures == ()
        assert client.calls == []
        assert writer.calls == []


class TestOneCandidate:
    def test_single_candidate_success(self):
        selector = FakeSelector(["octocat/Hello-World"])
        client = FakeClient({"octocat/Hello-World": make_repo("octocat/Hello-World")})
        fixed_id = uuid.uuid4()
        writer = FakeWriter({"octocat/Hello-World": fixed_id})
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria())

        assert result.stats.candidates_selected == 1
        assert result.stats.attempted == 1
        assert result.stats.succeeded == 1
        assert result.stats.failed == 0
        assert result.repository_ids == (fixed_id,)
        assert client.calls == ["octocat/Hello-World"]
        assert writer.calls == ["octocat/Hello-World"]


class TestMultipleCandidates:
    def test_all_succeed_in_order(self):
        names = [f"owner/repo{i}" for i in range(5)]
        selector = FakeSelector(names)
        client = FakeClient({n: make_repo(n) for n in names})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=5))

        assert result.stats.succeeded == 5
        assert result.stats.failed == 0
        assert len(result.repository_ids) == 5
        assert client.calls == names
        assert writer.calls == names

    def test_respects_max_results_even_if_selector_returns_more(self):
        # Defensive re-bound (Sec "Respect max_results" in the pipeline's
        # own stated responsibilities) - independent of trusting the
        # selector to have already enforced it.
        names = [f"owner/repo{i}" for i in range(10)]
        selector = FakeSelector(names)
        client = FakeClient({n: make_repo(n) for n in names})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=3))

        assert result.stats.candidates_selected == 3
        assert result.stats.attempted == 3
        assert client.calls == names[:3]


class TestStorageFailure:
    def test_storage_failure_recorded_and_pipeline_continues(self):
        names = ["owner/good1", "owner/bad", "owner/good2"]
        selector = FakeSelector(names)
        client = FakeClient({n: make_repo(n) for n in names})
        writer = FakeWriter({"owner/bad": RepositoryPersistenceError("owner/bad", RuntimeError("db exploded"))})
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=3))

        assert result.stats.succeeded == 2
        assert result.stats.failed == 1
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.full_name == "owner/bad"
        assert failure.stage == "storage"
        assert failure.error_type == "RepositoryPersistenceError"
        # Pipeline continued to the third candidate despite the middle failure.
        assert client.calls == names
        assert writer.calls == names


class TestGitHubFetchFailure:
    def test_fetch_failure_recorded_and_pipeline_continues(self):
        names = ["owner/good1", "owner/missing", "owner/good2"]
        selector = FakeSelector(names)
        client = FakeClient(
            {
                "owner/good1": make_repo("owner/good1"),
                "owner/missing": RepositoryNotFoundError("owner", "missing"),
                "owner/good2": make_repo("owner/good2"),
            }
        )
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=3))

        assert result.stats.succeeded == 2
        assert result.stats.failed == 1
        assert result.failures[0].full_name == "owner/missing"
        assert result.failures[0].stage == "fetch"
        assert result.failures[0].error_type == "RepositoryNotFoundError"
        # The failed fetch never reached storage.
        assert writer.calls == ["owner/good1", "owner/good2"]

    def test_malformed_candidate_full_name_recorded_as_fetch_failure(self):
        selector = FakeSelector(["no-slash-here"])
        client = FakeClient({})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria())

        assert result.stats.failed == 1
        assert result.failures[0].stage == "fetch"
        assert result.failures[0].error_type == "ValueError"
        assert client.calls == []  # never attempted a malformed name


class TestRetryPath:
    def test_transient_github_failure_recovers_via_real_retry_policy(self):
        selector = FakeSelector(["owner/flaky"])
        attempts = {"n": 0}

        class FlakyThenSucceedsClient:
            def get_repository(self, owner, repo):
                attempts["n"] += 1
                if attempts["n"] < 2:
                    raise GitHubAPIError("transient failure")
                return make_repo(f"{owner}/{repo}")

        writer = FakeWriter()
        pipeline = AcquisitionPipeline(
            FlakyThenSucceedsClient(),
            selector,
            writer,
            rate_limiter=no_wait_rate_limiter(),
            retry_policy=instant_retry_policy(max_attempts=3),
        )

        result = pipeline.run(SelectionCriteria())

        assert result.stats.succeeded == 1
        assert attempts["n"] == 2  # first call failed, second succeeded - real RetryPolicy recovered it

    def test_retry_exhaustion_surfaces_as_fetch_failure(self):
        selector = FakeSelector(["owner/always-fails"])
        client = FakeClient({"owner/always-fails": GitHubAPIError("persistent failure")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(
            client,
            selector,
            writer,
            rate_limiter=no_wait_rate_limiter(),
            retry_policy=instant_retry_policy(max_attempts=2),
        )

        result = pipeline.run(SelectionCriteria())

        assert result.stats.failed == 1
        assert result.failures[0].error_type == "GitHubAPIError"
        assert len(client.calls) == 2  # both attempts were made before giving up


class TestRateLimiterInteraction:
    def test_rate_limiter_consulted_after_each_fetch_before_storage(self):
        names = ["owner/repo1", "owner/repo2"]
        selector = FakeSelector(names)
        client = FakeClient({n: make_repo(n) for n in names})

        consulted: list[RateLimitInfo] = []

        class RecordingRateLimiter(RateLimiter):
            def wait_if_needed(self, rate_limit):
                consulted.append(rate_limit)
                return super().wait_if_needed(rate_limit)

        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=RecordingRateLimiter())

        pipeline.run(SelectionCriteria(max_results=2))

        assert len(consulted) == 2  # once per successful fetch, not once per candidate overall

    def test_rate_limiter_waits_when_quota_exhausted(self):
        exhausted = RateLimitInfo(limit=5000, remaining=0, reset_at=100, used=5000, resource="core")
        selector = FakeSelector(["owner/repo1"])
        client = FakeClient({"owner/repo1": make_repo("owner/repo1", rate_limit=exhausted)})
        writer = FakeWriter()
        waited = {"called": False}
        limiter = RateLimiter(sleep_fn=lambda s: waited.__setitem__("called", True), time_fn=lambda: 0.0)
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=limiter)

        pipeline.run(SelectionCriteria())

        assert waited["called"] is True

    def test_rate_limiter_not_consulted_after_a_fetch_failure(self):
        selector = FakeSelector(["owner/missing"])
        client = FakeClient({"owner/missing": RepositoryNotFoundError("owner", "missing")})
        writer = FakeWriter()

        consulted = {"n": 0}

        class CountingRateLimiter(RateLimiter):
            def wait_if_needed(self, rate_limit):
                consulted["n"] += 1
                return False

        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=CountingRateLimiter())

        pipeline.run(SelectionCriteria())

        assert consulted["n"] == 0  # nothing to check - the fetch never produced a RepositoryData


class TestDuplicateCandidateHandling:
    def test_duplicate_full_names_skipped_after_first_occurrence(self):
        selector = FakeSelector(["owner/repo1", "owner/repo2", "owner/repo1"])
        client = FakeClient({"owner/repo1": make_repo("owner/repo1"), "owner/repo2": make_repo("owner/repo2")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=3))

        assert result.stats.skipped_duplicates == 1
        assert result.stats.attempted == 2
        assert result.stats.succeeded == 2
        assert client.calls == ["owner/repo1", "owner/repo2"]  # second "owner/repo1" never fetched


class TestProgressLogging:
    def test_start_and_completion_logged(self, caplog):
        caplog.set_level(logging.INFO)
        selector = FakeSelector(["owner/repo1"])
        client = FakeClient({"owner/repo1": make_repo("owner/repo1")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        pipeline.run(SelectionCriteria())

        assert "pipeline_started" in caplog.text
        assert "candidates=1" in caplog.text
        assert "pipeline_repository_succeeded" in caplog.text
        assert "pipeline_completed" in caplog.text
        assert "succeeded=1" in caplog.text

    def test_failure_logged_with_stage_and_index(self, caplog):
        caplog.set_level(logging.INFO)
        selector = FakeSelector(["owner/missing"])
        client = FakeClient({"owner/missing": RepositoryNotFoundError("owner", "missing")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        pipeline.run(SelectionCriteria())

        assert "pipeline_repository_failed" in caplog.text
        assert "stage=fetch" in caplog.text
        assert "index=1/1" in caplog.text

    def test_duplicate_skip_logged(self, caplog):
        caplog.set_level(logging.INFO)
        selector = FakeSelector(["owner/repo1", "owner/repo1"])
        client = FakeClient({"owner/repo1": make_repo("owner/repo1")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        pipeline.run(SelectionCriteria(max_results=2))

        assert "pipeline_skip_duplicate" in caplog.text


class TestStatisticsAccuracy:
    def test_mixed_outcomes_produce_correct_counts(self):
        # 5 candidates: 1 duplicate, 1 fetch failure, 1 storage failure, 2 successes.
        names = ["owner/ok1", "owner/fetch-fail", "owner/storage-fail", "owner/ok2", "owner/ok1"]
        selector = FakeSelector(names)
        client = FakeClient(
            {
                "owner/ok1": make_repo("owner/ok1"),
                "owner/fetch-fail": GitHubAPIError("boom"),
                "owner/storage-fail": make_repo("owner/storage-fail"),
                "owner/ok2": make_repo("owner/ok2"),
            }
        )
        writer = FakeWriter(
            {"owner/storage-fail": RepositoryPersistenceError("owner/storage-fail", RuntimeError("db down"))}
        )
        pipeline = AcquisitionPipeline(
            client,
            selector,
            writer,
            rate_limiter=no_wait_rate_limiter(),
            retry_policy=instant_retry_policy(max_attempts=1),
        )

        result = pipeline.run(SelectionCriteria(max_results=5))

        assert result.stats.candidates_selected == 5
        assert result.stats.skipped_duplicates == 1
        assert result.stats.attempted == 4  # 5 candidates - 1 duplicate
        assert result.stats.succeeded == 2
        assert result.stats.failed == 2
        assert result.stats.attempted == result.stats.succeeded + result.stats.failed
        assert len(result.repository_ids) == 2
        assert {f.full_name for f in result.failures} == {"owner/fetch-fail", "owner/storage-fail"}
        assert {f.stage for f in result.failures} == {"fetch", "storage"}


class TestCrossLayerComposition:
    """Wires the REAL RepositorySelector (1.3.a) and REAL RepositoryWriter
    (1.3.b) — not pipeline-level fakes — into a REAL AcquisitionPipeline
    (1.3.c). Doubles only the true I/O boundary: the GitHub HTTP client's
    search/fetch methods, and the storage layer's database connection
    (tests.test_storage's FakeConnection/FakeIdRegistry). Proves selection
    -> fetch -> rate-limit check -> storage -> result accounting compose
    correctly using the genuine production classes, deterministically, with
    no live GitHub or PostgreSQL dependency — Checkpoint 1.3.d's own
    "cross-layer behaviour" requirement, distinct from the fakes-of-fakes
    scenarios above (which isolate AcquisitionPipeline's own logic) and
    from tests/test_storage.py (which isolates RepositoryWriter's own SQL).
    """

    def test_selection_through_storage_composes_end_to_end(self):
        from acquisition.selection import RepositorySelector
        from acquisition.storage import RepositoryWriter
        from github_client.rest import RepositorySearchPage
        from tests.test_storage import FakeConnection, FakeIdRegistry, make_repo_data

        repo = make_repo_data("octocat/Hello-World", github_id=1, topics=("ml", "data-science"))
        search_query = "stars:>=0 archived:false"

        class FullGitHubClientDouble:
            """Doubles both endpoints RepositorySelector/AcquisitionPipeline
            call — search_repositories() (selection) and get_repository()
            (fetch) — the only real I/O boundary in this composed run."""

            def __init__(self):
                self.search_calls = 0
                self.fetch_calls = []

            def search_repositories(self, query, sort="stars", order="desc", per_page=100, page=1):
                assert query == search_query
                self.search_calls += 1
                return RepositorySearchPage(total_count=1, incomplete_results=False, items=(repo,))

            def get_repository(self, owner, repo_name):
                self.fetch_calls.append(f"{owner}/{repo_name}")
                return repo

        client_double = FullGitHubClientDouble()
        selector = RepositorySelector(client_double)
        registry = FakeIdRegistry()
        fake_conn = FakeConnection(registry)
        writer = RepositoryWriter(connection_factory=lambda: fake_conn)
        pipeline = AcquisitionPipeline(client_double, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=1))

        assert client_double.search_calls == 1
        assert client_double.fetch_calls == ["octocat/Hello-World"]
        assert result.stats == AcquisitionStats(
            candidates_selected=1, attempted=1, succeeded=1, failed=0, skipped_duplicates=0
        )
        assert len(result.repository_ids) == 1
        assert registry.row_count("repositories") == 1
        assert registry.row_count("topics") == 2
        assert fake_conn.committed is True

    def test_storage_failure_in_composed_run_is_isolated_and_recorded(self):
        from acquisition.selection import RepositorySelector
        from acquisition.storage import RepositoryWriter
        from github_client.rest import RepositorySearchPage
        from tests.test_storage import FakeConnection, make_repo_data

        repo = make_repo_data("octocat/Hello-World", github_id=1)
        search_query = "stars:>=0 archived:false"

        class FullGitHubClientDouble:
            def search_repositories(self, query, sort="stars", order="desc", per_page=100, page=1):
                return RepositorySearchPage(total_count=1, incomplete_results=False, items=(repo,))

            def get_repository(self, owner, repo_name):
                return repo

        client_double = FullGitHubClientDouble()
        selector = RepositorySelector(client_double)
        fake_conn = FakeConnection(fail_on="INSERT INTO repositories")
        writer = RepositoryWriter(connection_factory=lambda: fake_conn)
        pipeline = AcquisitionPipeline(client_double, selector, writer, rate_limiter=no_wait_rate_limiter())

        result = pipeline.run(SelectionCriteria(max_results=1))

        assert result.stats.succeeded == 0
        assert result.stats.failed == 1
        assert result.failures[0].stage == "storage"
        assert result.failures[0].error_type == "RepositoryPersistenceError"
        assert fake_conn.rolled_back is True


class TestPipelineReusesExistingInfrastructure:
    """Confirms AcquisitionPipeline composes the real, unmodified
    RateLimiter/RetryPolicy/RepositorySelector/RepositoryWriter contracts -
    no new retry, rate-limit, HTTP, or database logic exists in
    acquisition/pipeline.py itself."""

    def test_default_collaborators_are_real_types_when_not_injected(self):
        selector = FakeSelector([])
        client = FakeClient({})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer)

        assert isinstance(pipeline._rate_limiter, RateLimiter)
        assert isinstance(pipeline._retry_policy, RetryPolicy)

    def test_selector_called_exactly_once_per_run(self):
        selector = FakeSelector(["owner/repo1"])
        client = FakeClient({"owner/repo1": make_repo("owner/repo1")})
        writer = FakeWriter()
        pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=no_wait_rate_limiter())

        criteria = SelectionCriteria(max_results=1)
        pipeline.run(criteria)

        assert selector.calls == [criteria]
