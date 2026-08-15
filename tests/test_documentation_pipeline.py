"""Test suite for processing.documentation_pipeline — Checkpoint 2.3.d.

Deterministic, mocked tests exercising the real DocumentationPipeline
against fake ReadmeCandidateProvider/extract_readme_features/
compute_readability/DocumentationMetricsWriter collaborators — no real
network, no live PostgreSQL dependency for normal test execution. Mirrors
the style established in tests/test_technology_pipeline.py (2.2.c).

ReadmeCandidateProvider's own SQL is tested separately
(TestCandidateProvider below) against a minimal fake psycopg connection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

import pytest

from processing.exceptions import DocumentationPersistenceError
from processing.documentation_pipeline import (
    DocumentationAcquisitionStats,
    DocumentationCandidate,
    DocumentationPipeline,
    ProcessedRepository,
    ReadmeCandidateProvider,
)
from processing.readability import ReadabilityScores
from processing.readme_parser import ReadmeFeatures

# ---------------------------------------------------------------------------
# Fakes — mirror tests/test_technology_pipeline.py's dict/list-driven,
# call-recording style
# ---------------------------------------------------------------------------


class FakeCandidateProvider:
    def __init__(self, candidates: list[DocumentationCandidate]) -> None:
        self._candidates = candidates
        self.calls = 0

    def get_candidates(self) -> list[DocumentationCandidate]:
        self.calls += 1
        return self._candidates


class FailingCandidateProvider:
    def get_candidates(self):
        raise RuntimeError("candidate query failed")


class FakeFeatureExtractor:
    def __init__(self, results: dict[str, object]) -> None:
        self._results = results
        self.calls: list[str] = []

    def __call__(self, content: str) -> ReadmeFeatures:
        self.calls.append(content)
        result = self._results[content]
        if isinstance(result, Exception):
            raise result
        return result


class FakeReadabilityScorer:
    def __init__(self, results: dict[str, object]) -> None:
        self._results = results
        self.calls: list[str] = []

    def __call__(self, content: str) -> ReadabilityScores:
        self.calls.append(content)
        result = self._results[content]
        if isinstance(result, Exception):
            raise result
        return result


class FakeDocumentationWriter:
    """Fake DocumentationMetricsWriter.upsert_documentation_metrics() -
    driven by repository_id -> None-or-exception. Records every call."""

    def __init__(self, results_by_repo_id: dict[uuid.UUID, object] | None = None) -> None:
        self._results = results_by_repo_id or {}
        self.calls: list[tuple] = []

    def upsert_documentation_metrics(self, repository_id, snapshot_date, **kwargs):
        self.calls.append((repository_id, snapshot_date, kwargs))
        result = self._results.get(repository_id)
        if isinstance(result, Exception):
            raise result


FEATURES = ReadmeFeatures(word_count=100, section_count=2, code_example_count=1, detected_sections=("installation", "usage"))
READABILITY = ReadabilityScores(flesch_reading_ease=65.0, flesch_kincaid_grade_level=8.0)
FIXED_DATE = date(2026, 8, 15)


def build_pipeline(candidates, feature_results, readability_results, writer_results=None):
    provider = FakeCandidateProvider(candidates)
    extractor = FakeFeatureExtractor(feature_results)
    scorer = FakeReadabilityScorer(readability_results)
    writer = FakeDocumentationWriter(writer_results)
    pipeline = DocumentationPipeline(
        candidate_provider=provider,
        writer=writer,
        extract_features_fn=extractor,
        compute_readability_fn=scorer,
        snapshot_date_fn=lambda: FIXED_DATE,
    )
    return pipeline, provider, extractor, scorer, writer


def stats(selected, attempted, succeeded, failed):
    return DocumentationAcquisitionStats(
        candidates_selected=selected, attempted=attempted, succeeded=succeeded, failed=failed
    )


# ---------------------------------------------------------------------------
# Basic composition
# ---------------------------------------------------------------------------


class TestBasicRun:
    def test_empty_candidate_set(self):
        pipeline, provider, extractor, scorer, writer = build_pipeline([], {}, {})

        result = pipeline.run()

        assert result.stats == stats(0, 0, 0, 0)
        assert result.processed == ()
        assert result.failures == ()
        assert writer.calls == []

    def test_one_successful_repository(self):
        repo_id = uuid.uuid4()
        candidate = DocumentationCandidate(repo_id, "octocat/Hello-World", "# Hello\nSome README content.")
        pipeline, *_rest = build_pipeline(
            [candidate],
            {candidate.content: FEATURES},
            {candidate.content: READABILITY},
        )

        result = pipeline.run()

        assert result.stats == stats(1, 1, 1, 0)
        assert result.processed == (
            ProcessedRepository(repo_id, "octocat/Hello-World", word_count=100, section_count=2, code_example_count=1, readability_score=65.0),
        )
        assert result.failures == ()

    def test_writer_called_with_expected_arguments(self):
        repo_id = uuid.uuid4()
        candidate = DocumentationCandidate(repo_id, "a/a", "content")
        pipeline, provider, extractor, scorer, writer = build_pipeline(
            [candidate], {candidate.content: FEATURES}, {candidate.content: READABILITY}
        )

        pipeline.run()

        [(called_repo_id, called_date, kwargs)] = writer.calls
        assert called_repo_id == repo_id
        assert called_date == FIXED_DATE
        assert kwargs == {
            "readme_present": True,
            "readme_word_count": 100,
            "readme_section_count": 2,
            "readme_code_example_count": 1,
            "readme_readability_score": 65.0,
        }

    def test_null_readability_score_passed_through(self):
        repo_id = uuid.uuid4()
        candidate = DocumentationCandidate(repo_id, "a/a", "```\nonly code\n```")
        empty_readability = ReadabilityScores(flesch_reading_ease=None, flesch_kincaid_grade_level=None)
        pipeline, *_rest = build_pipeline(
            [candidate], {candidate.content: FEATURES}, {candidate.content: empty_readability}
        )

        result = pipeline.run()

        assert result.processed[0].readability_score is None

    def test_multiple_repositories_processed_in_order(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "content a")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b],
            {"content a": FEATURES, "content b": FEATURES},
            {"content a": READABILITY, "content b": READABILITY},
        )

        result = pipeline.run()

        assert [p.repository_id for p in result.processed] == [repo_a, repo_b]


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_persistence_failure_recorded_and_batch_continues(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "content a")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        pipeline, provider, extractor, scorer, writer = build_pipeline(
            [candidate_a, candidate_b],
            {"content a": FEATURES, "content b": FEATURES},
            {"content a": READABILITY, "content b": READABILITY},
            writer_results={repo_a: DocumentationPersistenceError(repo_a, RuntimeError("db down"))},
        )

        result = pipeline.run()

        assert result.stats == stats(2, 2, 1, 1)
        assert result.failures[0].stage == "persist"
        assert result.failures[0].repository_id == repo_a
        assert result.processed[0].repository_id == repo_b

    def test_unexpected_exception_during_extraction_recorded(self, caplog):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "boom")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b],
            {"boom": RuntimeError("unexpected parser crash"), "content b": FEATURES},
            {"content b": READABILITY},
        )

        with caplog.at_level(logging.ERROR):
            result = pipeline.run()

        assert result.stats == stats(2, 2, 1, 1)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "RuntimeError"
        assert result.processed[0].repository_id == repo_b
        assert any("documentation_pipeline_unexpected_error" in r.message for r in caplog.records)

    def test_unexpected_exception_during_readability_recorded(self):
        repo_id = uuid.uuid4()
        candidate = DocumentationCandidate(repo_id, "a/a", "content")
        pipeline, *_rest = build_pipeline(
            [candidate], {"content": FEATURES}, {"content": RuntimeError("scorer crash")}
        )

        result = pipeline.run()

        assert result.stats == stats(1, 1, 0, 1)
        assert result.failures[0].stage == "unexpected"

    def test_unexpected_exception_from_writer_recorded(self):
        repo_id = uuid.uuid4()
        candidate = DocumentationCandidate(repo_id, "a/a", "content")
        pipeline, *_rest = build_pipeline(
            [candidate],
            {"content": FEATURES},
            {"content": READABILITY},
            writer_results={repo_id: RuntimeError("unexpected db driver crash")},
        )

        result = pipeline.run()

        assert result.stats == stats(1, 1, 0, 1)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "RuntimeError"

    def test_all_repositories_failing(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "content a")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b],
            {"content a": FEATURES, "content b": FEATURES},
            {"content a": READABILITY, "content b": READABILITY},
            writer_results={
                repo_a: DocumentationPersistenceError(repo_a, RuntimeError("down")),
                repo_b: DocumentationPersistenceError(repo_b, RuntimeError("down")),
            },
        )

        result = pipeline.run()

        assert result.stats == stats(2, 2, 0, 2)
        assert result.processed == ()
        assert len(result.failures) == 2

    def test_mixed_success_and_failure_preserves_order(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "content a")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        candidate_c = DocumentationCandidate(repo_c, "c/c", "content c")
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b, candidate_c],
            {"content a": FEATURES, "content b": FEATURES, "content c": FEATURES},
            {"content a": READABILITY, "content b": READABILITY, "content c": READABILITY},
            writer_results={repo_b: DocumentationPersistenceError(repo_b, RuntimeError("down"))},
        )

        result = pipeline.run()

        assert [p.repository_id for p in result.processed] == [repo_a, repo_c]
        assert [f.repository_id for f in result.failures] == [repo_b]

    def test_candidate_provider_failure_propagates_not_caught(self):
        pipeline = DocumentationPipeline(
            candidate_provider=FailingCandidateProvider(),
            writer=FakeDocumentationWriter(),
            extract_features_fn=FakeFeatureExtractor({}),
            compute_readability_fn=FakeReadabilityScorer({}),
        )

        with pytest.raises(RuntimeError, match="candidate query failed"):
            pipeline.run()


# ---------------------------------------------------------------------------
# Statistics invariant
# ---------------------------------------------------------------------------


class TestStatisticsInvariant:
    def test_attempted_equals_succeeded_plus_failed(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        candidate_a = DocumentationCandidate(repo_a, "a/a", "content a")
        candidate_b = DocumentationCandidate(repo_b, "b/b", "content b")
        candidate_c = DocumentationCandidate(repo_c, "c/c", "content c")
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b, candidate_c],
            {"content a": FEATURES, "content b": FEATURES, "content c": FEATURES},
            {"content a": READABILITY, "content b": READABILITY, "content c": READABILITY},
            writer_results={repo_b: DocumentationPersistenceError(repo_b, RuntimeError("down"))},
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
        candidate = DocumentationCandidate(repo_id, "a/a", "content")
        pipeline, *_rest = build_pipeline([candidate], {"content": FEATURES}, {"content": READABILITY})

        with caplog.at_level(logging.INFO):
            pipeline.run()

        messages = [r.message for r in caplog.records]
        assert any("documentation_pipeline_started" in m for m in messages)
        assert any("documentation_pipeline_repository_succeeded" in m for m in messages)
        assert any("documentation_pipeline_completed" in m for m in messages)


# ---------------------------------------------------------------------------
# ReadmeCandidateProvider — its own SQL, tested separately
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
    def test_returns_one_candidate_per_readme_row(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection([(repo_id, "octocat/Hello-World", "# Hello\nContent.")])
        provider = ReadmeCandidateProvider(connection_factory=lambda: conn)

        candidates = provider.get_candidates()

        assert candidates == [DocumentationCandidate(repo_id, "octocat/Hello-World", "# Hello\nContent.")]

    def test_query_filters_on_readme_content_type(self):
        conn = FakeConnection([])
        provider = ReadmeCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        sql, params = conn.cur.executed[0]
        assert "content_type = 'readme'" in sql
        assert "FROM repository_content" in sql
        assert "repository_metrics" not in sql

    def test_connection_closed_after_use(self):
        conn = FakeConnection([])
        provider = ReadmeCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        assert conn.closed is True

    def test_no_readme_rows(self):
        conn = FakeConnection([])
        provider = ReadmeCandidateProvider(connection_factory=lambda: conn)

        assert provider.get_candidates() == []
