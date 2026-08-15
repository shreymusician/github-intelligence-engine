"""Documentation metrics storage layer — Checkpoint 2.3.d.

Persists the structural (2.3.a) and readability (2.3.b) features already
computed for one repository's README into the existing `repository_metrics`
table (Migration 006) — no migration, no new table, reusing exactly the
five columns already allocated for documentation features:
`readme_present`, `readme_word_count`, `readme_section_count`,
`readme_code_example_count`, `readme_readability_score`.

This module owns none of the parsing (processing/readme_parser.py) or
readability (processing/readability.py) logic, and none of the batch
orchestration lifecycle (processing/documentation_pipeline.py). It is
handed a repository_id, a snapshot_date, and the already-computed scalar
values, and persists them — nothing more.

Deliberately does NOT write a documentation "quality score" anywhere —
per the 2.3 design review (Sec12), the frozen schema does not clearly
specify where that value belongs (no dedicated `repository_metrics`
column exists, and `repository_scores` is an architecturally distinct
Milestone-3 intelligence table). That decision is explicitly deferred,
not made here.

Mirrors processing/technology_storage.py's established conventions
directly:
- Same ConnectionFactory / _default_connection_factory(settings) shape.
- Same transaction boundary: one psycopg3 Connection.transaction() per
  upsert_documentation_metrics() call.
- Same typed-exception wrapping: psycopg.Error is caught and re-raised as
  DocumentationPersistenceError, never leaking a raw psycopg exception.

Idempotency semantics: `repository_metrics` is upserted on its existing
UNIQUE(repository_id, snapshot_date) constraint (Migration 006) — one
metrics snapshot per repository per day. The `DO UPDATE SET` clause lists
only the five documentation columns explicitly, by name — this is a
partial-row upsert. It never touches the other 22 typed columns or either
JSONB column on this table (activity/code/dependency features other
Milestone-2 checkpoints will eventually populate), so re-running 2.3
after e.g. 2.1's activity metrics have already been written for the same
(repository_id, snapshot_date) leaves those activity columns exactly as
they were.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Callable

import psycopg

from config.settings import Settings, get_settings
from logging_setup import get_logger
from processing.exceptions import DocumentationPersistenceError

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


class DocumentationMetricsWriter:
    """Persists one repository's documentation features into
    `repository_metrics`, one (repository_id, snapshot_date) row per call.

    Holds no long-lived database connection - each
    upsert_documentation_metrics() call opens, uses, and closes its own
    connection, matching TechnologyWriter's/ContentWriter's precedent
    exactly. A connection_factory can be injected for testing (a fake/mock
    connection) without touching a real database; production code uses
    the default, built from config.settings.get_settings()'s postgres_*
    fields.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _default_connection_factory(
            settings or get_settings()
        )

    def upsert_documentation_metrics(
        self,
        repository_id: uuid.UUID,
        snapshot_date: date,
        *,
        readme_present: bool,
        readme_word_count: int | None,
        readme_section_count: int | None,
        readme_code_example_count: int | None,
        readme_readability_score: float | None,
    ) -> None:
        """Upsert one `repository_metrics` row's five documentation
        columns for `repository_id`/`snapshot_date`, in one transaction.

        On any failure, the transaction is rolled back (no partial write
        survives) and DocumentationPersistenceError is raised, wrapping
        the underlying psycopg error.
        """
        conn = self._connection_factory()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO repository_metrics (
                            repository_id, snapshot_date, readme_present, readme_word_count,
                            readme_section_count, readme_code_example_count, readme_readability_score
                        )
                        VALUES (
                            %(repository_id)s, %(snapshot_date)s, %(readme_present)s, %(readme_word_count)s,
                            %(readme_section_count)s, %(readme_code_example_count)s, %(readme_readability_score)s
                        )
                        ON CONFLICT (repository_id, snapshot_date) DO UPDATE SET
                            readme_present = EXCLUDED.readme_present,
                            readme_word_count = EXCLUDED.readme_word_count,
                            readme_section_count = EXCLUDED.readme_section_count,
                            readme_code_example_count = EXCLUDED.readme_code_example_count,
                            readme_readability_score = EXCLUDED.readme_readability_score
                        """,
                        {
                            "repository_id": repository_id,
                            "snapshot_date": snapshot_date,
                            "readme_present": readme_present,
                            "readme_word_count": readme_word_count,
                            "readme_section_count": readme_section_count,
                            "readme_code_example_count": readme_code_example_count,
                            "readme_readability_score": readme_readability_score,
                        },
                    )
        except psycopg.Error as exc:
            log.error(
                "documentation_metrics_persist_failed repository_id=%s snapshot_date=%s error=%s",
                repository_id,
                snapshot_date,
                exc.__class__.__name__,
            )
            raise DocumentationPersistenceError(repository_id, exc) from exc
        else:
            log.info(
                "documentation_metrics_persisted repository_id=%s snapshot_date=%s",
                repository_id,
                snapshot_date,
            )
        finally:
            conn.close()
