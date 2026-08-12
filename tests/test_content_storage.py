"""Test suite for acquisition.content_storage — Checkpoint 1.4.c.

Deterministic, mocked tests exercising ContentWriter against a fake
psycopg Connection/Cursor/Transaction double — no real network, no live
PostgreSQL dependency for normal test execution. Mirrors the style
established in tests/test_storage.py (Checkpoint 1.3.d), extended with a
fake registry that models repository_content's actual idempotency rule:
ON CONFLICT (repository_id, file_path) DO UPDATE, with updated_at only
advancing when the incoming content_hash differs from what's already
stored (a CASE ... IS DISTINCT FROM ... expression in the real SQL) —
this is new fake-double logic scoped to this file only, since no prior
table in this schema has conditional-touch semantics for updated_at.

Live PostgreSQL verification of this same SQL is documented separately
in CHECKPOINT_1_4C_REPORT.md; this suite's job is to protect that
already-verified behaviour from regressing, deterministically, in CI.
"""

from __future__ import annotations

import copy
import re
import uuid

import psycopg
import pytest

from acquisition.content_extractor import ExtractedFile
from acquisition.content_storage import ContentWriter
from acquisition.exceptions import ContentAcquisitionError, ContentPersistenceError

# ---------------------------------------------------------------------------
# Fixtures — a fake psycopg Connection/Cursor/Transaction modeling
# repository_content's real upsert + conditional-touch semantics.
# ---------------------------------------------------------------------------


class FakeContentRow:
    def __init__(self, row_id, content_type, file_path, content, content_hash, content_size_bytes):
        self.id = row_id
        self.content_type = content_type
        self.file_path = file_path
        self.content = content
        self.content_hash = content_hash
        self.content_size_bytes = content_size_bytes
        self.fetched_at_ticks = 0
        self.updated_at_ticks = 0


