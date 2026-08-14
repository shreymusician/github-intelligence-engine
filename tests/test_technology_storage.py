"""Test suite for processing.technology_storage — Checkpoint 2.2.b.

Deterministic, mocked tests exercising TechnologyWriter against a fake
psycopg Connection/Cursor/Transaction double modeling `technologies`
(keyed on slug) and `repository_technologies` (keyed on
(repository_id, technology_id)) - no real network, no live PostgreSQL
dependency for normal test execution. Mirrors the style established in
tests/test_content_storage.py (Checkpoint 1.4.c).
"""

from __future__ import annotations

import copy
import re
import uuid

import psycopg
import pytest

from processing.exceptions import TechnologyPersistenceError
from processing.technology_storage import TechnologyWriter
from processing.technology_taxonomy import Classification, Technology

# ---------------------------------------------------------------------------
# Fixtures — a fake psycopg Connection/Cursor/Transaction modeling
# technologies + repository_technologies' real upsert semantics.
# ---------------------------------------------------------------------------


class FakeTechnologyRow:
    def __init__(self, tech_id, slug, name, category):
        self.id = tech_id
        self.slug = slug
        self.name = name
        self.category = category


class FakeRepoTechRow:
    def __init__(self, role, confidence):
        self.role = role
        self.detected_via = "manifest"
        self.confidence = confidence
        self.first_detected_at_ticks = 0
        self.last_confirmed_at_ticks = 0
        self.removed_at = None  # None == NULL, else a tick int


class FakeTechnologyRegistry:
    """Simulates `technologies` (keyed on slug) and
    `repository_technologies` (keyed on (repository_id, technology_id)),
    modeling the exact ON CONFLICT DO UPDATE semantics
    technology_storage.py's upserts rely on."""

    def __init__(self) -> None:
        self._technologies_by_slug: dict[str, FakeTechnologyRow] = {}
        self._repo_tech: dict[tuple, FakeRepoTechRow] = {}
        self._clock = 0

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def upsert_technology(self, params: dict) -> uuid.UUID:
        existing = self._technologies_by_slug.get(params["slug"])
        if existing is None:
            row = FakeTechnologyRow(uuid.uuid4(), params["slug"], params["name"], params["category"])
            self._technologies_by_slug[params["slug"]] = row
            return row.id
        existing.name = params["name"]  # category deliberately NOT updated on conflict
        return existing.id

    def upsert_repository_technology(self, params: dict) -> None:
        key = (params["repository_id"], params["technology_id"])
        now = self._tick()
        existing = self._repo_tech.get(key)
        if existing is None:
            row = FakeRepoTechRow(params["role"], params["confidence"])
            row.first_detected_at_ticks = now
            row.last_confirmed_at_ticks = now
            self._repo_tech[key] = row
            return
        existing.role = params["role"]
        existing.confidence = params["confidence"]
        existing.last_confirmed_at_ticks = now
        existing.removed_at = None
        # first_detected_at_ticks intentionally untouched

    def technology_by_slug(self, slug: str) -> FakeTechnologyRow | None:
        return self._technologies_by_slug.get(slug)

    def repo_tech(self, repository_id, technology_id) -> FakeRepoTechRow | None:
        return self._repo_tech.get((repository_id, technology_id))

    def repo_tech_count(self, repository_id=None) -> int:
        if repository_id is None:
            return len(self._repo_tech)
        return sum(1 for (rid, _) in self._repo_tech if rid == repository_id)


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
        if re.search(r"INSERT INTO technologies", sql):
            self._last_id = self._conn.registry.upsert_technology(params)
        elif re.search(r"INSERT INTO repository_technologies", sql):
            self._conn.registry.upsert_repository_technology(params)
            self._last_id = None
        else:
            self._last_id = None

    def fetchone(self):
        return (self._last_id,)


