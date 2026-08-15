"""Documentation orchestration — Checkpoint 2.3.d.

Orchestrates the two already-independent, already-verified components
from 2.3.a/2.3.b into one batch run over the `repository_content` rows
already persisted by Checkpoint 1.4:

    repository_content (content_type='readme')
        -> extract_readme_features()                  (2.3.a — structural parsing)
        -> compute_readability()                       (2.3.b — Flesch scoring)
        -> DocumentationMetricsWriter.upsert_documentation_metrics()  (2.3.d — persistence)
        -> next repository

This module introduces no new parsing logic, no new readability logic,
and no new persistence SQL beyond what 2.3.a/2.3.b/2.3.d's writer already
define — it only sequences calls, mirroring exactly how
processing/technology_pipeline.py (2.2.c) and
acquisition/content_pipeline.py (1.4.d) orchestrate their own prior
sub-checkpoints without duplicating any of their logic.

Candidate source: ReadmeCandidateProvider, a new minimal read-only
component (this checkpoint's only new database access) that answers
"which repositories have a repository_content row with
content_type='readme'?" via one parameterized SELECT. By construction
(per acquisition/content_extractor.py's single-README-per-repository
selection, confirmed against the live corpus — 91 README rows across 91
distinct repositories, zero repositories with more than one), each
candidate is exactly one (repository_id, full_name, content) tuple — no
per-repository grouping is needed, unlike 2.2.c's manifest candidates
which could be many-per-repository.

It does NOT exclude repositories that already have `repository_metrics`
rows for today's snapshot_date — DocumentationMetricsWriter is already
the authoritative database-level idempotency boundary via
UNIQUE(repository_id, snapshot_date), exactly mirroring 2.2.c's and
1.4.d's rationale for their own candidate providers.

Deliberately does NOT process repositories that have no README row at
all. This mirrors 2.2.c's candidate-selection precedent exactly (that
orchestrator never wrote a "no technology" row for a repository with no
manifest content either) — no `repository_metrics` row is written for a
repository absent from `repository_content` with content_type='readme'.
`readme_present` is therefore always `True` for every row this pipeline
writes; a `readme_present=False` row is not fabricated for repositories
this pipeline never processes. This is a deliberate scope boundary
matching your explicit corpus-boundary instruction: 2.3 operates on the
91 repositories that already have persisted README content, not all 100.

Failure isolation: a persistence failure (DocumentationPersistenceError)
and any other unexpected exception (during parsing, readability scoring,
or persistence) are each caught, recorded as a structured
DocumentationFailure, logged, and the loop continues to the next
candidate — mirroring 2.2.c's isolation exactly. Unlike 2.2.c, there is
no per-manifest "parse_error" concept here: both `extract_readme_features`
(2.3.a) and `compute_readability` (2.3.b) never raise for any string
input, including malformed Markdown — they degrade to zero-valued or
`None` features instead, so there is no separate "partial success" state
to track at the orchestration level.

Does NOT compute or persist a documentation "quality score" — per the
2.3 design review (Sec12) and your explicit instruction, the frozen
schema does not clearly specify where that value belongs, and that
decision remains open, not made here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import psycopg

from config.settings import Settings, get_settings
from logging_setup import get_logger
from processing.documentation_storage import DocumentationMetricsWriter
from processing.exceptions import DocumentationPersistenceError
from processing.readability import ReadabilityScores, compute_readability
from processing.readme_parser import ReadmeFeatures, extract_readme_features

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


@dataclass(frozen=True)
class DocumentationCandidate:
    """One repository with a README row to process."""

    repository_id: uuid.UUID
    full_name: str
    content: str


class ReadmeCandidateProvider:
    """Reads (repository_id, full_name, content) from `repository_content`
    joined to `repositories`, filtered on `content_type='readme'` — the
    only new database access this checkpoint introduces. One
    parameterized SELECT; no exclusion of repositories already having
    `repository_metrics` rows (see module docstring). Holds no
    long-lived connection, matching ManifestCandidateProvider's
    precedent.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _default_connection_factory(
            settings or get_settings()
        )

    def get_candidates(self) -> list[DocumentationCandidate]:
        """Return one DocumentationCandidate per repository with a
        `content_type='readme'` row, ordered by repository id for a
        deterministic, stable candidate order across runs. Raises the
        underlying psycopg.Error uncaught — a failure to even obtain a
        candidate list has no partial-batch concept to isolate, matching
        2.2.c's precedent for selection failures.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rc.repository_id, r.full_name, rc.content
                    FROM repository_content rc
                    JOIN repositories r ON r.id = rc.repository_id
                    WHERE rc.content_type = 'readme'
                    ORDER BY r.id
                    """
                )
                rows = cur.fetchall()
            candidates = [
                DocumentationCandidate(repository_id=row[0], full_name=row[1], content=row[2]) for row in rows
            ]
            log.info("readme_candidate_provider_completed repositories=%s", len(candidates))
            return candidates
        finally:
            conn.close()


@dataclass(frozen=True)
class DocumentationFailure:
    """One candidate whose documentation metrics could not be persisted.

    `stage` distinguishes where the failure happened ("persist" — the
    writer's DocumentationPersistenceError, or "unexpected" — any other
    exception during parsing, readability scoring, or persistence),
    mirroring TechnologyFailure's `stage` field in
    processing/technology_pipeline.py.
    """

    repository_id: uuid.UUID
    full_name: str
    stage: str  # "persist" | "unexpected"
    error_type: str
    message: str


