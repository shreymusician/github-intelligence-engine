"""Test suite for processing.technology_pipeline — Checkpoint 2.2.c.

Deterministic, mocked tests exercising the real TechnologyPipeline against
fake ManifestCandidateProvider/parse_manifest/classify_language/
classify_package/TechnologyWriter collaborators — no real network, no live
PostgreSQL dependency for normal test execution. Mirrors the style
established in tests/test_content_pipeline.py (Checkpoint 1.4.d):
dict/list-driven fakes with a recorded call log, composed with the real
orchestrator class rather than a "fake of the pipeline itself."

ManifestCandidateProvider's own SQL is tested separately
(TestCandidateProvider below) against a minimal fake psycopg connection,
mirroring tests/test_content_pipeline.py's TestCandidateProvider pattern —
kept distinct from the pipeline tests since it is genuinely new read-only
SQL this checkpoint introduces, not orchestration logic.
"""

from __future__ import annotations

import logging
import uuid

import pytest

from processing.exceptions import TechnologyPersistenceError
from processing.manifest_parser import ManifestParseResult, ParsedDependency
from processing.technology_pipeline import (
    ManifestCandidateProvider,
    ManifestRow,
    ProcessedRepository,
    TechnologyAcquisitionStats,
    TechnologyCandidate,
    TechnologyPipeline,
)
from processing.technology_taxonomy import Classification, Technology

# ---------------------------------------------------------------------------
# Fakes — mirror tests/test_content_pipeline.py's dict/list-driven, call-recording style
# ---------------------------------------------------------------------------


class FakeCandidateProvider:
    def __init__(self, candidates: list[TechnologyCandidate]) -> None:
        self._candidates = candidates
        self.calls = 0

    def get_candidates(self) -> list[TechnologyCandidate]:
        self.calls += 1
        return self._candidates


class FailingCandidateProvider:
    def get_candidates(self):
        raise RuntimeError("candidate query failed")


class FakeParser:
    """Fake parse_manifest() — driven by (content_type, content) ->
    ManifestParseResult-or-exception. Records every call, in order."""

    def __init__(self, results: dict[tuple[str, str], object]) -> None:
        self._results = results
        self.calls: list[tuple[str, str]] = []

    def __call__(self, content_type: str, content: str) -> ManifestParseResult:
        key = (content_type, content)
        self.calls.append(key)
        result = self._results[key]
        if isinstance(result, Exception):
            raise result
        return result