class FakeTransaction:
    """Mimics psycopg3's Connection.transaction(): commits on clean exit,
    rolls back and re-raises on any exception, restoring the fake
    registry's in-memory state to its pre-transaction snapshot."""

    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self._tech_snapshot = None
        self._repo_tech_snapshot = None

    def __enter__(self) -> "FakeTransaction":
        self._tech_snapshot = copy.deepcopy(self._conn.registry._technologies_by_slug)
        self._repo_tech_snapshot = copy.deepcopy(self._conn.registry._repo_tech)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.committed = True
        else:
            self._conn.registry._technologies_by_slug = self._tech_snapshot
            self._conn.registry._repo_tech = self._repo_tech_snapshot
            self._conn.rolled_back = True
        return False


class FakeConnection:
    def __init__(self, registry: FakeTechnologyRegistry | None = None, fail_on: str | None = None) -> None:
        self.registry = registry or FakeTechnologyRegistry()
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


def make_classification(
    slug: str = "flask",
    name: str = "Flask",
    category: str = "framework",
    role: str = "secondary",
) -> Classification:
    return Classification(Technology(slug, name, category), role, 1.0)


# ---------------------------------------------------------------------------
# Basic persistence
# ---------------------------------------------------------------------------


class TestBasicPersistence:
    def test_new_technology_creation(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        ids = writer.upsert_technologies(repo_id, [make_classification()])

        assert len(ids) == 1
        tech = conn.registry.technology_by_slug("flask")
        assert tech is not None
        assert tech.name == "Flask"
        assert tech.category == "framework"
        assert conn.committed is True

    def test_repository_association_created(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])

        row = conn.registry.repo_tech(repo_id, tech_id)
        assert row is not None
        assert row.role == "secondary"
        assert row.detected_via == "manifest"
        assert row.confidence == 1.0

    def test_multiple_technologies_one_transaction(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        classifications = [
            make_classification("flask", "Flask", "framework"),
            make_classification("pytest", "pytest", "testing_tool", role="dev"),
        ]
        ids = writer.upsert_technologies(repo_id, classifications)

        assert len(ids) == 2
        assert len(set(ids)) == 2
        assert conn.registry.repo_tech_count(repo_id) == 2

    def test_empty_classifications_list_is_a_noop(self):
        repo_id = uuid.uuid4()
        connection_opened = []
        writer = TechnologyWriter(connection_factory=lambda: connection_opened.append(1) or FakeConnection())

        ids = writer.upsert_technologies(repo_id, [])

        assert ids == []
        assert connection_opened == []

    def test_detected_via_is_always_manifest(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])

        assert conn.registry.repo_tech(repo_id, tech_id).detected_via == "manifest"

    def test_confidence_persisted_unchanged(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])

        assert conn.registry.repo_tech(repo_id, tech_id).confidence == 1.0