@dataclass(frozen=True)
class ProcessedRepository:
    """One candidate whose documentation metrics were successfully
    persisted."""

    repository_id: uuid.UUID
    full_name: str
    word_count: int
    section_count: int
    code_example_count: int
    readability_score: float | None


@dataclass(frozen=True)
class DocumentationAcquisitionStats:
    """Counts for one DocumentationPipeline.run() call."""

    candidates_selected: int
    attempted: int
    succeeded: int
    failed: int


@dataclass(frozen=True)
class DocumentationAcquisitionResult:
    """The outcome of one DocumentationPipeline.run() call."""

    stats: DocumentationAcquisitionStats
    processed: tuple[ProcessedRepository, ...] = field(default_factory=tuple)
    failures: tuple[DocumentationFailure, ...] = field(default_factory=tuple)


class DocumentationPipeline:
    """Orchestrates candidate selection -> extract -> score -> persist for
    one batch of repositories that already have a persisted README.

    Every collaborator is dependency-injected (constructor arguments),
    matching TechnologyPipeline's/ContentAcquisitionPipeline's precedent
    — this class holds no global state and is fully testable against
    fakes, with the real extract_readme_features/compute_readability/
    DocumentationMetricsWriter used by default so production callers get
    correct behavior without extra configuration.
    """

    def __init__(
        self,
        candidate_provider: ReadmeCandidateProvider,
        writer: DocumentationMetricsWriter,
        extract_features_fn: Callable[[str], ReadmeFeatures] = extract_readme_features,
        compute_readability_fn: Callable[[str], ReadabilityScores] = compute_readability,
        snapshot_date_fn: Callable[[], date] = date.today,
    ) -> None:
        self._candidate_provider = candidate_provider
        self._writer = writer
        self._extract_features = extract_features_fn
        self._compute_readability = compute_readability_fn
        self._snapshot_date = snapshot_date_fn

    def run(self) -> DocumentationAcquisitionResult:
        """Obtain candidates and process each one: extract structural
        features, compute readability, persist. A failure for one
        repository is caught, recorded, and logged; the loop always
        continues to the next candidate. Only a failure while obtaining
        the candidate list itself (a single upfront call, not
        per-candidate) is NOT caught here.
        """
        candidates = self._candidate_provider.get_candidates()
        total = len(candidates)

        log.info("documentation_pipeline_started candidates=%s", total)

        processed: list[ProcessedRepository] = []
        failures: list[DocumentationFailure] = []

        for index, candidate in enumerate(candidates, start=1):
            log.info(
                "documentation_pipeline_repository_started full_name=%s repository_id=%s index=%s/%s",
                candidate.full_name,
                candidate.repository_id,
                index,
                total,
            )
            outcome = self._process_one(candidate)
            if isinstance(outcome, DocumentationFailure):
                failures.append(outcome)
                log.error(
                    "documentation_pipeline_repository_failed full_name=%s repository_id=%s stage=%s error=%s index=%s/%s",
                    candidate.full_name,
                    candidate.repository_id,
                    outcome.stage,
                    outcome.error_type,
                    index,
                    total,
                )
            else:
                processed.append(outcome)
                log.info(
                    "documentation_pipeline_repository_succeeded full_name=%s repository_id=%s "
                    "word_count=%s section_count=%s code_example_count=%s index=%s/%s",
                    candidate.full_name,
                    candidate.repository_id,
                    outcome.word_count,
                    outcome.section_count,
                    outcome.code_example_count,
                    index,
                    total,
                )

        stats = DocumentationAcquisitionStats(
            candidates_selected=total,
            attempted=total,
            succeeded=len(processed),
            failed=len(failures),
        )
        log.info(
            "documentation_pipeline_completed candidates=%s attempted=%s succeeded=%s failed=%s",
            stats.candidates_selected,
            stats.attempted,
            stats.succeeded,
            stats.failed,
        )
        return DocumentationAcquisitionResult(
            stats=stats,
            processed=tuple(processed),
            failures=tuple(failures),
        )

    def _process_one(self, candidate: DocumentationCandidate) -> ProcessedRepository | DocumentationFailure:
        """Extract features, compute readability, and persist one
        repository's documentation metrics. Returns a ProcessedRepository
        on success or a DocumentationFailure describing which stage
        failed.
        """
        try:
            features = self._extract_features(candidate.content)
            readability = self._compute_readability(candidate.content)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "documentation_pipeline_unexpected_error full_name=%s repository_id=%s",
                candidate.full_name,
                candidate.repository_id,
            )
            return DocumentationFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )

        try:
            self._writer.upsert_documentation_metrics(
                candidate.repository_id,
                self._snapshot_date(),
                readme_present=True,
                readme_word_count=features.word_count,
                readme_section_count=features.section_count,
                readme_code_example_count=features.code_example_count,
                readme_readability_score=readability.flesch_reading_ease,
            )
        except DocumentationPersistenceError as exc:
            return DocumentationFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="persist",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "documentation_pipeline_unexpected_error full_name=%s repository_id=%s",
                candidate.full_name,
                candidate.repository_id,
            )
            return DocumentationFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
            )

        return ProcessedRepository(
            repository_id=candidate.repository_id,
            full_name=candidate.full_name,
            word_count=features.word_count,
            section_count=features.section_count,
            code_example_count=features.code_example_count,
            readability_score=readability.flesch_reading_ease,
        )
