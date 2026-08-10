"""Test suite for acquisition.storage — Checkpoint 1.3.d.

Deterministic, mocked tests exercising RepositoryWriter against a fake
psycopg Connection/Cursor/Transaction double — no real network, no live
PostgreSQL dependency for normal test execution. Mirrors the style
established in tests/test_github_client.py (Checkpoint 1.1.g) and
tests/test_acquisition.py (Checkpoint 1.3.a).

CHECKPOINT_1_3B_REPORT.md already live-verified this module's SQL against
a real local PostgreSQL database (30/30 checks, Sec6.1) — this suite does
not repeat that live proof. Its job is different: protect the already-
verified behaviour from regressing, deterministically, in CI, without a
database, via a fake connection that mimics psycopg3's upsert semantics
(ON CONFLICT ... RETURNING id: same natural key -> same id; a new key ->
a freshly minted id) closely enough to exercise idempotency, FK ordering,
and rollback at the *code path* level.
"""

from __future__ import annotations

import logging
import re
import uuid

import psycopg
import pytest

from acquisition.exceptions import AcquisitionStorageError, RepositoryPersistenceError
from acquisition.storage import RepositoryWriter
from config.settings import Settings
from github_client.rate_limiter import RateLimitInfo
from github_client.rest import OwnerSummary, RepositoryData

# ---------------------------------------------------------------------------
# Fixtures — a fake psycopg Connection/Cursor/Transaction, no real database.
# ---------------------------------------------------------------------------


class FakeIdRegistry:
    """Simulates `ON CONFLICT (...) DO UPDATE ... RETURNING id`: the same
    natural key (table, key-value) always yields the same UUID, matching a
    real database's upsert semantics — the mechanism the "topic reuse",
    "owner update", and "idempotent repeated writes" tests below rely on.
    Persists across separate FakeConnection instances that share the same
    registry, the same way separate real connections would still see the
    same underlying rows.
    """

    def __init__(self) -> None:
        self._ids: dict[tuple[str, object], uuid.UUID] = {}
        self.write_counts: dict[tuple[str, object], int] = {}

    def get_or_create(self, table: str, key: object) -> uuid.UUID:
        cache_key = (table, key)
        self.write_counts[cache_key] = self.write_counts.get(cache_key, 0) + 1
        if cache_key not in self._ids:
            self._ids[cache_key] = uuid.uuid4()
        return self._ids[cache_key]

    def row_count(self, table: str) -> int:
        return len({k for (t, k) in self._ids if t == table})


def _extract_key(table: str, params: dict) -> object:
    if table in ("owners", "repositories"):
        return params["github_id"]
    if table == "licenses":
        return params["spdx_id"]
    if table == "topics":
        return params["slug"]
    raise AssertionError(f"unexpected RETURNING-id table in test double: {table}")


class FakeCursor:
    """Records every execute()/executemany() call verbatim (sql, params) on
    the owning connection's `operations` log, and resolves RETURNING id
    via FakeIdRegistry — never interprets or string-formats SQL beyond the
    minimal table-name regex needed to route to the registry.
    """

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
        table_match = re.search(r"INSERT INTO (\w+)", sql)
        if table_match and "RETURNING id" in sql:
            table = table_match.group(1)
            key = _extract_key(table, params)
            self._last_id = self._conn.registry.get_or_create(table, key)
        else:
            self._last_id = None

    def executemany(self, sql: str, params_seq) -> None:
        params_list = list(params_seq)
        self._conn.operations.append(("executemany", sql, params_list))
        if self._conn.fail_on and self._conn.fail_on in sql:
            raise psycopg.Error(f"simulated failure: {self._conn.fail_on}")

    def fetchone(self):
        return (self._last_id,)


class FakeTransaction:
    """Mimics psycopg3's `Connection.transaction()` context manager: commits
    (sets conn.committed) on clean exit, rolls back (sets conn.rolled_back)
    and re-raises on any exception — the exact contract acquisition/storage.py
    Sec"Transaction boundary" documents relying on.
    """

    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn

    def __enter__(self) -> "FakeTransaction":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._conn.committed = True
        else:
            self._conn.rolled_back = True
        return False  # never swallow — matches real Connection.transaction()


class FakeConnection:
    """A fake psycopg.Connection. `fail_on`, if set, is a substring of an
    executed SQL statement that should raise psycopg.Error when reached —
    used to simulate a failure at a specific step of upsert_repository()'s
    six-step sequence.
    """

    def __init__(self, registry: FakeIdRegistry | None = None, fail_on: str | None = None) -> None:
        self.registry = registry or FakeIdRegistry()
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


