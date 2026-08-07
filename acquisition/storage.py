"""Repository storage layer — Checkpoint 1.3.b.

Persists a single github_client.rest.RepositoryData into PostgreSQL,
across the tables Data Acquisition owns per DATABASE_DESIGN.md Sec5:
owners, licenses, topics, repositories, repository_topics, and
repository_snapshot. Fetching (Checkpoint 1.1), selecting candidates
(1.3.a), and tying selection -> fetch -> storage into one run (1.3.c)
are explicitly not this module's job - RepositoryWriter never imports
github_client.rest.GitHubRESTClient/GitHubGraphQLClient and never makes
an HTTP request.

FK insert/upsert order (required, since repositories.owner_id and
.license_id are NOT NULL / nullable FKs respectively, and
repository_topics/repository_snapshot both FK into repositories.id):
    owners -> licenses -> topics -> repositories -> repository_topics
    -> repository_snapshot
Licenses and topics have no FK relationship to owners or to each other,
so their relative order versus each other (but not versus repositories,
which depends on both) is arbitrary; they are upserted before
repositories here simply because repositories needs their generated ids.

Transaction boundary: one PostgreSQL transaction per upsert_repository()
call, covering all six write steps. psycopg3's `Connection.transaction()`
context manager commits on clean exit and rolls back on any exception
raised inside it - used here rather than manual BEGIN/COMMIT/ROLLBACK so
the rollback path cannot be accidentally skipped by a future edit.

Idempotency: every write in this module is an upsert (ON CONFLICT ...
DO UPDATE), keyed on the same natural/unique key GitHub's response is
already keyed on (github_id for owners/repositories, spdx_id for
licenses, slug for topics, (repository_id, snapshot_date) for the daily
snapshot). Re-running upsert_repository() with the same RepositoryData
on the same day produces the same rows, not duplicates.

Design decision (Phase 1 ambiguity, resolved before coding): the
checkpoint's requested method list names five per-table steps
(upsert_owner, upsert_license, upsert_topics, upsert_repository,
insert_repository_snapshot) AND a public orchestrating method with the
same repositories-table name, upsert_repository(data). Naming the
per-table repositories-row step identically to the public entry point
would either collide (same name, different signature, on the same
class) or require ad hoc overloading. Resolved by implementing the five
per-table steps as private module-level helper functions (operating on
an open psycopg connection, not the class), each doing exactly the one
table's upsert described in the checkpoint spec, and exposing exactly
one public method on RepositoryWriter - upsert_repository(data) - that
orchestrates them in FK order. This keeps every named responsibility
from the spec while avoiding the name collision.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Callable

import psycopg

from acquisition.exceptions import RepositoryPersistenceError
from config.settings import Settings, get_settings
from github_client.rest import OwnerSummary, RepositoryData
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


def _upsert_owner(conn: psycopg.Connection, owner: OwnerSummary) -> uuid.UUID:
    """Upsert one row into `owners`, keyed on github_id. Returns owners.id.

    GitHub's `type` field ("User"/"Organization") is upper-cased;
    owners.account_type is the lower-cased enum('user','organization')
    per Migration 002 - translated here, at the storage boundary, so
    OwnerSummary itself stays a faithful, unmodified mirror of the API
    response (github_client's own documented convention).
    """
    account_type = owner.account_type.lower()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO owners (github_id, login, account_type, avatar_url)
            VALUES (%(github_id)s, %(login)s, %(account_type)s, %(avatar_url)s)
            ON CONFLICT (github_id) DO UPDATE SET
                login = EXCLUDED.login,
                account_type = EXCLUDED.account_type,
                avatar_url = EXCLUDED.avatar_url,
                updated_at = now()
            RETURNING id
            """,
            {
                "github_id": owner.github_id,
                "login": owner.login,
                "account_type": account_type,
                "avatar_url": owner.avatar_url,
            },
        )
        return cur.fetchone()[0]


