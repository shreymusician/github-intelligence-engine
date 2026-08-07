"""Repository acquisition pipeline — Checkpoint 1.3.c.

Orchestrates the three already-independent, already-verified components
from 1.1/1.3.a/1.3.b into one batch run: select candidates, fetch each
one's detail, respect GitHub's rate limit between fetches, persist each
one, and keep going after individual failures. This module introduces
no new HTTP logic, no new retry/rate-limit logic, and no new database
logic - it only sequences calls to GitHubRESTClient.get_repository()
(1.1.c), RepositorySelector.select_candidates() (1.3.a),
RepositoryWriter.upsert_repository() (1.3.b), RateLimiter.wait_if_needed()
(1.1.d), and RetryPolicy.call() (1.1.e), all unmodified.

Why orchestration is a separate responsibility from selection and
storage: selection (1.3.a) answers "which repositories," a pure decision
with no side effects and no database dependency. Storage (1.3.b) answers
"how is one already-fetched RepositoryData persisted," a self-contained
transactional problem with no HTTP dependency. Neither module has, or
should have, any notion of a *batch* - a sequence of many repositories
where one failure must not abort the rest, where progress needs to be
observable mid-run, and where success/failure needs to be counted across
the whole set. That batch-control-flow concern (continue-on-error,
counting, defensive dedup, bounding to max_results) is what
AcquisitionPipeline owns, and it is genuinely distinct from either prior
module's job - keeping it separate is what let 1.3.a's 109 tests and
1.3.b's 30 live checks each run without any dependency on the other two
pieces existing yet.

Resume strategy: no persistent checkpoint/offset file exists here, by
design, not omission. RepositoryWriter.upsert_repository() is fully
idempotent (every write is keyed on GitHub's own natural key -
github_id, spdx_id, slug, or (repository_id, snapshot_date) -
CHECKPOINT_1_3B_REPORT.md Sec6.1), so simply re-running
AcquisitionPipeline.run() with the same SelectionCriteria is the resume
mechanism: already-persisted repositories are upserted again harmlessly
(refreshed, not duplicated), and repositories that failed on a prior run
are naturally retried since they're re-selected and re-fetched. Durable,
position-aware resume (e.g. "continue from candidate 47 of 200 without
re-fetching 1-46") is explicitly out of scope - that is
IMPLEMENTATION_ROADMAP.md Checkpoint 1.6's job (Incremental Update
Strategy), which needs change-detection semantics this checkpoint has no
reason to anticipate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from acquisition.exceptions import AcquisitionStorageError
from acquisition.selection import RepositorySelector, SelectionCriteria
from acquisition.storage import RepositoryWriter
from github_client.exceptions import GitHubClientError
from github_client.rate_limiter import RateLimiter
from github_client.rest import GitHubRESTClient
from github_client.retry import RetryPolicy
from logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RepositoryFailure:
    """One candidate that did not make it into the database.

    `stage` distinguishes where the failure happened ("fetch" - GitHub
    API/retry-exhausted error, or "storage" - RepositoryWriter/database
    error) since the two failure classes need different follow-up (a
    fetch failure might mean the repository is gone or renamed; a
    storage failure might mean a database problem affecting every
    subsequent candidate too). A plain string or tuple would lose this
    distinction without a caller re-parsing an error message.
    """

    full_name: str
    stage: str  # "fetch" | "storage"
    error_type: str
    message: str


@dataclass(frozen=True)
class AcquisitionStats:
    """Counts for one AcquisitionPipeline.run() call."""

    candidates_selected: int
    attempted: int
    succeeded: int
    failed: int
    skipped_duplicates: int


@dataclass(frozen=True)
class PipelineResult:
    """The outcome of one AcquisitionPipeline.run() call.

    `repository_ids` holds the repositories.id (from RepositoryWriter)
    for every successful upsert, in the order they completed -
    consumable directly by a caller that wants to know exactly which
    rows were touched, without re-querying the database.
    """

    stats: AcquisitionStats
    repository_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)
    failures: tuple[RepositoryFailure, ...] = field(default_factory=tuple)


class AcquisitionPipeline:
    """Orchestrates selection -> fetch -> rate-limit check -> storage for
    one batch of repositories.

    Every collaborator is dependency-injected (constructor arguments),
    matching this project's established pattern (GitHubRESTClient takes
    an injectable Settings/Session, RepositoryWriter takes an injectable
    Settings/connection_factory) - this class holds no global state and
    is fully testable against fakes, with the real RateLimiter/RetryPolicy
    used by default so production callers get correct behavior without
    having to remember to configure them.
    """

    def __init__(
        self,
        client: GitHubRESTClient,
        selector: RepositorySelector,
        writer: RepositoryWriter,
        rate_limiter: RateLimiter | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._client = client
        self._selector = selector
        self._writer = writer
        self._rate_limiter = rate_limiter or RateLimiter()
        self._retry_policy = retry_policy or RetryPolicy()

    def run(self, criteria: SelectionCriteria) -> PipelineResult:
        """Select candidates matching `criteria`, fetch and persist each
        one, and return a PipelineResult summarizing the run.

        Execution order per candidate (matches the checkpoint's specified
        flow exactly): fetch -> rate-limit check -> upsert -> progress
        log -> next. A failure at either fetch or storage is caught,
        recorded, logged, and the loop continues to the next candidate -
        one bad repository never aborts the batch. Failures during
        selection itself (a single upfront call, not per-candidate) are
        NOT caught here and propagate to the caller - there is no
        partial-batch concept before any candidates exist to iterate.
        """
        candidates = self._selector.select_candidates(criteria)
        # Defensive re-bound to max_results and re-dedup: RepositorySelector
        # already guarantees both (CHECKPOINT_1_3A_REPORT.md Sec5/Sec6), but
        # the checkpoint spec names "skip duplicates" and "respect
        # max_results" as this pipeline's own responsibilities too - kept
        # here as an explicit contract of AcquisitionPipeline.run() itself,
        # independent of any particular RepositorySelector implementation
        # (including test doubles, which is exactly what Phase 3's
        # "duplicate candidate handling" mocked test exercises).
        candidates = candidates[: criteria.max_results]

        log.info(
            "pipeline_started candidates=%s max_results=%s",
            len(candidates),
            criteria.max_results,
        )

        seen: set[str] = set()
        repository_ids: list[uuid.UUID] = []
        failures: list[RepositoryFailure] = []
        skipped_duplicates = 0
        attempted = 0
        total = len(candidates)

        for index, full_name in enumerate(candidates, start=1):
            if full_name in seen:
                skipped_duplicates += 1
                log.info(
                    "pipeline_skip_duplicate full_name=%s index=%s/%s",
                    full_name,
                    index,
                    total,
                )
                continue
            seen.add(full_name)
            attempted += 1

            owner, _, repo = full_name.partition("/")
            if not owner or not repo:
                failures.append(
                    RepositoryFailure(
                        full_name=full_name,
                        stage="fetch",
                        error_type="ValueError",
                        message=f"Malformed candidate full_name: {full_name!r}",
                    )
                )
                log.error(
                    "pipeline_repository_failed full_name=%s stage=fetch error=ValueError index=%s/%s",
                    full_name,
                    index,
                    total,
                )
                continue

            try:
                data = self._retry_policy.call(lambda o=owner, r=repo: self._client.get_repository(o, r))
            except GitHubClientError as exc:
                failures.append(
                    RepositoryFailure(
                        full_name=full_name,
                        stage="fetch",
                        error_type=exc.__class__.__name__,
                        message=str(exc),
                    )
                )
                log.error(
                    "pipeline_repository_failed full_name=%s stage=fetch error=%s index=%s/%s",
                    full_name,
                    exc.__class__.__name__,
                    index,
                    total,
                )
                continue

            self._rate_limiter.wait_if_needed(data.rate_limit)

            try:
                repository_id = self._writer.upsert_repository(data)
            except AcquisitionStorageError as exc:
                failures.append(
                    RepositoryFailure(
                        full_name=full_name,
                        stage="storage",
                        error_type=exc.__class__.__name__,
                        message=str(exc),
                    )
                )
                log.error(
                    "pipeline_repository_failed full_name=%s stage=storage error=%s index=%s/%s",
                    full_name,
                    exc.__class__.__name__,
                    index,
                    total,
                )
                continue

            repository_ids.append(repository_id)
            log.info(
                "pipeline_repository_succeeded full_name=%s repository_id=%s index=%s/%s",
                full_name,
                repository_id,
                index,
                total,
            )

        stats = AcquisitionStats(
            candidates_selected=total,
            attempted=attempted,
            succeeded=len(repository_ids),
            failed=len(failures),
            skipped_duplicates=skipped_duplicates,
        )
        log.info(
            "pipeline_completed candidates=%s attempted=%s succeeded=%s failed=%s skipped_duplicates=%s",
            stats.candidates_selected,
            stats.attempted,
            stats.succeeded,
            stats.failed,
            stats.skipped_duplicates,
        )
        return PipelineResult(
            stats=stats,
            repository_ids=tuple(repository_ids),
            failures=tuple(failures),
        )