def table_order(conn: FakeConnection) -> list[str]:
    """Extract the sequence of tables INSERTed into, in call order."""
    tables = []
    for _, sql, _ in conn.operations:
        match = re.search(r"INSERT INTO (\w+)", sql)
        if match:
            tables.append(match.group(1))
    return tables


# ---------------------------------------------------------------------------
# RepositoryData fixtures
# ---------------------------------------------------------------------------


def make_repo_data(
    full_name: str,
    github_id: int | None = None,
    license_spdx_id: str | None = "MIT",
    license_name: str | None = "MIT License",
    topics: tuple[str, ...] = ("machine-learning", "python-library"),
    owner_login: str | None = None,
    owner_account_type: str = "Organization",
    is_archived: bool = False,
) -> RepositoryData:
    owner_login = owner_login or full_name.split("/")[0]
    name = full_name.split("/")[1]
    return RepositoryData(
        github_id=github_id if github_id is not None else (hash(full_name) & 0xFFFFFFFF),
        name=name,
        full_name=full_name,
        description="A test repository",
        primary_language="Python",
        license_spdx_id=license_spdx_id,
        is_archived=is_archived,
        is_fork=False,
        github_created_at="2020-01-01T00:00:00Z",
        pushed_at="2020-06-01T00:00:00Z",
        owner=OwnerSummary(
            github_id=(hash(owner_login) & 0xFFFFFFFF),
            login=owner_login,
            account_type=owner_account_type,
            avatar_url="https://example.invalid/a.png",
        ),
        rate_limit=RateLimitInfo(limit=5000, remaining=4999, reset_at=0, used=1, resource="core"),
        topics=topics,
        license_name=license_name,
        stars_count=1000,
        forks_count=50,
        watchers_count=1000,
        open_issues_count=5,
        size_kb=890,
    )


def make_settings(**overrides) -> Settings:
    fields = {"github_token": "x", "log_level": "INFO"}
    fields.update(overrides)
    return Settings(**fields)


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------