def _upsert_license(
    conn: psycopg.Connection, spdx_id: str | None, name: str | None
) -> uuid.UUID | None:
    """Upsert one row into `licenses`, keyed on spdx_id. Returns licenses.id,
    or None when the repository has no license (a valid, meaningful state
    per DATABASE_DESIGN.md Sec2.3 - not an error).

    is_osi_approved is never set here: GitHub's embedded repository
    license object does not carry OSI-approval data, so this column is
    left to its schema default (false) on insert and untouched on
    conflict - not fabricated from data we don't have.
    """
    if spdx_id is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO licenses (spdx_id, name)
            VALUES (%(spdx_id)s, %(name)s)
            ON CONFLICT (spdx_id) DO UPDATE SET
                name = EXCLUDED.name
            RETURNING id
            """,
            {"spdx_id": spdx_id, "name": name or spdx_id},
        )
        return cur.fetchone()[0]


def _upsert_topics(conn: psycopg.Connection, topics: tuple[str, ...]) -> list[uuid.UUID]:
    """Upsert one row per topic into `topics`, keyed on slug. Returns
    topics.id for each, in the same order as `topics`.

    GitHub's repository topics are already slug-form strings (e.g.
    "machine-learning") with no separate display name in the API
    response used by this project (Checkpoint 1.1's single-endpoint
    scope) - slug and name are therefore the same string here.
    """
    topic_ids: list[uuid.UUID] = []
    with conn.cursor() as cur:
        for slug in topics:
            cur.execute(
                """
                INSERT INTO topics (slug, name)
                VALUES (%(slug)s, %(slug)s)
                ON CONFLICT (slug) DO UPDATE SET
                    slug = EXCLUDED.slug
                RETURNING id
                """,
                {"slug": slug},
            )
            topic_ids.append(cur.fetchone()[0])
    return topic_ids


def _upsert_repository(
    conn: psycopg.Connection,
    data: RepositoryData,
    owner_id: uuid.UUID,
    license_id: uuid.UUID | None,
) -> uuid.UUID:
    """Upsert one row into `repositories`, keyed on github_id. Returns
    repositories.id.

    is_accessible is unconditionally set true and inaccessible_since
    cleared: reaching this function at all means RepositoryData was just
    successfully fetched from GitHub (Checkpoint 1.1), so the repository
    is, by definition, currently accessible. last_extraction_status is
    set to 'success' for the same reason - this module is only ever
    invoked with data from a successful fetch; recording 'pending'/
    'failed' extraction outcomes is the acquisition orchestration
    pipeline's responsibility (Checkpoint 1.3.c, not yet implemented).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repositories (
                github_id, owner_id, name, full_name, description,
                primary_language, license_id, is_archived, is_fork,
                is_accessible, inaccessible_since, github_created_at,
                pushed_at, fetched_at, last_extraction_status
            )
            VALUES (
                %(github_id)s, %(owner_id)s, %(name)s, %(full_name)s, %(description)s,
                %(primary_language)s, %(license_id)s, %(is_archived)s, %(is_fork)s,
                true, NULL, %(github_created_at)s,
                %(pushed_at)s, now(), 'success'
            )
            ON CONFLICT (github_id) DO UPDATE SET
                owner_id = EXCLUDED.owner_id,
                name = EXCLUDED.name,
                full_name = EXCLUDED.full_name,
                description = EXCLUDED.description,
                primary_language = EXCLUDED.primary_language,
                license_id = EXCLUDED.license_id,
                is_archived = EXCLUDED.is_archived,
                is_fork = EXCLUDED.is_fork,
                is_accessible = true,
                inaccessible_since = NULL,
                github_created_at = EXCLUDED.github_created_at,
                pushed_at = EXCLUDED.pushed_at,
                fetched_at = now(),
                last_extraction_status = 'success',
                updated_at = now()
            RETURNING id
            """,
            {
                "github_id": data.github_id,
                "owner_id": owner_id,
                "name": data.name,
                "full_name": data.full_name,
                "description": data.description,
                "primary_language": data.primary_language,
                "license_id": license_id,
                "is_archived": data.is_archived,
                "is_fork": data.is_fork,
                "github_created_at": data.github_created_at,
                "pushed_at": data.pushed_at,
            },
        )
        return cur.fetchone()[0]


def _link_repository_topics(
    conn: psycopg.Connection, repository_id: uuid.UUID, topic_ids: list[uuid.UUID]
) -> None:
    """Insert repository_topics rows for the given repository/topic pairs.

    Additive only, by design: does not delete a repository_topics row for
    a topic previously associated but no longer present in `data.topics`.
    No roadmap query (QUERY_DRIVEN_SCHEMA_DESIGN.md Category A query 4)
    requires topic *removal* tracking, unlike repository_technologies's
    documented removed_at (a different table, a different, explicit
    requirement). Logged as a known limitation in the checkpoint report,
    not silently ignored.
    """
    if not topic_ids:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO repository_topics (repository_id, topic_id)
            VALUES (%(repository_id)s, %(topic_id)s)
            ON CONFLICT (repository_id, topic_id) DO NOTHING
            """,
            [{"repository_id": repository_id, "topic_id": topic_id} for topic_id in topic_ids],
        )