class FakeLanguageClassifier:
    def __init__(self, results: dict[str, object] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    def __call__(self, ecosystem: str) -> Classification | None:
        self.calls.append(ecosystem)
        result = self._results.get(ecosystem)
        if isinstance(result, Exception):
            raise result
        return result


class FakePackageClassifier:
    def __init__(self, results: dict[tuple[str, str], object] | None = None) -> None:
        self._results = results or {}
        self.calls: list[tuple[str, str, bool]] = []

    def __call__(self, name: str, ecosystem: str, *, is_dev: bool) -> Classification | None:
        self.calls.append((name, ecosystem, is_dev))
        result = self._results.get((name, ecosystem))
        if isinstance(result, Exception):
            raise result
        return result


class FakeTechnologyWriter:
    """Fake TechnologyWriter.upsert_technologies() — driven by
    repository_id -> ids-or-exception. Records every call, in order."""

    def __init__(self, results_by_repo_id: dict[uuid.UUID, object] | None = None) -> None:
        self._results = results_by_repo_id or {}
        self.calls: list[tuple[uuid.UUID, list[Classification]]] = []

    def upsert_technologies(self, repository_id: uuid.UUID, classifications: list[Classification]) -> list[uuid.UUID]:
        self.calls.append((repository_id, classifications))
        result = self._results.get(repository_id, [uuid.uuid4() for _ in classifications])
        if isinstance(result, Exception):
            raise result
        return result


LANGUAGE = Classification(technology=Technology("python", "Python", "language"), role="primary", confidence=1.0)
FRAMEWORK = Classification(technology=Technology("flask", "Flask", "framework"), role="secondary", confidence=1.0)


def make_dependency(name="flask", ecosystem="pypi", version=None, is_dev=False) -> ParsedDependency:
    return ParsedDependency(name=name, ecosystem=ecosystem, version_constraint=version, is_dev=is_dev)


def build_pipeline(candidates, parser_results, language_results=None, package_results=None, writer_results=None):
    provider = FakeCandidateProvider(candidates)
    parser = FakeParser(parser_results)
    language_classifier = FakeLanguageClassifier(language_results)
    package_classifier = FakePackageClassifier(package_results)
    writer = FakeTechnologyWriter(writer_results)
    pipeline = TechnologyPipeline(
        candidate_provider=provider,
        writer=writer,
        parse_manifest_fn=parser,
        classify_language_fn=language_classifier,
        classify_package_fn=package_classifier,
    )
    return pipeline, provider, parser, language_classifier, package_classifier, writer


def stats(selected, attempted, succeeded, failed, manifests=0, deps=0, errors=0, technologies=0):
    return TechnologyAcquisitionStats(
        candidates_selected=selected,
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        manifests_processed=manifests,
        dependencies_parsed=deps,
        parse_errors=errors,
        technologies_persisted=technologies,
    )


# ---------------------------------------------------------------------------
# Basic composition / empty / unclassified cases
# ---------------------------------------------------------------------------


class TestBasicRun:
    def test_empty_candidate_set(self):
        pipeline, provider, parser, lang, pkg, writer = build_pipeline([], {})

        result = pipeline.run()

        assert result.stats == stats(0, 0, 0, 0)
        assert result.processed == ()
        assert result.failures == ()
        assert writer.calls == []

    def test_one_successful_repository(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id,
            full_name="octocat/Hello-World",
            manifests=(ManifestRow("requirements_txt", "flask==2.0"),),
        )
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
        )

        result = pipeline.run()

        assert result.stats == stats(1, 1, 1, 0, manifests=1, deps=1, technologies=2)
        assert result.processed == (
            ProcessedRepository(repo_id, "octocat/Hello-World", manifests_processed=1, dependencies_parsed=1, parse_errors=0, technologies_persisted=2),
        )
        assert result.failures == ()

    def test_repository_with_manifest_but_zero_dependencies_is_not_an_error(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id, full_name="empty/manifest", manifests=(ManifestRow("package_json", "{}"),)
        )
        pipeline, *_rest = build_pipeline(
            [candidate], {("package_json", "{}"): ManifestParseResult((), None)}
        )

        result = pipeline.run()

        assert result.stats.succeeded == 1
        assert result.stats.failed == 0
        assert result.processed[0].dependencies_parsed == 0
        assert result.processed[0].technologies_persisted == 0

    def test_malformed_manifest_records_parse_error_not_a_failure(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id, full_name="broken/manifest", manifests=(ManifestRow("package_json", "{bad json"),)
        )
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("package_json", "{bad json"): ManifestParseResult((), "JSONDecodeError: bad json")},
        )

        result = pipeline.run()

        assert result.stats.succeeded == 1
        assert result.stats.failed == 0
        assert result.stats.parse_errors == 1
        assert result.processed[0].parse_errors == 1
        assert result.processed[0].technologies_persisted == 0

    def test_dependencies_with_no_taxonomy_match_are_not_an_error(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id, full_name="unknown/deps", manifests=(ManifestRow("requirements_txt", "numpy==1.0"),)
        )
        dep = make_dependency(name="numpy")
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("requirements_txt", "numpy==1.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={},  # numpy classifies to None
        )

        result = pipeline.run()

        assert result.stats.succeeded == 1
        # language is still classified (ecosystem observed via an actual dependency)
        assert result.processed[0].technologies_persisted == 1

    def test_mixed_recognized_and_unrecognized_technologies_persists_recognized_only(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id,
            full_name="mixed/deps",
            manifests=(ManifestRow("requirements_txt", "flask==2.0\nnumpy==1.0"),),
        )
        dep_flask = make_dependency(name="flask")
        dep_numpy = make_dependency(name="numpy")
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("requirements_txt", "flask==2.0\nnumpy==1.0"): ManifestParseResult((dep_flask, dep_numpy), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
        )

        result = pipeline.run()

        # language (once) + flask; numpy contributes nothing
        assert result.processed[0].technologies_persisted == 2

    def test_multiple_manifests_same_ecosystem_classifies_language_once(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id,
            full_name="two/manifests",
            manifests=(
                ManifestRow("requirements_txt", "flask==2.0"),
                ManifestRow("pyproject_toml", "[project]\ndependencies=[]"),
            ),
        )
        dep = make_dependency(name="flask")
        pipeline, provider, parser, lang, pkg, writer = build_pipeline(
            [candidate],
            {
                ("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None),
                ("pyproject_toml", "[project]\ndependencies=[]"): ManifestParseResult((), None),
            },
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
        )

        result = pipeline.run()

        assert lang.calls == ["pypi"]  # classified once, not twice
        assert result.processed[0].manifests_processed == 2
        assert result.processed[0].technologies_persisted == 2

    def test_no_manifest_existence_does_not_manufacture_a_language(self):
        """A manifest that parses to zero dependencies contributes no
        language classification either — ecosystem is only derived from
        actually-parsed ParsedDependency rows (see module docstring)."""
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(
            repository_id=repo_id, full_name="empty/manifest", manifests=(ManifestRow("cargo_toml", ""),)
        )
        pipeline, provider, parser, lang, pkg, writer = build_pipeline(
            [candidate], {("cargo_toml", ""): ManifestParseResult((), None)}
        )

        result = pipeline.run()

        assert lang.calls == []
        assert result.processed[0].technologies_persisted == 0


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    def test_persistence_failure_recorded_and_batch_continues(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = TechnologyCandidate(repo_a, "a/a", (ManifestRow("requirements_txt", "flask==2.0"),))
        candidate_b = TechnologyCandidate(repo_b, "b/b", (ManifestRow("requirements_txt", "flask==2.0"),))
        dep = make_dependency()
        pipeline, provider, parser, lang, pkg, writer = build_pipeline(
            [candidate_a, candidate_b],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
            writer_results={repo_a: TechnologyPersistenceError(repo_a, RuntimeError("db down"))},
        )

        result = pipeline.run()

        assert result.stats == stats(2, 2, 1, 1, manifests=2, deps=2, technologies=2)
        assert result.failures[0].stage == "persist"
        assert result.failures[0].repository_id == repo_a
        assert result.failures[0].manifests_processed == 1
        assert result.failures[0].dependencies_parsed == 1
        assert result.processed[0].repository_id == repo_b

    def test_unexpected_exception_during_parse_recorded_and_batch_continues(self, caplog):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = TechnologyCandidate(repo_a, "a/a", (ManifestRow("requirements_txt", "boom"),))
        candidate_b = TechnologyCandidate(repo_b, "b/b", (ManifestRow("requirements_txt", "flask==2.0"),))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b],
            {
                ("requirements_txt", "boom"): RuntimeError("unexpected parser crash"),
                ("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None),
            },
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
        )

        with caplog.at_level(logging.ERROR):
            result = pipeline.run()

        assert result.stats == stats(2, 2, 1, 1, manifests=1, deps=1, technologies=2)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "RuntimeError"
        assert result.processed[0].repository_id == repo_b
        assert any("technology_pipeline_unexpected_error" in r.message for r in caplog.records)

    def test_unexpected_exception_from_writer_recorded(self):
        repo_id = uuid.uuid4()
        candidate = TechnologyCandidate(repo_id, "a/a", (ManifestRow("requirements_txt", "flask==2.0"),))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
            writer_results={repo_id: RuntimeError("unexpected db driver crash")},
        )

        result = pipeline.run()

        assert result.stats == stats(1, 1, 0, 1, manifests=1, deps=1)
        assert result.failures[0].stage == "unexpected"
        assert result.failures[0].error_type == "RuntimeError"

    def test_all_repositories_failing(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        candidate_a = TechnologyCandidate(repo_a, "a/a", (ManifestRow("requirements_txt", "flask==2.0"),))
        candidate_b = TechnologyCandidate(repo_b, "b/b", (ManifestRow("requirements_txt", "flask==2.0"),))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
            writer_results={
                repo_a: TechnologyPersistenceError(repo_a, RuntimeError("down")),
                repo_b: TechnologyPersistenceError(repo_b, RuntimeError("down")),
            },
        )

        result = pipeline.run()

        assert result.stats == stats(2, 2, 0, 2, manifests=2, deps=2)
        assert result.processed == ()
        assert len(result.failures) == 2

    def test_mixed_success_and_failure_preserves_order(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        row = ManifestRow("requirements_txt", "flask==2.0")
        candidate_a = TechnologyCandidate(repo_a, "a/a", (row,))
        candidate_b = TechnologyCandidate(repo_b, "b/b", (row,))
        candidate_c = TechnologyCandidate(repo_c, "c/c", (row,))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b, candidate_c],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
            writer_results={repo_b: TechnologyPersistenceError(repo_b, RuntimeError("down"))},
        )

        result = pipeline.run()

        assert [p.repository_id for p in result.processed] == [repo_a, repo_c]
        assert [f.repository_id for f in result.failures] == [repo_b]

    def test_candidate_provider_failure_propagates_not_caught(self):
        pipeline = TechnologyPipeline(
            candidate_provider=FailingCandidateProvider(),
            writer=FakeTechnologyWriter(),
            parse_manifest_fn=FakeParser({}),
            classify_language_fn=FakeLanguageClassifier(),
            classify_package_fn=FakePackageClassifier(),
        )

        with pytest.raises(RuntimeError, match="candidate query failed"):
            pipeline.run()


