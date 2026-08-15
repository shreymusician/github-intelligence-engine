"""Test suite for processing.documentation_storage — Checkpoint 2.3.d.

Deterministic, mocked tests exercising DocumentationMetricsWriter against a
fake psycopg Connection/Cursor/Transaction double modeling
`repository_metrics` (keyed on (repository_id, snapshot_date)) - no real
network, no live PostgreSQL dependency for normal test execution. Mirrors
the fake-registry style established in tests/test_technology_storage.py
(Checkpoint 2.2.b).

The fake registry deliberately models a handful of *unrelated*
`repository_metrics` columns (e.g. `commits_last_3mo`) in addition to the
five documentation columns, specifically to verify the partial-row upsert
never touches columns outside its own five — a correctness property that
did not exist in 2.2.b's writer (which owns its entire target rows) and is
genuinely new to this checkpoint.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date

import psycopg
import pytest

from processing.documentation_storage import DocumentationMetricsWriter
from processing.exceptions import DocumentationPersistenceError

# ---------------------------------------------------------------------------
# Fixtures — a fake psycopg Connection/Cursor/Transaction modeling
# repository_metrics' real partial-row upsert semantics.
# ---------------------------------------------------------------------------


class FakeMetricsRow:
    def __init__(self) -> None:
        self.readme_present: bool | None = None
        self.readme_word_count: int | None = None
        self.readme_section_count: int | None = None
        self.readme_code_example_count: int | None = None
        self.readme_readability_score: float | None = None
        # unrelated columns another Milestone-2 checkpoint would populate -
        # present here purely to verify this writer never touches them.
        self.commits_last_3mo: int | None = None


class FakeMetricsRegistry:
    """Simulates `repository_metrics`, keyed on
    (repository_id, snapshot_date), modeling the exact partial-column
    ON CONFLICT DO UPDATE semantics documentation_storage.py relies on."""

    def __init__(self) -> None:
        self._rows: dict[tuple, FakeMetricsRow] = {}

    def upsert_documentation(self, params: dict) -> None:
        key = (params["repository_id"], params["snapshot_date"])
        row = self._rows.get(key)
        if row is None:
            row = FakeMetricsRow()
            self._rows[key] = row
        row.readme_present = params["readme_present"]
        row.readme_word_count = params["readme_word_count"]
        row.readme_section_count = params["readme_section_count"]
        row.readme_code_example_count = params["readme_code_example_count"]
        row.readme_readability_score = params["readme_readability_score"]
        # commits_last_3mo intentionally never assigned here

    def get(self, repository_id, snapshot_date) -> FakeMetricsRow | None:
        return self._rows.get((repository_id, snapshot_date))

    def row_count(self, repository_id=None) -> int:
        if repository_id is None:
            return len(self._rows)
        return sum(1 for (rid, _) in self._rows if rid == repository_id)


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self._conn.operations.append(("execute", sql, params))
        if self._conn.fail_on and self._conn.fail_on in sql:
            raise psycopg.Error(f"simulated failure: {self._conn.fail_on}")
        if re.search(r"INSERT INTO repository_metrics", sql):
            self._conn.registry.upsert_documentation(params)


class FakeTransaction:
    """Mimics psycopg3's Connection.transaction(): commits on clean exit,
    rolls back and re-raises on any exception, restoring the fake
    registry's in-memory state to its pre-transaction snapshot."""

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
        return False


class FakeConnection:
    def __init__(self, registry: FakeMetricsRegistry | None = None, fail_on: str | None = None) -> None:
        self.registry = registry or FakeMetricsRegistry()
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


SNAPSHOT_DATE = date(2026, 8, 15)


def upsert(writer, repo_id, **overrides):
    defaults = dict(
        readme_present=True,
        readme_word_count=120,
        readme_section_count=3,
        readme_code_example_count=2,
        readme_readability_score=65.5,
    )
    defaults.update(overrides)
    writer.upsert_documentation_metrics(repo_id, SNAPSHOT_DATE, **defaults)