def _insert_repository_snapshot(
    conn: psycopg.Connection, repository_id: uuid.UUID, data: RepositoryData
) -> None:
    """Upsert one row into `repository_snapshot` for today's date, keyed on
    (repository_id, snapshot_date) - the table's declared unique
    constraint (Migration 005). Upserting (not plain INSERT) is what
    makes a same-day re-run of upsert_repository() idempotent instead of
    raising a unique-violation on the second call.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO repository_snapshot (
                repository_id, snapshot_date, stars_count, forks_count,
                watchers_count, open_issues_count, size_kb
            )
            VALUES (
                %(repository_id)s, %(snapshot_date)s, %(stars_count)s, %(forks_count)s,
                %(watchers_count)s, %(open_issues_count)s, %(size_kb)s
            )
            ON CONFLICT (repository_id, snapshot_date) DO UPDATE SET
                stars_count = EXCLUDED.stars_count,
                forks_count = EXCLUDED.forks_count,
                watchers_count = EXCLUDED.watchers_count,
                open_issues_count = EXCLUDED.open_issues_count,
                size_kb = EXCLUDED.size_kb
            """,
            {
                "repository_id": repository_id,
                "snapshot_date": datetime.date.today(),
                "stars_count": data.stars_count,
                "forks_count": data.forks_count,
                "watchers_count": data.watchers_count,
                "open_issues_count": data.open_issues_count,
                "size_kb": data.size_kb,
            },
        )


class RepositoryWriter:
    """Persists RepositoryData into PostgreSQL, one repository per call.

    Holds no long-lived database connection - each upsert_repository()
    call opens, uses, and closes its own connection, matching
    acquisition/selection.py's RepositorySelector precedent of holding no
    state beyond what it was constructed with. A connection_factory can
    be injected for testing (a fake/mock connection) without touching a
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

    def upsert_repository(self, data: RepositoryData) -> uuid.UUID:
        """Persist one RepositoryData across owners/licenses/topics/
        repositories/repository_topics/repository_snapshot, in one
        transaction. Returns the repository's repositories.id.

        On any failure, the transaction is rolled back (no partial
        writes across any of the six tables survive) and
        RepositoryPersistenceError is raised, wrapping the underlying
        psycopg error.
        """
        conn = self._connection_factory()
        try:
            with conn.transaction():
                owner_id = _upsert_owner(conn, data.owner)
                license_id = _upsert_license(conn, data.license_spdx_id, data.license_name)
                topic_ids = _upsert_topics(conn, data.topics)
                repository_id = _upsert_repository(conn, data, owner_id, license_id)
                _link_repository_topics(conn, repository_id, topic_ids)
                _insert_repository_snapshot(conn, repository_id, data)
        except psycopg.Error as exc:
            log.error(
                "repository_persist_failed full_name=%s error=%s",
                data.full_name,
                exc.__class__.__name__,
            )
            raise RepositoryPersistenceError(data.full_name, exc) from exc
        else:
            log.info(
                "repository_persisted full_name=%s repository_id=%s topics=%s",
                data.full_name,
                repository_id,
                len(topic_ids),
            )
            return repository_id
        finally:
            conn.close()