class FakeContentRegistry:
    """Simulates the repository_content table's storage state: keyed on
    (repository_id, file_path), models the exact ON CONFLICT DO UPDATE
    semantics content_storage.py's upsert relies on — including that
    updated_at only advances when content_hash actually changes.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple, FakeContentRow] = {}
        self._clock = 0

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def upsert(self, repository_id, params: dict) -> uuid.UUID:
        key = (repository_id, params["file_path"])
        now = self._tick()
        existing = self._rows.get(key)
        if existing is None:
            row = FakeContentRow(
                row_id=uuid.uuid4(),
                content_type=params["content_type"],
                file_path=params["file_path"],
                content=params["content"],
                content_hash=params["content_hash"],
                content_size_bytes=params["content_size_bytes"],
            )
            row.fetched_at_ticks = now
            row.updated_at_ticks = now
            self._rows[key] = row
            return row.id

        existing.content_type = params["content_type"]
        existing.content = params["content"]
        content_changed = existing.content_hash != params["content_hash"]
        existing.content_hash = params["content_hash"]
        existing.content_size_bytes = params["content_size_bytes"]
        existing.fetched_at_ticks = now
        if content_changed:
            existing.updated_at_ticks = now
        return existing.id

    def get(self, repository_id, file_path) -> FakeContentRow | None:
        return self._rows.get((repository_id, file_path))

    def row_count(self, repository_id=None) -> int:
        if repository_id is None:
            return len(self._rows)
        return sum(1 for (rid, _) in self._rows if rid == repository_id)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self._last_id: uuid.UUID | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._conn.operations.append(("execute", sql, params))
        if self._conn.fail_on and self._conn.fail_on in sql:
            raise psycopg.Error(f"simulated failure: {self._conn.fail_on}")
        if re.search(r"INSERT INTO repository_content", sql) and "RETURNING id" in sql:
            self._last_id = self._conn.registry.upsert(self._conn.repository_id, params)
        else:
            self._last_id = None

    def fetchone(self):
        return (self._last_id,)


class FakeTransaction:
    """Mimics psycopg3's Connection.transaction(): commits on clean exit,
    rolls back and re-raises on any exception. Also restores the fake
    registry's in-memory state to its pre-transaction snapshot on
    rollback — real PostgreSQL discards every write made inside a failed
    transaction, and this double must reproduce that for "no partial
    rows survive a rollback" assertions to mean anything.
    """

    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self._snapshot = None

    def __enter__(self) -> "FakeTransaction":
        self._snapshot = copy.deepcopy(self._conn.registry._rows)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.committed = True
        else:
            self._conn.registry._rows = self._snapshot
            self._conn.rolled_back = True
        return False  # never swallow


class FakeConnection:
    """A fake psycopg.Connection. `repository_id` is fixed per-connection
    since the fake registry keys on it explicitly (real SQL passes it as a
    bound parameter each call; this double takes it once at construction
    for simplicity, since every test uses one connection per repository).
    `fail_on`, if set, is a substring of an executed SQL statement that
    should raise psycopg.Error when reached.
    """

    def __init__(self, repository_id, registry: FakeContentRegistry | None = None, fail_on: str | None = None) -> None:
        self.repository_id = repository_id
        self.registry = registry or FakeContentRegistry()
        self.fail_on = fail_on
        self.operations: list[tuple[str, str, object]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def close(self) -> None:
        self.closed = True


def make_file(
    file_path: str = "README.md",
    content_type: str = "readme",
    content: str = "# Hello",
    content_hash: str = "hash-1",
    content_size_bytes: int = 7,
) -> ExtractedFile:
    return ExtractedFile(
        content_type=content_type,
        file_path=file_path,
        content=content,
        content_hash=content_hash,
        content_size_bytes=content_size_bytes,
    )


# ---------------------------------------------------------------------------
# Basic persistence
# ---------------------------------------------------------------------------


class TestBasicPersistence:
    def test_single_content_insert(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        ids = writer.upsert_content(repo_id, [make_file()])

        assert len(ids) == 1
        row = conn.registry.get(repo_id, "README.md")
        assert row.content == "# Hello"
        assert row.content_hash == "hash-1"
        assert row.content_size_bytes == 7
        assert conn.committed is True

    def test_multiple_files_one_repository(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        files = [make_file("README.md", "readme"), make_file("package.json", "package_json", content="{}", content_hash="h2", content_size_bytes=2)]
        ids = writer.upsert_content(repo_id, files)

        assert len(ids) == 2
        assert len(set(ids)) == 2  # distinct rows
        assert conn.registry.row_count(repo_id) == 2

    def test_multiple_repositories_are_independent(self):
        repo_a = uuid.uuid4()
        repo_b = uuid.uuid4()
        registry = FakeContentRegistry()
        writer_a = ContentWriter(connection_factory=lambda: FakeConnection(repo_a, registry))
        writer_b = ContentWriter(connection_factory=lambda: FakeConnection(repo_b, registry))

        writer_a.upsert_content(repo_a, [make_file("README.md")])
        writer_b.upsert_content(repo_b, [make_file("README.md")])

        assert registry.row_count(repo_a) == 1
        assert registry.row_count(repo_b) == 1
        assert registry.row_count() == 2  # same file_path, different repositories -> separate rows

    def test_multiple_content_types(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        files = [
            make_file("README.md", "readme", content_hash="h1"),
            make_file("requirements.txt", "requirements_txt", content="flask\n", content_hash="h2", content_size_bytes=6),
            make_file("pyproject.toml", "pyproject_toml", content="[tool]", content_hash="h3", content_size_bytes=6),
        ]
        writer.upsert_content(repo_id, files)

        assert conn.registry.get(repo_id, "README.md").content_type == "readme"
        assert conn.registry.get(repo_id, "requirements.txt").content_type == "requirements_txt"
        assert conn.registry.get(repo_id, "pyproject.toml").content_type == "pyproject_toml"

    def test_empty_files_list_is_a_noop(self):
        repo_id = uuid.uuid4()
        connection_opened = []

        def factory():
            connection_opened.append(True)
            return FakeConnection(repo_id)

        writer = ContentWriter(connection_factory=factory)
        result = writer.upsert_content(repo_id, [])

        assert result == []
        assert connection_opened == []  # no connection opened for an empty batch

    def test_content_size_bytes_correctness(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file(content_size_bytes=12345)])

        assert conn.registry.get(repo_id, "README.md").content_size_bytes == 12345

    def test_content_hash_persisted(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file(content_hash="deadbeef")])

        assert conn.registry.get(repo_id, "README.md").content_hash == "deadbeef"


# ---------------------------------------------------------------------------
# Idempotency: same hash, changed hash, different file_path
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_file_same_hash_refreshes_fetched_at_preserves_updated_at_no_duplicate(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file(content_hash="same-hash")])
        row_after_first = conn.registry.get(repo_id, "README.md")
        first_updated_at = row_after_first.updated_at_ticks
        first_fetched_at = row_after_first.fetched_at_ticks
        first_id = row_after_first.id

        writer.upsert_content(repo_id, [make_file(content_hash="same-hash")])
        row_after_second = conn.registry.get(repo_id, "README.md")

        assert row_after_second.id == first_id  # no duplicate row
        assert conn.registry.row_count(repo_id) == 1
        assert row_after_second.updated_at_ticks == first_updated_at  # preserved
        assert row_after_second.fetched_at_ticks > first_fetched_at  # refreshed

    def test_same_file_changed_hash_updates_row_and_bumps_updated_at(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file(content="v1", content_hash="hash-v1")])
        first_id = conn.registry.get(repo_id, "README.md").id
        first_updated_at = conn.registry.get(repo_id, "README.md").updated_at_ticks

        writer.upsert_content(repo_id, [make_file(content="v2", content_hash="hash-v2", content_size_bytes=2)])
        row = conn.registry.get(repo_id, "README.md")

        assert row.id == first_id  # same logical row, updated in place
        assert conn.registry.row_count(repo_id) == 1
        assert row.content == "v2"
        assert row.content_hash == "hash-v2"
        assert row.updated_at_ticks > first_updated_at

    def test_different_file_path_creates_separate_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file("README.md")])
        writer.upsert_content(repo_id, [make_file("README.rst", content_hash="other-hash")])

        assert conn.registry.row_count(repo_id) == 2

    def test_reinserting_identical_batch_twice_produces_no_duplicate_rows(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        files = [make_file("README.md", content_hash="h1"), make_file("package.json", "package_json", content="{}", content_hash="h2", content_size_bytes=2)]
        writer.upsert_content(repo_id, files)
        writer.upsert_content(repo_id, files)

        assert conn.registry.row_count(repo_id) == 2


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransactionBehavior:
    def test_transaction_commits_on_success(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file()])

        assert conn.committed is True
        assert conn.rolled_back is False
        assert conn.closed is True

    def test_transaction_rolls_back_on_failure_no_partial_rows(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id, fail_on="INSERT INTO repository_content")
        writer = ContentWriter(connection_factory=lambda: conn)

        with pytest.raises(ContentPersistenceError):
            writer.upsert_content(repo_id, [make_file()])

        assert conn.rolled_back is True
        assert conn.committed is False
        assert conn.registry.row_count(repo_id) == 0  # no partial row survives

    def test_failure_midway_through_batch_leaves_no_partial_rows(self):
        repo_id = uuid.uuid4()

        class FailSecondCursor(FakeCursor):
            _calls = {"n": 0}

            def execute(self, sql, params=None):
                if re.search(r"INSERT INTO repository_content", sql):
                    FailSecondCursor._calls["n"] += 1
                    if FailSecondCursor._calls["n"] == 2:
                        raise psycopg.Error("simulated failure on second file")
                super().execute(sql, params)

        class FailingConnection(FakeConnection):
            def cursor(self):
                return FailSecondCursor(self)

        conn = FailingConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        with pytest.raises(ContentPersistenceError):
            writer.upsert_content(
                repo_id,
                [make_file("README.md", content_hash="h1"), make_file("package.json", "package_json", content_hash="h2")],
            )

        assert conn.rolled_back is True
        assert conn.registry.row_count(repo_id) == 0

    def test_database_error_translated_to_content_persistence_error(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id, fail_on="INSERT INTO repository_content")
        writer = ContentWriter(connection_factory=lambda: conn)

        with pytest.raises(ContentPersistenceError) as exc_info:
            writer.upsert_content(repo_id, [make_file()])

        assert isinstance(exc_info.value, ContentAcquisitionError)
        assert exc_info.value.repository_id == repo_id
        assert isinstance(exc_info.value.cause, psycopg.Error)

    def test_connection_always_closed(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id, fail_on="INSERT INTO repository_content")
        writer = ContentWriter(connection_factory=lambda: conn)

        with pytest.raises(ContentPersistenceError):
            writer.upsert_content(repo_id, [make_file()])

        assert conn.closed is True


# ---------------------------------------------------------------------------
# SQL shape / parameterization
# ---------------------------------------------------------------------------


class TestParameterizedSQL:
    def test_sql_uses_parameter_placeholders_not_string_interpolation(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        malicious = make_file(content="'; DROP TABLE repository_content; --")
        writer.upsert_content(repo_id, [malicious])

        insert_ops = [op for op in conn.operations if "INSERT INTO repository_content" in op[1]]
        assert len(insert_ops) == 1
        _, sql, params = insert_ops[0]
        assert "DROP TABLE" not in sql  # never string-interpolated into the statement
        assert params["content"] == "'; DROP TABLE repository_content; --"  # passed as a bound parameter instead
        assert "%(content)s" in sql

    def test_on_conflict_targets_repository_id_and_file_path(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file()])

        _, sql, _ = conn.operations[0]
        assert "ON CONFLICT (repository_id, file_path)" in sql

    def test_content_type_refreshed_on_conflict(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        writer.upsert_content(repo_id, [make_file()])
        insert_ops = [op for op in conn.operations if "INSERT INTO repository_content" in op[1]]
        _, sql, _ = insert_ops[0]
        assert "content_type = EXCLUDED.content_type" in sql

    def test_repository_id_passed_as_bound_parameter_per_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(repo_id)
        writer = ContentWriter(connection_factory=lambda: conn)

        files = [make_file("README.md", content_hash="h1"), make_file("package.json", "package_json", content_hash="h2")]
        writer.upsert_content(repo_id, files)

        insert_ops = [op for op in conn.operations if "INSERT INTO repository_content" in op[1]]
        assert len(insert_ops) == 2
        for _, _, params in insert_ops:
            assert params["repository_id"] == repo_id  # same FK value on every row in the batch