# ---------------------------------------------------------------------------
# Basic persistence
# ---------------------------------------------------------------------------


class TestBasicPersistence:
    def test_first_write_creates_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_id)

        row = conn.registry.get(repo_id, SNAPSHOT_DATE)
        assert row is not None
        assert row.readme_present is True
        assert row.readme_word_count == 120
        assert row.readme_section_count == 3
        assert row.readme_code_example_count == 2
        assert row.readme_readability_score == 65.5
        assert conn.committed is True

    def test_null_readability_score_persisted_as_none(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_id, readme_readability_score=None)

        assert conn.registry.get(repo_id, SNAPSHOT_DATE).readme_readability_score is None

    def test_readme_present_false_with_zero_counts(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(
            writer,
            repo_id,
            readme_present=False,
            readme_word_count=None,
            readme_section_count=None,
            readme_code_example_count=None,
            readme_readability_score=None,
        )

        row = conn.registry.get(repo_id, SNAPSHOT_DATE)
        assert row.readme_present is False
        assert row.readme_word_count is None

    def test_row_count_is_one_per_repository_per_snapshot(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_id)

        assert conn.registry.row_count() == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_rerun_same_snapshot_updates_not_duplicates(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_id, readme_word_count=100)
        upsert(writer, repo_id, readme_word_count=150)

        assert conn.registry.row_count(repo_id) == 1
        assert conn.registry.get(repo_id, SNAPSHOT_DATE).readme_word_count == 150

    def test_no_duplicate_rows_across_multiple_repositories(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_a)
        upsert(writer, repo_b)
        upsert(writer, repo_a)  # rerun

        assert conn.registry.row_count() == 2

    def test_unrelated_metric_fields_preserved_across_rerun(self):
        # simulates a repository_metrics row that already has an activity
        # feature (a different Milestone-2 checkpoint's column) populated
        # before 2.3 ever writes to it - the partial-row upsert must not
        # clobber it.
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        conn.registry.upsert_documentation(
            {
                "repository_id": repo_id,
                "snapshot_date": SNAPSHOT_DATE,
                "readme_present": None,
                "readme_word_count": None,
                "readme_section_count": None,
                "readme_code_example_count": None,
                "readme_readability_score": None,
            }
        )
        conn.registry.get(repo_id, SNAPSHOT_DATE).commits_last_3mo = 42

        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)
        upsert(writer, repo_id)

        row = conn.registry.get(repo_id, SNAPSHOT_DATE)
        assert row.commits_last_3mo == 42  # untouched by this writer
        assert row.readme_present is True  # documentation fields still updated


# ---------------------------------------------------------------------------
# Failure / rollback
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_rollback_on_failure_leaves_no_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO repository_metrics")
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        with pytest.raises(DocumentationPersistenceError):
            upsert(writer, repo_id)

        assert conn.registry.row_count() == 0
        assert conn.rolled_back is True
        assert conn.committed is False

    def test_persistence_error_wraps_original_cause(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO repository_metrics")
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        with pytest.raises(DocumentationPersistenceError) as exc_info:
            upsert(writer, repo_id)

        assert exc_info.value.repository_id == repo_id
        assert isinstance(exc_info.value.cause, psycopg.Error)

    def test_rollback_does_not_clobber_preexisting_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)
        upsert(writer, repo_id, readme_word_count=100)

        conn.fail_on = "INSERT INTO repository_metrics"
        with pytest.raises(DocumentationPersistenceError):
            upsert(writer, repo_id, readme_word_count=999)

        assert conn.registry.get(repo_id, SNAPSHOT_DATE).readme_word_count == 100

    def test_connection_always_closed(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO repository_metrics")
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        with pytest.raises(DocumentationPersistenceError):
            upsert(writer, repo_id)

        assert conn.closed is True

    def test_connection_closed_on_success_too(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = DocumentationMetricsWriter(connection_factory=lambda: conn)

        upsert(writer, repo_id)

        assert conn.closed is True
