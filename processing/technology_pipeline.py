"""Technology detection orchestration — Checkpoint 2.2.c.

Orchestrates the three already-independent, already-verified components
from 2.2.a/2.2.b into one batch run over the repository_content rows
already persisted by Checkpoint 1.4:

    repository_content (manifest rows)
        -> parse_manifest()                    (2.2.a — deterministic parsing)
        -> classify_language()/classify_package() (2.2.b — taxonomy mapping)
        -> TechnologyWriter.upsert_technologies()  (2.2.b — durable persistence)
        -> next repository

This module introduces no new parsing logic, no new taxonomy/classification
logic, and no new persistence/upsert SQL for technologies — it only
sequences calls to parse_manifest() (2.2.a), classify_language()/
classify_package() (2.2.b), and TechnologyWriter.upsert_technologies()
(2.2.b), all unmodified, mirroring exactly how
acquisition/content_pipeline.py (1.4.d) orchestrates 1.4.a/1.4.b/1.4.c
without duplicating any of their logic.

Candidate source: ManifestCandidateProvider, a new minimal read-only
component (this checkpoint's only new database access) that answers
"which repositories have at least one repository_content row whose
content_type is a manifest format 2.2.a supports?" via one parameterized
SELECT filtered on
`processing.manifest_parser.SUPPORTED_MANIFEST_CONTENT_TYPES`, joined to
`repositories` only for `full_name` (used for logging, matching 1.4.d's
candidate provider). It does NOT filter out repositories that already
have `repository_technologies` rows — TechnologyWriter (2.2.b) is
already the authoritative database-level idempotency boundary, exactly
mirroring 1.4.d's rationale for RepositoryContentCandidateProvider.

Language classification is deliberately NOT triggered by a manifest's
mere existence when that manifest parses to zero dependencies — even
though 2.2.b's `classify_language(ecosystem)` docstring notes a manifest
existing is itself evidence of that ecosystem's language. Deriving an
ecosystem from `content_type` alone (independent of any parsed
dependency) would require a new content_type -> ecosystem mapping that
neither 2.2.a nor 2.2.b expose as reusable, tested logic — inventing one
here would be a new classification mechanism, out of this checkpoint's
scope. Instead, `classify_language` is called once per distinct
`ParsedDependency.ecosystem` actually observed among a repository's
parsed dependencies. This is the conservative reading of "do not
manufacture a technology just because a manifest exists": a manifest
that parses to zero dependencies contributes no language classification
either. This is a known, documented limitation (see the 2.2.c report),
not an oversight.

Failure isolation: a persistence failure (TechnologyPersistenceError)
and any other unexpected exception (during parsing, classification, or
persistence) are each caught, recorded as a structured
TechnologyFailure, logged, and the loop continues to the next candidate.
A per-manifest parse_error (2.2.a's own "report failure as data"
contract — parse_manifest() never raises) is not a repository-level
failure at all: it is counted in the returned statistics and processing
continues with that repository's remaining manifests, exactly as the
EMPTY/UNCLASSIFIED CASES section of this checkpoint's brief specifies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable

import psycopg

from config.settings import Settings, get_settings
from logging_setup import get_logger
from processing.exceptions import TechnologyPersistenceError
from processing.manifest_parser import (
    SUPPORTED_MANIFEST_CONTENT_TYPES,
    ManifestParseResult,
    ParsedDependency,
    parse_manifest,
)
from processing.technology_storage import TechnologyWriter
from processing.technology_taxonomy import Classification, classify_language, classify_package

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
class ManifestRow:
    """One repository_content row this checkpoint recognizes as a manifest."""

    content_type: str
    content: str


@dataclass(frozen=True)
class TechnologyCandidate:
    """One repository with at least one manifest row to process."""

    repository_id: uuid.UUID
    full_name: str
    manifests: tuple[ManifestRow, ...]


class ManifestCandidateProvider:
    """Reads (repository_id, full_name, manifest rows) from
    `repository_content` joined to `repositories` — the only new database
    access this checkpoint introduces. One parameterized SELECT filtered
    on `SUPPORTED_MANIFEST_CONTENT_TYPES`; no exclusion of repositories
    already having `repository_technologies` rows (see module docstring).
    Holds no long-lived connection, matching RepositoryContentCandidateProvider's
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

    def get_candidates(self) -> list[TechnologyCandidate]:
        """Return one TechnologyCandidate per distinct repository that has
        at least one manifest-typed `repository_content` row, ordered by
        repository id for a deterministic, stable candidate order across
        runs. Raises the underlying psycopg.Error uncaught — a failure to
        even obtain a candidate list has no partial-batch concept to
        isolate, matching 1.4.d's precedent for selection failures.
        """
        conn = self._connection_factory()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT rc.repository_id, r.full_name, rc.content_type, rc.content
                    FROM repository_content rc
                    JOIN repositories r ON r.id = rc.repository_id
                    WHERE rc.content_type = ANY(%(content_types)s)
                    ORDER BY r.id, rc.id
                    """,
                    {"content_types": list(SUPPORTED_MANIFEST_CONTENT_TYPES)},
                )
                rows = cur.fetchall()

            grouped: dict[uuid.UUID, tuple[str, list[ManifestRow]]] = {}
            for repository_id, full_name, content_type, content in rows:
                if repository_id not in grouped:
                    grouped[repository_id] = (full_name, [])
                grouped[repository_id][1].append(ManifestRow(content_type=content_type, content=content))

            candidates = [
                TechnologyCandidate(repository_id=repo_id, full_name=full_name, manifests=tuple(manifest_rows))
                for repo_id, (full_name, manifest_rows) in grouped.items()
            ]
            log.info(
                "manifest_candidate_provider_completed repositories=%s manifest_rows=%s",
                len(candidates),
                len(rows),
            )
            return candidates
        finally:
            conn.close()


@dataclass(frozen=True)
class TechnologyFailure:
    """One candidate whose technologies could not be persisted.

    `stage` distinguishes where the failure happened ("persist" — 2.2.b's
    TechnologyPersistenceError, or "unexpected" — any other exception
    during parsing, classification, or persistence), mirroring
    ContentFailure's `stage` field in acquisition/content_pipeline.py.
    Parsing/classification work already completed before the failure is
    still reported via the manifests_processed/dependencies_parsed/
    parse_errors fields, since a persist-stage failure only means this
    repository's technologies were rolled back — not that no work
    happened.
    """

    repository_id: uuid.UUID
    full_name: str
    stage: str  # "persist" | "unexpected"
    error_type: str
    message: str
    manifests_processed: int = 0
    dependencies_parsed: int = 0
    parse_errors: int = 0


@dataclass(frozen=True)
class ProcessedRepository:
    """One candidate whose classified technologies were successfully
    persisted (possibly zero — a manifest with no recognized dependencies
    is a success, not a failure)."""

    repository_id: uuid.UUID
    full_name: str
    manifests_processed: int
    dependencies_parsed: int
    parse_errors: int
    technologies_persisted: int


@dataclass(frozen=True)
class TechnologyAcquisitionStats:
    """Counts for one TechnologyPipeline.run() call. Every field is a
    directly measured count — see the 2.2.c report for what each one
    does and does not represent (e.g. `technologies_persisted` counts
    repository_technologies relationship writes, not unique technology
    identities)."""

    candidates_selected: int
    attempted: int
    succeeded: int
    failed: int
    manifests_processed: int
    dependencies_parsed: int
    parse_errors: int
    technologies_persisted: int


@dataclass(frozen=True)
class TechnologyAcquisitionResult:
    """The outcome of one TechnologyPipeline.run() call."""

    stats: TechnologyAcquisitionStats
    processed: tuple[ProcessedRepository, ...] = field(default_factory=tuple)
    failures: tuple[TechnologyFailure, ...] = field(default_factory=tuple)


class TechnologyPipeline:
    """Orchestrates candidate selection -> parse -> classify -> persist
    for one batch of repositories that already have manifest content.

    Every collaborator is dependency-injected (constructor arguments),
    matching ContentAcquisitionPipeline's precedent — this class holds no
    global state and is fully testable against fakes, with the real
    parse_manifest/classify_language/classify_package/TechnologyWriter
    used by default so production callers get correct behavior without
    extra configuration.
    """

    def __init__(
        self,
        candidate_provider: ManifestCandidateProvider,
        writer: TechnologyWriter,
        parse_manifest_fn: Callable[[str, str], ManifestParseResult] = parse_manifest,
        classify_language_fn: Callable[[str], Classification | None] = classify_language,
        classify_package_fn: Callable[..., Classification | None] = classify_package,
    ) -> None:
        self._candidate_provider = candidate_provider
        self._writer = writer
        self._parse_manifest = parse_manifest_fn
        self._classify_language = classify_language_fn
        self._classify_package = classify_package_fn

    def run(self) -> TechnologyAcquisitionResult:
        """Obtain candidates and process each one: parse every manifest,
        classify every parsed dependency, persist the classifications. A
        failure for one repository is caught, recorded, and logged; the
        loop always continues to the next candidate. Only a failure while
        obtaining the candidate list itself (a single upfront call, not
        per-candidate) is NOT caught here — there is no partial-batch
        concept before any candidates exist to iterate.
        """
        candidates = self._candidate_provider.get_candidates()
        total = len(candidates)

        log.info("technology_pipeline_started candidates=%s", total)

        processed: list[ProcessedRepository] = []
        failures: list[TechnologyFailure] = []

        for index, candidate in enumerate(candidates, start=1):
            log.info(
                "technology_pipeline_repository_started full_name=%s repository_id=%s index=%s/%s",
                candidate.full_name,
                candidate.repository_id,
                index,
                total,
            )
            outcome = self._process_one(candidate)
            if isinstance(outcome, TechnologyFailure):
                failures.append(outcome)
                log.error(
                    "technology_pipeline_repository_failed full_name=%s repository_id=%s stage=%s error=%s index=%s/%s",
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
                    "technology_pipeline_repository_succeeded full_name=%s repository_id=%s "
                    "technologies_persisted=%s index=%s/%s",
                    candidate.full_name,
                    candidate.repository_id,
                    outcome.technologies_persisted,
                    index,
                    total,
                )

        stats = TechnologyAcquisitionStats(
            candidates_selected=total,
            attempted=total,
            succeeded=len(processed),
            failed=len(failures),
            manifests_processed=sum(p.manifests_processed for p in processed)
            + sum(f.manifests_processed for f in failures),
            dependencies_parsed=sum(p.dependencies_parsed for p in processed)
            + sum(f.dependencies_parsed for f in failures),
            parse_errors=sum(p.parse_errors for p in processed) + sum(f.parse_errors for f in failures),
            technologies_persisted=sum(p.technologies_persisted for p in processed),
        )
        log.info(
            "technology_pipeline_completed candidates=%s attempted=%s succeeded=%s failed=%s "
            "manifests_processed=%s dependencies_parsed=%s parse_errors=%s technologies_persisted=%s",
            stats.candidates_selected,
            stats.attempted,
            stats.succeeded,
            stats.failed,
            stats.manifests_processed,
            stats.dependencies_parsed,
            stats.parse_errors,
            stats.technologies_persisted,
        )
        return TechnologyAcquisitionResult(
            stats=stats,
            processed=tuple(processed),
            failures=tuple(failures),
        )

    def _process_one(self, candidate: TechnologyCandidate) -> ProcessedRepository | TechnologyFailure:
        """Parse every manifest for one repository, classify every parsed
        dependency, and persist the resulting classifications. Returns a
        ProcessedRepository on success (including zero technologies
        persisted — a legitimate outcome, not a failure) or a
        TechnologyFailure describing which stage failed.
        """
        manifests_processed = 0
        dependencies_parsed = 0
        parse_errors = 0
        dependencies: list[ParsedDependency] = []

        try:
            for manifest in candidate.manifests:
                result = self._parse_manifest(manifest.content_type, manifest.content)
                manifests_processed += 1
                if result.parse_error is not None:
                    parse_errors += 1
                    log.info(
                        "technology_pipeline_manifest_parse_error full_name=%s repository_id=%s "
                        "content_type=%s error=%s",
                        candidate.full_name,
                        candidate.repository_id,
                        manifest.content_type,
                        result.parse_error,
                    )
                    continue
                dependencies_parsed += len(result.dependencies)
                dependencies.extend(result.dependencies)

            classifications: list[Classification] = []
            seen_ecosystems: set[str] = set()
            for dependency in dependencies:
                if dependency.ecosystem not in seen_ecosystems:
                    seen_ecosystems.add(dependency.ecosystem)
                    language = self._classify_language(dependency.ecosystem)
                    if language is not None:
                        classifications.append(language)
                package = self._classify_package(
                    dependency.name, dependency.ecosystem, is_dev=dependency.is_dev
                )
                if package is not None:
                    classifications.append(package)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "technology_pipeline_unexpected_error full_name=%s repository_id=%s",
                candidate.full_name,
                candidate.repository_id,
            )
            return TechnologyFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
                manifests_processed=manifests_processed,
                dependencies_parsed=dependencies_parsed,
                parse_errors=parse_errors,
            )

        try:
            technology_ids = self._writer.upsert_technologies(candidate.repository_id, classifications)
        except TechnologyPersistenceError as exc:
            return TechnologyFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="persist",
                error_type=exc.__class__.__name__,
                message=str(exc),
                manifests_processed=manifests_processed,
                dependencies_parsed=dependencies_parsed,
                parse_errors=parse_errors,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            log.exception(
                "technology_pipeline_unexpected_error full_name=%s repository_id=%s",
                candidate.full_name,
                candidate.repository_id,
            )
            return TechnologyFailure(
                repository_id=candidate.repository_id,
                full_name=candidate.full_name,
                stage="unexpected",
                error_type=exc.__class__.__name__,
                message=str(exc),
                manifests_processed=manifests_processed,
                dependencies_parsed=dependencies_parsed,
                parse_errors=parse_errors,
            )

        return ProcessedRepository(
            repository_id=candidate.repository_id,
            full_name=candidate.full_name,
            manifests_processed=manifests_processed,
            dependencies_parsed=dependencies_parsed,
            parse_errors=parse_errors,
            technologies_persisted=len(technology_ids),
        )