# ---------------------------------------------------------------------------
# Idempotency / reuse
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_existing_technology_reused_not_duplicated(self):
        repo_a = uuid.uuid4()
        repo_b = uuid.uuid4()
        registry = FakeTechnologyRegistry()
        writer_a = TechnologyWriter(connection_factory=lambda: FakeConnection(registry))
        writer_b = TechnologyWriter(connection_factory=lambda: FakeConnection(registry))

        [id_a] = writer_a.upsert_technologies(repo_a, [make_classification()])
        [id_b] = writer_b.upsert_technologies(repo_b, [make_classification()])

        assert id_a == id_b  # same slug -> same technologies row
        assert len(registry._technologies_by_slug) == 1

    def test_duplicate_association_prevented_by_pk(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        writer.upsert_technologies(repo_id, [make_classification(), make_classification()])

        assert conn.registry.repo_tech_count(repo_id) == 1

    def test_repeated_processing_preserves_first_detected_at(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])
        first_tick = conn.registry.repo_tech(repo_id, tech_id).first_detected_at_ticks

        writer.upsert_technologies(repo_id, [make_classification()])
        row = conn.registry.repo_tech(repo_id, tech_id)

        assert row.first_detected_at_ticks == first_tick

    def test_repeated_processing_advances_last_confirmed_at(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])
        first_confirmed = conn.registry.repo_tech(repo_id, tech_id).last_confirmed_at_ticks

        writer.upsert_technologies(repo_id, [make_classification()])
        row = conn.registry.repo_tech(repo_id, tech_id)

        assert row.last_confirmed_at_ticks > first_confirmed

    def test_removed_at_reset_when_observed_again(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [tech_id] = writer.upsert_technologies(repo_id, [make_classification()])
        conn.registry.repo_tech(repo_id, tech_id).removed_at = 999  # simulate a prior "removed" mark

        writer.upsert_technologies(repo_id, [make_classification()])

        assert conn.registry.repo_tech(repo_id, tech_id).removed_at is None

    def test_no_stale_reconciliation_for_unobserved_technology(self):
        # 2.2.b never touches a technology it wasn't handed - a
        # previously-associated technology absent from this call's
        # input is left completely unchanged (no removed_at set).
        repo_id = uuid.uuid4()
        conn = FakeConnection()
        writer = TechnologyWriter(connection_factory=lambda: conn)

        [flask_id] = writer.upsert_technologies(repo_id, [make_classification("flask", "Flask", "framework")])
        writer.upsert_technologies(repo_id, [make_classification("pytest", "pytest", "testing_tool", role="dev")])

        # flask's row must be untouched by the second call (which only mentioned pytest)
        assert conn.registry.repo_tech(repo_id, flask_id).removed_at is None
        assert conn.registry.repo_tech_count(repo_id) == 2


# ---------------------------------------------------------------------------
# Failure / rollback
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_rollback_on_failure_leaves_no_partial_rows(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO repository_technologies")
        writer = TechnologyWriter(connection_factory=lambda: conn)

        with pytest.raises(TechnologyPersistenceError):
            writer.upsert_technologies(repo_id, [make_classification()])

        assert conn.registry.repo_tech_count(repo_id) == 0
        assert conn.rolled_back is True
        assert conn.committed is False

    def test_failure_midway_through_batch_rolls_back_everything(self):
        repo_id = uuid.uuid4()

        # A connection that fails only on the *second* repository_technologies
        # insert, to prove a partial in-batch failure discards the whole batch,
        # not just the failing item.
        class FailAfterOne(FakeConnection):
            def __init__(self, registry):
                super().__init__(registry)
                self._repo_tech_inserts = 0

            def cursor(self):
                return FailAfterOneCursor(self)

        class FailAfterOneCursor(FakeCursor):
            def execute(self, sql, params=None):
                if re.search(r"INSERT INTO repository_technologies", sql):
                    self._conn._repo_tech_inserts += 1
                    if self._conn._repo_tech_inserts == 2:
                        raise psycopg.Error("simulated failure on second association")
                super().execute(sql, params)

        conn2 = FailAfterOne(FakeTechnologyRegistry())
        writer2 = TechnologyWriter(connection_factory=lambda: conn2)

        with pytest.raises(TechnologyPersistenceError):
            writer2.upsert_technologies(
                repo_id,
                [
                    make_classification("flask", "Flask", "framework"),
                    make_classification("pytest", "pytest", "testing_tool", role="dev"),
                ],
            )

        assert conn2.registry.repo_tech_count(repo_id) == 0  # both rolled back, not just the failing one

    def test_persistence_error_wraps_original_cause(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO technologies")
        writer = TechnologyWriter(connection_factory=lambda: conn)

        with pytest.raises(TechnologyPersistenceError) as exc_info:
            writer.upsert_technologies(repo_id, [make_classification()])

        assert exc_info.value.repository_id == repo_id
        assert isinstance(exc_info.value.cause, psycopg.Error)

    def test_connection_always_closed(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(fail_on="INSERT INTO technologies")
        writer = TechnologyWriter(connection_factory=lambda: conn)

        with pytest.raises(TechnologyPersistenceError):
            writer.upsert_technologies(repo_id, [make_classification()])

        assert conn.closed is True