class TestOwnerUpsert:
    def test_owner_insert(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        owner_ops = [op for op in conn.operations if "INSERT INTO owners" in op[1]]
        assert len(owner_ops) == 1
        _, sql, params = owner_ops[0]
        assert params["login"] == "octocat"
        assert params["account_type"] == "organization"  # lower-cased at the storage boundary

    def test_owner_update_reuses_same_id_no_duplicate_row(self):
        registry = FakeIdRegistry()
        conn = FakeConnection(registry)
        writer = RepositoryWriter(connection_factory=lambda: conn)

        data1 = make_repo_data("octocat/Hello-World", owner_login="octocat")
        writer.upsert_repository(data1)
        # Re-run with the same owner (github_id derived from login) but a
        # different repository — simulates the owner row being updated
        # (login/account_type refreshed), not re-inserted as a new row.
        data2 = make_repo_data("octocat/Spoon-Knife", owner_login="octocat")
        writer.upsert_repository(data2)

        assert registry.row_count("owners") == 1  # one physical row
        assert registry.write_counts[("owners", data1.owner.github_id)] == 2  # written twice


# ---------------------------------------------------------------------------
# License
# ---------------------------------------------------------------------------


class TestLicenseUpsert:
    def test_license_insert(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", license_spdx_id="MIT", license_name="MIT License"))

        license_ops = [op for op in conn.operations if "INSERT INTO licenses" in op[1]]
        assert len(license_ops) == 1
        assert license_ops[0][2] == {"spdx_id": "MIT", "name": "MIT License"}

    def test_repository_without_license_stores_null_license_id(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", license_spdx_id=None, license_name=None))

        license_ops = [op for op in conn.operations if "INSERT INTO licenses" in op[1]]
        assert license_ops == []  # no license row touched at all
        repo_ops = [op for op in conn.operations if "INSERT INTO repositories" in op[1]]
        assert repo_ops[0][2]["license_id"] is None


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TestRepositoryUpsert:
    def test_repository_insert(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        repo_id = writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert isinstance(repo_id, uuid.UUID)
        repo_ops = [op for op in conn.operations if "INSERT INTO repositories" in op[1]]
        assert len(repo_ops) == 1
        assert repo_ops[0][2]["full_name"] == "octocat/Hello-World"

    def test_repository_update_same_github_id_returns_same_id(self):
        registry = FakeIdRegistry()
        conn = FakeConnection(registry)
        writer = RepositoryWriter(connection_factory=lambda: conn)

        data = make_repo_data("octocat/Hello-World", github_id=555001)
        first_id = writer.upsert_repository(data)

        changed = make_repo_data("octocat/Hello-World", github_id=555001)
        object.__setattr__(changed, "description", "An updated description")
        second_id = writer.upsert_repository(changed)

        assert first_id == second_id
        assert registry.row_count("repositories") == 1


# ---------------------------------------------------------------------------
# Topics + repository_topics
# ---------------------------------------------------------------------------


class TestTopics:
    def test_topic_creation(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", topics=("ml", "data-science")))

        topic_ops = [op for op in conn.operations if "INSERT INTO topics" in op[1]]
        assert {op[2]["slug"] for op in topic_ops} == {"ml", "data-science"}

    def test_topic_reuse_across_repositories(self):
        registry = FakeIdRegistry()
        conn = FakeConnection(registry)
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/repo-a", github_id=1, topics=("shared-topic",)))
        writer.upsert_repository(make_repo_data("octocat/repo-b", github_id=2, topics=("shared-topic",)))

        assert registry.row_count("topics") == 1  # one physical topic row, reused
        assert registry.write_counts[("topics", "shared-topic")] == 2

    def test_repository_without_topics(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", topics=()))

        assert [op for op in conn.operations if "INSERT INTO topics" in op[1]] == []
        assert [op for op in conn.operations if op[0] == "executemany"] == []  # no repository_topics link at all

    def test_repository_topics_linking(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", topics=("ml", "data-science")))

        link_ops = [op for op in conn.operations if op[0] == "executemany"]
        assert len(link_ops) == 1
        _, sql, params_list = link_ops[0]
        assert "INSERT INTO repository_topics" in sql
        assert len(params_list) == 2
        assert all("repository_id" in p and "topic_id" in p for p in params_list)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_insertion(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        snapshot_ops = [op for op in conn.operations if "INSERT INTO repository_snapshot" in op[1]]
        assert len(snapshot_ops) == 1
        assert snapshot_ops[0][2]["stars_count"] == 1000
        assert snapshot_ops[0][2]["forks_count"] == 50


# ---------------------------------------------------------------------------
# Idempotency (whole-call)
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_repeated_write_same_data_produces_same_ids_no_duplicates(self):
        registry = FakeIdRegistry()
        conn = FakeConnection(registry)
        writer = RepositoryWriter(connection_factory=lambda: conn)
        data = make_repo_data("octocat/Hello-World", github_id=42, topics=("ml",))

        first_id = writer.upsert_repository(data)
        second_id = writer.upsert_repository(data)

        assert first_id == second_id
        assert registry.row_count("owners") == 1
        assert registry.row_count("licenses") == 1
        assert registry.row_count("topics") == 1
        assert registry.row_count("repositories") == 1


# ---------------------------------------------------------------------------
# FK ordering
# ---------------------------------------------------------------------------


class TestForeignKeyOrdering:
    def test_writes_happen_in_fk_dependency_order(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World", topics=("ml", "data-science")))

        order = table_order(conn)
        # owners and licenses must precede repositories (FK); topics must
        # precede repository_topics (executemany, checked separately);
        # repositories must precede repository_snapshot (FK).
        assert order.index("owners") < order.index("repositories")
        assert order.index("licenses") < order.index("repositories")
        assert max(i for i, t in enumerate(order) if t == "topics") < order.index("repositories")
        assert order.index("repositories") < order.index("repository_snapshot")

        link_op_index = next(i for i, op in enumerate(conn.operations) if op[0] == "executemany")
        repositories_op_index = next(i for i, op in enumerate(conn.operations) if "INSERT INTO repositories" in op[1])
        assert repositories_op_index < link_op_index  # repository_topics linked only after the repository row exists


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransactionBoundary:
    def test_commits_on_success(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert conn.committed is True
        assert conn.rolled_back is False
        assert conn.closed is True

    def test_rolls_back_on_failure_partway_through(self):
        conn = FakeConnection(fail_on="INSERT INTO repositories")
        writer = RepositoryWriter(connection_factory=lambda: conn)

        with pytest.raises(RepositoryPersistenceError):
            writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert conn.rolled_back is True
        assert conn.committed is False
        assert conn.closed is True  # connection is always closed, success or failure

    @pytest.mark.parametrize(
        "fail_on",
        ["INSERT INTO owners", "INSERT INTO licenses", "INSERT INTO topics", "INSERT INTO repository_snapshot"],
    )
    def test_rolls_back_on_failure_at_every_step(self, fail_on):
        conn = FakeConnection(fail_on=fail_on)
        writer = RepositoryWriter(connection_factory=lambda: conn)

        with pytest.raises(RepositoryPersistenceError):
            writer.upsert_repository(make_repo_data("octocat/Hello-World", topics=("ml",)))

        assert conn.rolled_back is True
        assert conn.committed is False


# ---------------------------------------------------------------------------
# Parameterized SQL usage
# ---------------------------------------------------------------------------


class TestParameterizedSQL:
    def test_all_execute_calls_use_named_placeholders_not_string_interpolation(self):
        conn = FakeConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)
        data = make_repo_data("octocat/UniqueRepoName123", github_id=987654321)

        writer.upsert_repository(data)

        for kind, sql, params in conn.operations:
            if kind == "execute" and ("INSERT" in sql or "UPDATE" in sql):
                assert isinstance(params, dict), f"expected dict params, got {type(params)} for: {sql[:60]}"
                # The distinctive literal values must never appear directly
                # embedded in the SQL text — only via %(name)s placeholders.
                assert "987654321" not in sql
                assert "UniqueRepoName123" not in sql
            elif kind == "executemany":
                assert isinstance(params, list)
                assert all(isinstance(p, dict) for p in params)


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class TestTypedExceptions:
    def test_persistence_error_wraps_cause_and_full_name(self):
        conn = FakeConnection(fail_on="INSERT INTO repositories")
        writer = RepositoryWriter(connection_factory=lambda: conn)

        with pytest.raises(RepositoryPersistenceError) as excinfo:
            writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert excinfo.value.full_name == "octocat/Hello-World"
        assert isinstance(excinfo.value.cause, psycopg.Error)
        assert "octocat/Hello-World" in str(excinfo.value)

    def test_persistence_error_is_an_acquisition_storage_error(self):
        assert issubclass(RepositoryPersistenceError, AcquisitionStorageError)

    def test_non_psycopg_exception_is_not_wrapped(self):
        # Confirms the except clause is narrowly typed to psycopg.Error,
        # not a bare `except Exception` that would swallow programming
        # errors (e.g. a bug in this test double itself) as if they were
        # ordinary persistence failures.
        class BrokenCursor(FakeCursor):
            def execute(self, sql, params=None):
                if "INSERT INTO owners" in sql:
                    raise RuntimeError("not a psycopg error")
                super().execute(sql, params)

        class BrokenConnection(FakeConnection):
            def cursor(self):
                return BrokenCursor(self)

        conn = BrokenConnection()
        writer = RepositoryWriter(connection_factory=lambda: conn)

        with pytest.raises(RuntimeError):
            writer.upsert_repository(make_repo_data("octocat/Hello-World"))


# ---------------------------------------------------------------------------
# Security — no credential leakage via logging
# ---------------------------------------------------------------------------


class TestNoCredentialLeakage:
    def test_postgres_password_never_logged_on_success(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        secret = "ultra-secret-pw-99"
        settings = make_settings(postgres_password=secret)
        conn = FakeConnection()
        monkeypatch.setattr(psycopg, "connect", lambda **kwargs: conn)
        writer = RepositoryWriter(settings=settings)

        writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert secret not in caplog.text

    def test_postgres_password_never_logged_on_failure(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        secret = "ultra-secret-pw-99"
        settings = make_settings(postgres_password=secret)
        conn = FakeConnection(fail_on="INSERT INTO repositories")
        monkeypatch.setattr(psycopg, "connect", lambda **kwargs: conn)
        writer = RepositoryWriter(settings=settings)

        with pytest.raises(RepositoryPersistenceError) as excinfo:
            writer.upsert_repository(make_repo_data("octocat/Hello-World"))

        assert secret not in caplog.text
        assert secret not in str(excinfo.value)

    def test_settings_repr_redacts_postgres_password(self):
        # Retains/extends the redaction contract tests/test_github_client.py
        # already established for github_token — postgres_password gained
        # the same __repr__ redaction in Checkpoint 1.3.b (config/settings.py).
        settings = make_settings(postgres_password="another-secret")
        assert "another-secret" not in repr(settings)
        assert "***redacted***" in repr(settings)
