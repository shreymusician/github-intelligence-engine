"""Repository content storage layer — Checkpoint 1.4.c.

Persists a batch of acquisition.content_extractor.ExtractedFile objects
(1.4.b's output) into PostgreSQL's repository_content table (Migration
010), keyed by the (repository_id, file_path) unique constraint that
table's approved Checkpoint 1.4 design specifies.

This module owns none of the upstream lifecycle: it never clones a
repository, never invokes git, never discovers or reads files from disk,
and never orchestrates which repositories to process. It is handed a
repository_id (already a real repositories.id, produced by
acquisition.storage.RepositoryWriter in Checkpoint 1.3) and a list of
already-extracted ExtractedFile objects, and persists them — nothing
more. repository_id is deliberately a parameter of upsert_content(), not
a field on ExtractedFile: ExtractedFile belongs to the extraction layer
(1.4.b), which has no reason to know about repository identity at all;
repository_id belongs to this persistence boundary.

Mirrors acquisition/storage.py's established conventions directly:
- Same ConnectionFactory / _default_connection_factory(settings) shape,
  built from config.settings.get_settings()'s postgres_* fields.
- Same transaction boundary: one psycopg3 Connection.transaction() per
  upsert_content() call, covering every row in the batch — commits on
  clean exit, rolls back and re-raises on any exception, so no partial
  writes across a multi-file batch survive a failure partway through.
- Same idempotency mechanism: ON CONFLICT ... DO UPDATE, keyed on the
  table's declared unique constraint (repository_id, file_path) — a
  same-batch re-run does not create duplicate rows.
- Same typed-exception wrapping: psycopg.Error is caught and re-raised
  as ContentPersistenceError (acquisition/exceptions.py), never leaking
  a raw psycopg exception to callers, exactly as RepositoryPersistenceError
  does for upsert_repository().

Idempotency semantics (the specific behavior this module must get right,
approved in the Checkpoint 1.4.c design-verification phase):
- Same file_path + same content_hash: fetched_at is refreshed to now(),
  updated_at is left untouched, and no second row is created.
- Same file_path + different content_hash: content, content_hash,
  content_size_bytes, and fetched_at are all refreshed, and updated_at
  is bumped to now() — content_type is refreshed unconditionally too
  (EXCLUDED.content_type), per the approved upsert semantics.
- A different file_path for the same repository_id is always a separate
  row (the unique constraint is a composite of both columns).

This is expressed as a single upsert statement per file, using a CASE
... IS DISTINCT FROM ... expression against the existing row's stored
content_hash to decide whether updated_at moves — not a read-then-write
of the existing row, which would introduce a race and a second round
trip for no benefit. No CREATE TRIGGER / auto-touch mechanism exists
anywhere in this schema for updated_at (confirmed by inspection of every
prior migration and of acquisition/storage.py, which sets updated_at
explicitly in application SQL) - this module follows that same explicit,
in-statement convention rather than introducing a new mechanism.
"""

from __future__ import annotations

import uuid
from typing import Callable

import psycopg

from acquisition.content_extractor import ExtractedFile
from acquisition.exceptions import ContentPersistenceError
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


def _upsert_content_row(
    conn: psycopg.Connection, repository_id: uuid.UUID, file: ExtractedFile
) -> uuid.UUID:
    """Upsert one row into `repository_content`, keyed on
    (repository_id, file_path). Returns repository_content.id.

    updated_at only moves when the incoming content_hash differs from
    the row's currently-stored content_hash (IS DISTINCT FROM correctly
    treats "no existing row" — the INSERT branch — as a change, and is
    NULL-safe for the conflict-branch comparison itself).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repository_content (
                repository_id, content_type, file_path, content,
                content_hash, content_size_bytes
            )
            VALUES (
                %(repository_id)s, %(content_type)s, %(file_path)s, %(content)s,
                %(content_hash)s, %(content_size_bytes)s
            )
            ON CONFLICT (repository_id, file_path) DO UPDATE SET
                content_type = EXCLUDED.content_type,
                content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash,
                content_size_bytes = EXCLUDED.content_size_bytes,
                fetched_at = now(),
                updated_at = CASE
                    WHEN repository_content.content_hash IS DISTINCT FROM EXCLUDED.content_hash
                    THEN now()
                    ELSE repository_content.updated_at
                END
            RETURNING id
            """,
            {
                "repository_id": repository_id,
                "content_type": file.content_type,
                "file_path": file.file_path,
                "content": file.content,
                "content_hash": file.content_hash,
                "content_size_bytes": file.content_size_bytes,
            },
        )
        return cur.fetchone()[0]


class ContentWriter:
    """Persists ExtractedFile batches into PostgreSQL's repository_content
    table, one repository's batch per call.

    Holds no long-lived database connection — each upsert_content() call
    opens, uses, and closes its own connection, matching
    acquisition/storage.py's RepositoryWriter precedent exactly. A
    connection_factory can be injected for testing (a fake/mock
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

    def upsert_content(
        self, repository_id: uuid.UUID, files: list[ExtractedFile]
    ) -> list[uuid.UUID]:
        """Persist every ExtractedFile in `files` for `repository_id`, in one
        transaction. Returns repository_content.id for each file, in the
        same order as `files`.

        On any failure, the transaction is rolled back (no partial writes
        across the batch survive) and ContentPersistenceError is raised,
        wrapping the underlying psycopg error. An empty `files` list is a
        no-op — opens no connection, returns an empty list.
        """
        if not files:
            return []

        conn = self._connection_factory()
        try:
            content_ids: list[uuid.UUID] = []
            with conn.transaction():
                for file in files:
                    content_ids.append(_upsert_content_row(conn, repository_id, file))
        except psycopg.Error as exc:
            log.error(
                "content_persist_failed repository_id=%s files=%s error=%s",
                repository_id,
                len(files),
                exc.__class__.__name__,
            )
            raise ContentPersistenceError(repository_id, exc) from exc
        else:
            log.info(
                "content_persisted repository_id=%s files=%s",
                repository_id,
                len(content_ids),
            )
            return content_ids
        finally:
            conn.close()
