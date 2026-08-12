"""Content acquisition orchestration — Checkpoint 1.4.d.

Orchestrates the three already-independent, already-verified components
from 1.4.a/1.4.b/1.4.c into one batch run over the repositories already
persisted by Checkpoint 1.3:

    repository candidate (id, full_name)
        -> RepositoryCloner.clone()        (1.4.a — temporary shallow clone)
        -> extract_content()               (1.4.b — README/manifest extraction)
        -> ContentWriter.upsert_content()  (1.4.c — durable persistence)
        -> next candidate

This module introduces no new git logic, no new README/manifest
detection logic, no new hashing, and no new persistence/upsert SQL — it
only sequences calls to RepositoryCloner.clone() (1.4.a),
extract_content() (1.4.b), and ContentWriter.upsert_content() (1.4.c),
all unmodified, exactly mirroring how acquisition/pipeline.py (1.3.c)
orchestrates 1.1/1.3.a/1.3.b without duplicating any of their logic.

Why orchestration is a separate responsibility from cloning, extraction,
and persistence: each of those three answers a self-contained question
with no notion of a *batch* — a sequence of many repositories where one
failure must not abort the rest, where progress needs to be observable
mid-run, and where success/failure needs to be counted across the whole
set. That batch-control-flow concern (continue-on-error, per-repository
failure isolation, counting) is what ContentAcquisitionPipeline owns,
and it is genuinely distinct from any of the three prior modules' jobs.

Candidate source: RepositoryContentCandidateProvider, a new minimal
read-only component (this checkpoint's only new database access) that
answers "which repositories exist in PostgreSQL and are candidates for
content acquisition?" via one parameterized, unfiltered SELECT. It does
NOT exclude repositories that already have repository_content rows —
per explicit design decision, since ContentWriter (1.4.c) is already the
authoritative database-level idempotency boundary, and skipping based on
"already has some content row" would encode orchestration/business logic
about what counts as "fully acquired" that has not been specified (e.g.
it would risk silently skipping a repository whose future manifest
support hasn't been backfilled yet). Re-running this pipeline is safe
without any candidate-level filtering because upsert_content() already
guarantees idempotent persistence — orchestration-level resumability
here means simply "re-running the whole batch is safe and cheap to
reason about," not "the orchestrator remembers what it already did."

Failure isolation (deliberately broader than acquisition/pipeline.py's
two-typed-exception catch): a clone failure (RepositoryCloneError), an
extraction-layer failure (ContentAcquisitionError — extract_content()
itself does not raise for per-file problems, it skips and logs
internally per 1.4.b, but this catch exists as defense in depth for any
future/injected extractor that does), a persistence failure
(ContentPersistenceError), and any other unexpected exception are each
caught individually, recorded as a structured ContentFailure, logged,
and the loop continues to the next candidate. This is intentionally
wider than AcquisitionPipeline's catch (which only catches its own two
typed-exception hierarchies) — an explicit, approved divergence for this
checkpoint, not an oversight: an unexpected exception during content
acquisition for one repository must never silently abort the batch, and
must never be silently swallowed either — it is always recorded with its
real exception class name/message and logged at ERROR.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psycopg

from acquisition.clone_workspace import RepositoryCloner
from acquisition.content_extractor import ExtractedFile, extract_content
from acquisition.content_storage import ContentWriter
from acquisition.exceptions import ContentAcquisitionError, RepositoryCloneError
from config.settings import Settings, get_settings
from logging_setup import get_logger

log = get_logger(__name__)

ConnectionFactory = Callable[[], "psycopg.Connection"]


def _default_connection_factory(settings: Settings) -> ConnectionFactory:
    def _connect() -> psycopg.Connection:
        return psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )

    return _connect


class RepositoryContentCandidateProvider:
    """Reads (id, full_name) pairs from `repositories` — the only new
    database access this checkpoint introduces. One parameterized,
    unfiltered SELECT; no JOIN, no NOT EXISTS, no repository_content
    filtering (see module docstring for why). Holds no long-lived
    connection, matching RepositoryWriter/ContentWriter's precedent.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _default_connection_factory(
            settings or get_settings()
        )

    def get_candidates(self) -> list[tuple[uuid.UUID, str]]:
        """Return every (repositories.id, repositories.full_name) pair,
        ordered by id for a deterministic, stable candidate order across
        runs. Raises the underlying psycopg.Error uncaught — a failure to
        even obtain a candidate list has no partial-batch concept to
        isolate, matching acquisition/pipeline.py's precedent for
        selection failures.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, full_name FROM repositories ORDER BY id")
                rows = cur.fetchall()
            log.info("candidate_provider_completed candidates=%s", len(rows))
            return [(row[0], row[1]) for row in rows]
        finally:
            conn.close()


@dataclass(frozen=True)
class ContentFailure:
    """One candidate whose content could not be persisted.

    `stage` distinguishes where the failure happened ("clone" — 1.4.a's
    RepositoryCloneError, "extract" — a ContentAcquisitionError raised by
    the extraction step, "persist" — 1.4.c's ContentPersistenceError, or
    "unexpected" — any other exception) since each failure class implies
    a different follow-up, exactly mirroring RepositoryFailure's `stage`
    field in acquisition/pipeline.py.
    """

    repository_id: uuid.UUID
    full_name: str
    stage: str  # "clone" | "extract" | "persist" | "unexpected"
    error_type: str
    message: str


@dataclass(frozen=True)
class ProcessedRepository:
    """One candidate whose content was successfully persisted (possibly
    zero files — an empty extraction result is a success, not a
    failure)."""

    repository_id: uuid.UUID
    full_name: str
    files_persisted: int


@dataclass(frozen=True)
class ContentAcquisitionStats:
    """Counts for one ContentAcquisitionPipeline.run() call."""

    candidates_selected: int
    attempted: int
    succeeded: int
    failed: int


@dataclass(frozen=True)
class ContentAcquisitionResult:
    """The outcome of one ContentAcquisitionPipeline.run() call."""

    stats: ContentAcquisitionStats
    processed: tuple[ProcessedRepository, ...] = field(default_factory=tuple)
    failures: tuple[ContentFailure, ...] = field(default_factory=tuple)


class ContentAcquisitionPipeline:
    """Orchestrates candidate acquisition -> clone -> extract -> persist
    for one batch of repositories already present in `repositories`.

    Every collaborator is dependency-injected (constructor arguments),
    matching acquisition/pipeline.py's AcquisitionPipeline precedent —
    this class holds no global state and is fully testable against
    fakes, with the real RepositoryCloner/ContentWriter/extract_content
    used by default so production callers get correct behavior without
    extra configuration.
    """

    def __init__(
        self,
        candidate_provider: RepositoryContentCandidateProvider,
        cloner: RepositoryCloner,
        writer: ContentWriter,
        extract_content_fn: Callable[[Path], list[ExtractedFile]] = extract_content,
    ) -> None:
        self._candidate_provider = candidate_provider
        self._cloner = cloner
        self._writer = writer
        self._extract_content = extract_content_fn

    def run(self) -> ContentAcquisitionResult:
        """Obtain candidates and process each one: clone, extract,
        persist. A failure for one repository is caught, recorded, and
        logged; the loop always continues to the next candidate. Only a
        failure while obtaining the candidate list itself (a single
        upfront call, not per-candidate) is NOT caught here — there is
        no partial-batch concept before any candidates exist to iterate.
        """
        candidates = self._candidate_provider.get_candidates()
        total = len(candidates)

        log.info("content_pipeline_started candidates=%s", total)

        processed: list[ProcessedRepository] = []
        failures: list[ContentFailure] = []

        for index, (repository_id, full_name) in enumerate(candidates, start=1):
            log.info(
                "content_pipeline_repository_started full_name=%s repository_id=%s index=%s/%s",
                full_name,
                repository_id,
                index,
                total,
            )
            outcome = self._process_one(repository_id, full_name)
            if isinstance(outcome, ContentFailure):
                failures.append(outcome)
                log.error(
                    "content_pipeline_repository_failed full_name=%s repository_id=%s stage=%s error=%s index=%s/%s",
                    full_name,
                    repository_id,
                    outcome.stage,
                    outcome.error_type,
                    index,
                    total,
                )
            else:
                processed.append(outcome)
                log.info(
                    "content_pipeline_repository_succeeded full_name=%s repository_id=%s files_persisted=%s index=%s/%s",
                    full_name,
                    repository_id,
                    outcome.files_persisted,
                    index,
                    total,
                )

        stats = ContentAcquisitionStats(
            candidates_selected=total,
            attempted=len(candidates),
            succeeded=len(processed),
            failed=len(failures),
        )
        log.info(
            "content_pipeline_completed candidates=%s attempted=%s succeeded=%s failed=%s",
            stats.candidates_selected,
            stats.attempted,
            stats.succeeded,
            stats.failed,
        )
        return ContentAcquisitionResult(
            stats=stats,
            processed=tuple(processed),
            failures=tuple(failures),
        )

    def _process_one(
        self, repository_id: uuid.UUID, full_name: str
    ) -> ProcessedRepository | ContentFailure:
        """Clone, extract, and persist one repository's content. Returns
        a ProcessedRepository on success (including zero files extracted
        — a legitimate outcome, not a failure) or a ContentFailure
        describing exactly which stage failed. The clone workspace is
        always cleaned up by RepositoryCloner's own context-manager
        contract (1.4.a) regardless of which branch below is taken —
        this method introduces no cleanup logic of its own.
        """
        try:
            with self._cloner.clone(full_name) as clone_dir:
                files = self._extract_content(clone_dir)
        except RepositoryCloneError as exc:
            return ContentFailure(
                repository_id=repository_id,
                full_name=full_name,
                stage="clone",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )
        except ContentAcquisitionError as exc:
            return ContentFailure(
                repository_id=repository_id,
                full_name=full_name,
                stage="extract",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "content_pipeline_unexpected_error full_name=%s repository_id=%s",
                full_name,
                repository_id,
            )
            return ContentFailure(
                repository_id=repository_id,
                full_name=full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )

        try:
            content_ids = self._writer.upsert_content(repository_id, files)
        except ContentAcquisitionError as exc:
            return ContentFailure(
                repository_id=repository_id,
                full_name=full_name,
                stage="persist",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "content_pipeline_unexpected_error full_name=%s repository_id=%s",
                full_name,
                repository_id,
            )
            return ContentFailure(
                repository_id=repository_id,
                full_name=full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )

        return ProcessedRepository(
            repository_id=repository_id,
            full_name=full_name,
            files_persisted=len(content_ids),
        )