# ---------------------------------------------------------------------------
# Statistics invariant
# ---------------------------------------------------------------------------


class TestStatisticsInvariant:
    def test_attempted_equals_succeeded_plus_failed(self):
        repo_a, repo_b, repo_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        row = ManifestRow("requirements_txt", "flask==2.0")
        candidate_a = TechnologyCandidate(repo_a, "a/a", (row,))
        candidate_b = TechnologyCandidate(repo_b, "b/b", (row,))
        candidate_c = TechnologyCandidate(repo_c, "c/c", (row,))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate_a, candidate_b, candidate_c],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
            writer_results={repo_b: TechnologyPersistenceError(repo_b, RuntimeError("down"))},
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
        candidate = TechnologyCandidate(repo_id, "a/a", (ManifestRow("requirements_txt", "flask==2.0"),))
        dep = make_dependency()
        pipeline, *_rest = build_pipeline(
            [candidate],
            {("requirements_txt", "flask==2.0"): ManifestParseResult((dep,), None)},
            language_results={"pypi": LANGUAGE},
            package_results={("flask", "pypi"): FRAMEWORK},
        )

        with caplog.at_level(logging.INFO):
            pipeline.run()

        messages = [r.message for r in caplog.records]
        assert any("technology_pipeline_started" in m for m in messages)
        assert any("technology_pipeline_repository_succeeded" in m for m in messages)
        assert any("technology_pipeline_completed" in m for m in messages)


