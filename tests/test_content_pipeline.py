"""Test suite for acquisition.content_pipeline — Checkpoint 1.4.d.

Deterministic, mocked tests exercising the real ContentAcquisitionPipeline
against fake RepositoryContentCandidateProvider/RepositoryCloner/
extract_content/ContentWriter collaborators — no real network, no live
git, no live PostgreSQL dependency for normal test execution. Mirrors
the style established in tests/test_pipeline.py (Checkpoint 1.3.c):
dict/list-driven fakes with a recorded call log, composed with the real
orchestrator class rather than a "fake of the pipeline itself."

RepositoryContentCandidateProvider's own SQL is tested separately
(TestCandidateProvider below) against a minimal fake psycopg connection,
mirroring tests/test_content_storage.py's FakeConnection pattern — kept
distinct from the pipeline tests since it is genuinely new read-only SQL
this checkpoint introduces, not orchestration logic.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from acquisition.content_extractor import ExtractedFile
from acquisition.content_pipeline import (
    ContentAcquisitionPipeline,
    ContentFailure,
    ProcessedRepository,
    RepositoryContentCandidateProvider,
)
from acquisition.exceptions import (
    ContentAcquisitionError,
    ContentPersistenceError,
    RepositoryCloneError,
)

# ---------------------------------------------------------------------------
# Fakes — mirror tests/test_pipeline.py's dict/list-driven, call-recording style
# ---------------------------------------------------------------------------


class FakeCandidateProvider:
    def __init__(self, candidates: list[tuple[uuid.UUID, str]]) -> None:
        self._candidates = candidates
        self.calls = 0

    def get_candidates(self) -> list[tuple[uuid.UUID, str]]:
        self.calls += 1
        return self._candidates


class FailingCandidateProvider:
    def get_candidates(self):
        raise RuntimeError("candidate query failed")


class FakeCloner:
    """Fake RepositoryCloner.clone() — driven by full_name -> Path-or-
    exception. Records every call, in order."""

    def __init__(self, results_by_full_name: dict[str, object]) -> None:
        self._results = results_by_full_name
        self.calls: list[str] = []

    @contextmanager
    def clone(self, full_name: str):
        self.calls.append(full_name)
        result = self._results[full_name]
        if isinstance(result, Exception):
            raise result
        yield result


class FakeExtractor:
    """Fake extract_content() — driven by clone_dir -> files-or-exception.
    Records every call (by the path it received), in order."""

    def __init__(self, results_by_path: dict[Path, object]) -> None:
        self._results = results_by_path
        self.calls: list[Path] = []

    def __call__(self, clone_dir: Path) -> list[ExtractedFile]:
        self.calls.append(clone_dir)
        result = self._results[clone_dir]
        if isinstance(result, Exception):
            raise result
        return result


class FakeContentWriter:
    """Fake ContentWriter.upsert_content() — driven by repository_id ->
    ids-or-exception. Records every call, in order."""

    def __init__(self, results_by_repo_id: dict[uuid.UUID, object] | None = None) -> None:
        self._results = results_by_repo_id or {}
        self.calls: list[tuple[uuid.UUID, list[ExtractedFile]]] = []

    def upsert_content(self, repository_id: uuid.UUID, files: list[ExtractedFile]) -> list[uuid.UUID]:
        self.calls.append((repository_id, files))
        result = self._results.get(repository_id, [uuid.uuid4() for _ in files])
        if isinstance(result, Exception):
            raise result
        return result


def make_file(file_path: str = "README.md", content: str = "hello") -> ExtractedFile:
    return ExtractedFile(
        content_type="readme",
        file_path=file_path,
        content=content,
        content_hash="hash",
        content_size_bytes=len(content),
    )


def build_pipeline(candidates, clones, extracts, writer_results=None):
    provider = FakeCandidateProvider(candidates)
    cloner = FakeCloner(clones)
    extractor = FakeExtractor(extracts)
    writer = FakeContentWriter(writer_results)
    pipeline = ContentAcquisitionPipeline(
        candidate_provider=provider,
        cloner=cloner,
        writer=writer,
        extract_content_fn=extractor,
    )
    return pipeline, provider, cloner, extractor, writer


# ---------------------------------------------------------------------------
# Basic composition
# ---------------------------------------------------------------------------


class TestBasicRun:
    def test_empty_candidate_set(self):
        pipeline, provider, cloner, extractor, writer = build_pipeline([], {}, {})

        result = pipeline.run()

        assert result.stats.candidates_selected == 0
        assert result.stats.attempted == 0
        assert result.stats.succeeded == 0
        assert result.stats.failed == 0
        assert result.processed == ()
        assert result.failures == ()
        assert cloner.calls == []
        assert writer.calls == []

    def test_one_successful_repository(self):
        repo_id = uuid.uuid4()
        clone_dir = Path("/tmp/clone-a")
        pipeline, *_rest = build_pipeline(
            [(repo_id, "octocat/Hello-World")],
            {"octocat/Hello-World": clone_dir},
            {clone_dir: [make_file()]},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(1, 1, 1, 0)
        assert result.processed == (ProcessedRepository(repo_id, "octocat/Hello-World", 1),)
        assert result.failures == ()

    def test_multiple_successful_repositories(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        dir_a, dir_b = Path("/tmp/a"), Path("/tmp/b")
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {"a/a": dir_a, "b/b": dir_b},
            {dir_a: [make_file("README.md")], dir_b: [make_file("README.md"), make_file("package.json")]},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 2, 0)
        assert [p.files_persisted for p in result.processed] == [1, 2]

    def test_empty_extraction_is_a_success_not_a_failure(self):
        repo_id = uuid.uuid4()
        clone_dir = Path("/tmp/empty")
        pipeline, *_rest = build_pipeline(
            [(repo_id, "empty/repo")],
            {"empty/repo": clone_dir},
            {clone_dir: []},
        )

        result = pipeline.run()

        assert result.stats.succeeded == 1
        assert result.stats.failed == 0
        assert result.processed[0].files_persisted == 0


def pytest_stats(selected, attempted, succeeded, failed):
    from acquisition.content_pipeline import ContentAcquisitionStats

    return ContentAcquisitionStats(
        candidates_selected=selected, attempted=attempted, succeeded=succeeded, failed=failed
    )


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_clone_failure_recorded_and_batch_continues(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        dir_b = Path("/tmp/b")
        pipeline, provider, cloner, extractor, writer = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {"a/a": RepositoryCloneError("a/a", "not found"), "b/b": dir_b},
            {dir_b: [make_file()]},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 1, 1)
        assert result.failures[0].stage == "clone"
        assert result.failures[0].error_type == "RepositoryCloneError"
        assert result.failures[0].repository_id == repo_a
        assert result.processed[0].repository_id == repo_b
        # extraction/persistence never attempted for the failed clone
        assert dir_b in extractor.calls
        assert len(extractor.calls) == 1
        assert writer.calls[0][0] == repo_b

    def test_extraction_failure_recorded_and_batch_continues(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        dir_a, dir_b = Path("/tmp/a"), Path("/tmp/b")
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {"a/a": dir_a, "b/b": dir_b},
            {dir_a: ContentAcquisitionError("extraction blew up"), dir_b: [make_file()]},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 1, 1)
        assert result.failures[0].stage == "extract"
        assert result.failures[0].repository_id == repo_a
        assert result.processed[0].repository_id == repo_b

    def test_persistence_failure_recorded_and_batch_continues(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        dir_a, dir_b = Path("/tmp/a"), Path("/tmp/b")
        pipeline, provider, cloner, extractor, writer = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {"a/a": dir_a, "b/b": dir_b},
            {dir_a: [make_file()], dir_b: [make_file()]},
            writer_results={repo_a: ContentPersistenceError(repo_a, RuntimeError("db down"))},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 1, 1)
        assert result.failures[0].stage == "persist"
        assert result.failures[0].repository_id == repo_a
        assert result.processed[0].repository_id == repo_b

    def test_unexpected_exception_recorded_and_batch_continues(self, caplog):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        dir_b = Path("/tmp/b")
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {"a/a": ValueError("something unrelated broke"), "b/b": dir_b},
            {dir_b: [make_file()]},
        )

        with caplog.at_level(logging.ERROR):
            result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 1, 1)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "ValueError"
        assert result.processed[0].repository_id == repo_b
        assert any("content_pipeline_unexpected_error" in r.message for r in caplog.records)

    def test_unexpected_exception_from_writer_recorded(self):
        repo_id = uuid.uuid4()
        clone_dir = Path("/tmp/a")
        pipeline, *_rest = build_pipeline(
            [(repo_id, "a/a")],
            {"a/a": clone_dir},
            {clone_dir: [make_file()]},
            writer_results={repo_id: RuntimeError("unexpected db driver crash")},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(1, 1, 0, 1)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "RuntimeError"

    def test_all_repositories_failing(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b")],
            {
                "a/a": RepositoryCloneError("a/a", "gone"),
                "b/b": RepositoryCloneError("b/b", "gone"),
            },
            {},
        )

        result = pipeline.run()

        assert result.stats == pytest_stats(2, 2, 0, 2)
        assert result.processed == ()
        assert len(result.failures) == 2

    def test_mixed_success_and_failure_preserves_order(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        dir_a, dir_c = Path("/tmp/a"), Path("/tmp/c")
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b"), (repo_c, "c/c")],
            {"a/a": dir_a, "b/b": RepositoryCloneError("b/b", "gone"), "c/c": dir_c},
            {dir_a: [make_file()], dir_c: [make_file()]},
        )

        result = pipeline.run()

        assert [p.repository_id for p in result.processed] == [repo_a, repo_c]
        assert [f.repository_id for f in result.failures] == [repo_b]

    def test_no_downstream_calls_after_clone_failure(self):
        repo_id = uuid.uuid4()
        pipeline, provider, cloner, extractor, writer = build_pipeline(
            [(repo_id, "a/a")],
            {"a/a": RepositoryCloneError("a/a", "gone")},
            {},
        )

        pipeline.run()

        assert extractor.calls == []
        assert writer.calls == []

    def test_candidate_provider_failure_propagates_not_caught(self):
        pipeline = ContentAcquisitionPipeline(
            candidate_provider=FailingCandidateProvider(),
            cloner=FakeCloner({}),
            writer=FakeContentWriter(),
            extract_content_fn=FakeExtractor({}),
        )

        with pytest.raises(RuntimeError, match="candidate query failed"):
            pipeline.run()


# ---------------------------------------------------------------------------
# Statistics invariant
# ---------------------------------------------------------------------------


class TestStatisticsInvariant:
    def test_attempted_equals_succeeded_plus_failed(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        dir_a, dir_c = Path("/tmp/a"), Path("/tmp/c")
        pipeline, *_rest = build_pipeline(
            [(repo_a, "a/a"), (repo_b, "b/b"), (repo_c, "c/c")],
            {"a/a": dir_a, "b/b": RepositoryCloneError("b/b", "gone"), "c/c": dir_c},
            {dir_a: [make_file()], dir_c: []},
        )

        result = pipeline.run()

        assert result.stats.attempted == result.stats.succeeded + result.stats.failed
        assert result.stats.candidates_selected == result.stats.attempted


# ---------------------------------------------------------------------------
# Progress logging
# ---------------------------------------------------------------------------


class TestLogging:
    def test_start_and_completion_logged(self, caplog):
        repo_id = uuid.uuid4()
        clone_dir = Path("/tmp/a")
        pipeline, *_rest = build_pipeline(
            [(repo_id, "a/a")], {"a/a": clone_dir}, {clone_dir: [make_file()]}
        )

        with caplog.at_level(logging.INFO):
            pipeline.run()

        messages = [r.message for r in caplog.records]
        assert any("content_pipeline_started" in m for m in messages)
        assert any("content_pipeline_repository_succeeded" in m for m in messages)
        assert any("content_pipeline_completed" in m for m in messages)

    def test_repository_content_never_logged(self, caplog):
        repo_id = uuid.uuid4()
        clone_dir = Path("/tmp/a")
        secret_content = "SECRET_CONTENT_MARKER_XYZ"
        pipeline, *_rest = build_pipeline(
            [(repo_id, "a/a")], {"a/a": clone_dir}, {clone_dir: [make_file(content=secret_content)]}
        )

        with caplog.at_level(logging.DEBUG):
            pipeline.run()

        assert all(secret_content not in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# RepositoryContentCandidateProvider — its own SQL, tested separately
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows):
        self._rows = rows
        self.cur = None
        self.closed = False

    def cursor(self):
        self.cur = FakeCursor(self._rows)
        return self.cur

    def close(self):
        self.closed = True


class TestCandidateProvider:
    def test_returns_id_full_name_pairs(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection([(repo_id, "octocat/Hello-World")])
        provider = RepositoryContentCandidateProvider(connection_factory=lambda: conn)

        candidates = provider.get_candidates()

        assert candidates == [(repo_id, "octocat/Hello-World")]

    def test_query_is_unfiltered_no_join_no_content_exclusion(self):
        conn = FakeConnection([])
        provider = RepositoryContentCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        sql, params = conn.cur.executed[0]
        assert "repository_content" not in sql
        assert "JOIN" not in sql.upper()
        assert "NOT EXISTS" not in sql.upper()
        assert "FROM repositories" in sql
        assert params is None

    def test_connection_closed_after_use(self):
        conn = FakeConnection([])
        provider = RepositoryContentCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        assert conn.closed is True

    def test_empty_repositories_table(self):
        conn = FakeConnection([])
        provider = RepositoryContentCandidateProvider(connection_factory=lambda: conn)

        assert provider.get_candidates() == []
