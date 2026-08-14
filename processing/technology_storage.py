"""Technology association storage layer — Checkpoint 2.2.b.

Persists a batch of processing.technology_taxonomy.Classification
objects (2.2.b's classifier output) into PostgreSQL's `technologies`
and `repository_technologies` tables (Migration 001/003), for one
repository at a time.

This module owns none of the classification logic (processing/technology_taxonomy.py)
and none of the manifest-parsing/orchestration lifecycle. It is handed a
repository_id (an existing repositories.id) and a list of already-produced
Classification objects, and persists them - nothing more.

Mirrors acquisition/content_storage.py's established conventions directly:
- Same ConnectionFactory / _default_connection_factory(settings) shape.
- Same transaction boundary: one psycopg3 Connection.transaction() per
  upsert_technologies() call, covering every classification in the batch.
- Same typed-exception wrapping: psycopg.Error is caught and re-raised
  as TechnologyPersistenceError, never leaking a raw psycopg exception.

Idempotency semantics (approved in the 2.2.b design-review phase):
- `technologies` is upserted on the existing UNIQUE(slug) constraint -
  `category` is never changed on conflict (a slug's category is fixed by
  the curated taxonomy, not something that should drift underneath an
  existing row); only `name` is refreshed.
- `repository_technologies` is upserted on the existing PK
  (repository_id, technology_id) - `first_detected_at` is never touched
  on conflict (its `server_default now()` fires only on INSERT, so it is
  preserved automatically), `last_confirmed_at` always advances to
  now(), and `removed_at` is always cleared to NULL, since this module
  only ever records "this technology was observed just now" - it never
  marks a technology removed. Stale-technology reconciliation (marking
  `removed_at` for a technology no longer observed) requires knowing the
  complete set of repositories actually reprocessed in a run, which is
  orchestration-level context this module does not have - explicitly
  deferred to a future checkpoint, not implemented here.
"""

from __future__ import annotations

import uuid
from typing import Callable

import psycopg

from config.settings import Settings, get_settings
from logging_setup import get_logger
from processing.exceptions import TechnologyPersistenceError
from processing.technology_taxonomy import Classification

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


def _upsert_technology(conn: psycopg.Connection, classification: Classification) -> uuid.UUID:
    """Upsert one row into `technologies`, keyed on slug. Returns
    technologies.id whether the row was just inserted or already
    existed."""
    tech = classification.technology
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO technologies (slug, name, category)
            VALUES (%(slug)s, %(name)s, %(category)s)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name
            RETURNING id
            """,
            {"slug": tech.slug, "name": tech.name, "category": tech.category},
        )
        return cur.fetchone()[0]


def _upsert_repository_technology(
    conn: psycopg.Connection,
    repository_id: uuid.UUID,
    technology_id: uuid.UUID,
    classification: Classification,
) -> None:
    """Upsert one row into `repository_technologies`, keyed on
    (repository_id, technology_id). `detected_via` is always
    `'manifest'` - dockerfile/ci_config/linguist detection do not exist
    in this checkpoint."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repository_technologies (
                repository_id, technology_id, role, detected_via, confidence
            )
            VALUES (
                %(repository_id)s, %(technology_id)s, %(role)s, 'manifest', %(confidence)s
            )
            ON CONFLICT (repository_id, technology_id) DO UPDATE SET
                role = EXCLUDED.role,
                confidence = EXCLUDED.confidence,
                last_confirmed_at = now(),
                removed_at = NULL
            """,
            {
                "repository_id": repository_id,
                "technology_id": technology_id,
                "role": classification.role,
                "confidence": classification.confidence,
            },
        )


class TechnologyWriter:
    """Persists Classification batches into `technologies` +
    `repository_technologies`, one repository's batch per call.

    Holds no long-lived database connection - each upsert_technologies()
    call opens, uses, and closes its own connection, matching
    ContentWriter's precedent exactly. A connection_factory can be
    injected for testing (a fake/mock connection) without touching a
    real database; production code uses the default, built from
    config.settings.get_settings()'s postgres_* fields.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._connection_factory = connection_factory or _default_connection_factory(
            settings or get_settings()
        )

    def upsert_technologies(
        self, repository_id: uuid.UUID, classifications: list[Classification]
    ) -> list[uuid.UUID]:
        """Persist every Classification in `classifications` for
        `repository_id`, in one transaction. Returns the technology_id
        for each classification, in the same order as `classifications`.

        On any failure, the transaction is rolled back (no partial
        writes for this repository survive) and TechnologyPersistenceError
        is raised, wrapping the underlying psycopg error. An empty
        `classifications` list is a no-op - opens no connection, returns
        an empty list.
        """
        if not classifications:
            return []

        conn = self._connection_factory()
        try:
            technology_ids: list[uuid.UUID] = []
            with conn.transaction():
                for classification in classifications:
                    technology_id = _upsert_technology(conn, classification)
                    _upsert_repository_technology(conn, repository_id, technology_id, classification)
                    technology_ids.append(technology_id)
        except psycopg.Error as exc:
            log.error(
                "technology_persist_failed repository_id=%s classifications=%s error=%s",
                repository_id,
                len(classifications),
                exc.__class__.__name__,
            )
            raise TechnologyPersistenceError(repository_id, exc) from exc
        else:
            log.info(
                "technologies_persisted repository_id=%s technologies=%s",
                repository_id,
                len(technology_ids),
            )
            return technology_ids
        finally:
            conn.close()