# ---------------------------------------------------------------------------
# ManifestCandidateProvider — its own SQL, tested separately
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
    def test_groups_manifest_rows_by_repository(self):
        repo_id = uuid.uuid4()
        conn = FakeConnection(
            [
                (repo_id, "octocat/Hello-World", "requirements_txt", "flask==2.0"),
                (repo_id, "octocat/Hello-World", "pyproject_toml", "[project]"),
            ]
        )
        provider = ManifestCandidateProvider(connection_factory=lambda: conn)

        candidates = provider.get_candidates()

        assert len(candidates) == 1
        assert candidates[0].repository_id == repo_id
        assert candidates[0].full_name == "octocat/Hello-World"
        assert candidates[0].manifests == (
            ManifestRow("requirements_txt", "flask==2.0"),
            ManifestRow("pyproject_toml", "[project]"),
        )

    def test_multiple_repositories_kept_separate(self):
        repo_a, repo_b = uuid.uuid4(), uuid.uuid4()
        conn = FakeConnection(
            [
                (repo_a, "a/a", "requirements_txt", "flask==2.0"),
                (repo_b, "b/b", "package_json", "{}"),
            ]
        )
        provider = ManifestCandidateProvider(connection_factory=lambda: conn)

        candidates = provider.get_candidates()

        assert [c.repository_id for c in candidates] == [repo_a, repo_b]

    def test_query_filters_by_supported_content_types_no_content_exclusion(self):
        conn = FakeConnection([])
        provider = ManifestCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        sql, params = conn.cur.executed[0]
        assert "FROM repository_content" in sql
        assert "content_type = ANY" in sql
        assert "repository_technologies" not in sql
        assert params is not None and "content_types" in params

    def test_connection_closed_after_use(self):
        conn = FakeConnection([])
        provider = ManifestCandidateProvider(connection_factory=lambda: conn)

        provider.get_candidates()

        assert conn.closed is True

    def test_no_manifest_rows(self):
        conn = FakeConnection([])
        provider = ManifestCandidateProvider(connection_factory=lambda: conn)

        assert provider.get_candidates() == []
