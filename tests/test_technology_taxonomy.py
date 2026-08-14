"""Deterministic tests for processing/technology_taxonomy.py — Checkpoint 2.2.b.

No database, no network — pure function tests, mirroring
tests/test_manifest_parser.py's style.
"""

from __future__ import annotations

import pytest

from processing.technology_taxonomy import (
    Classification,
    Technology,
    classify_language,
    classify_package,
)


class TestEcosystemLanguage:
    @pytest.mark.parametrize(
        "ecosystem,slug,name",
        [
            ("npm", "javascript", "JavaScript"),
            ("pypi", "python", "Python"),
            ("cargo", "rust", "Rust"),
            ("go", "go", "Go"),
            ("rubygems", "ruby", "Ruby"),
            ("composer", "php", "PHP"),
        ],
    )
    def test_every_supported_ecosystem_maps_to_a_language(self, ecosystem, slug, name):
        result = classify_language(ecosystem)
        assert result == Classification(Technology(slug, name, "language"), "primary", 1.0)

    def test_unknown_ecosystem_returns_none(self):
        assert classify_language("nuget") is None
        assert classify_language("") is None

    def test_language_role_is_always_primary(self):
        for ecosystem in ("npm", "pypi", "cargo", "go", "rubygems", "composer"):
            assert classify_language(ecosystem).role == "primary"

    def test_language_confidence_is_always_1(self):
        assert classify_language("pypi").confidence == 1.0


class TestKnownPackageMapping:
    def test_flask_is_a_framework(self):
        result = classify_package("flask", "pypi", is_dev=False)
        assert result.technology == Technology("flask", "Flask", "framework")
        assert result.role == "secondary"
        assert result.confidence == 1.0

    def test_fastapi_is_a_framework(self):
        result = classify_package("fastapi", "pypi", is_dev=False)
        assert result.technology.category == "framework"

    def test_pytest_is_a_testing_tool(self):
        result = classify_package("pytest", "pypi", is_dev=True)
        assert result.technology.category == "testing_tool"

    def test_boto3_is_a_platform(self):
        result = classify_package("boto3", "pypi", is_dev=False)
        assert result.technology.category == "platform"

    def test_svelte_is_a_framework_npm(self):
        result = classify_package("svelte", "npm", is_dev=False)
        assert result.technology.category == "framework"


class TestDevRoleMapping:
    def test_is_dev_true_yields_dev_role(self):
        result = classify_package("pytest", "pypi", is_dev=True)
        assert result.role == "dev"

    def test_is_dev_false_yields_secondary_role(self):
        result = classify_package("flask", "pypi", is_dev=False)
        assert result.role == "secondary"

    def test_package_role_is_never_primary(self):
        for is_dev in (True, False):
            for eco, name in (("pypi", "flask"), ("pypi", "pytest"), ("pypi", "boto3"), ("npm", "svelte")):
                assert classify_package(name, eco, is_dev=is_dev).role != "primary"


class TestCaseNormalization:
    def test_uppercase_package_name(self):
        assert classify_package("FLASK", "pypi", is_dev=False) is not None
        assert classify_package("Flask", "pypi", is_dev=False) is not None
        assert classify_package("fLaSk", "pypi", is_dev=False) is not None

    def test_whitespace_stripped(self):
        assert classify_package("  flask  ", "pypi", is_dev=False) is not None


class TestPypiNormalization:
    def test_hyphen_underscore_dot_equivalence(self):
        # Synthetic: prove the normalization rule itself is correct,
        # independent of whether the curated list has a separator-bearing
        # entry today.
        from processing.technology_taxonomy import _normalize

        assert _normalize("Fast_API", "pypi") == _normalize("fast-api", "pypi") == _normalize("fast.api", "pypi")

    def test_non_pypi_ecosystem_not_separator_folded(self):
        from processing.technology_taxonomy import _normalize

        assert _normalize("some_pkg", "npm") == "some_pkg"  # npm is case-fold only, no separator rule


class TestUnknownAndAmbiguousPackages:
    def test_unknown_package_returns_none(self):
        assert classify_package("numpy", "pypi", is_dev=False) is None
        assert classify_package("requests", "pypi", is_dev=False) is None
        assert classify_package("left-pad", "npm", is_dev=False) is None

    def test_client_libraries_are_not_misclassified_as_database(self):
        # Explicit regression guard for this checkpoint's correction:
        # these must NOT be classified, even though they interact with
        # databases/queues.
        for eco, name in (
            ("pypi", "sqlalchemy"),
            ("pypi", "psycopg"),
            ("pypi", "psycopg2"),
            ("pypi", "redis"),
            ("pypi", "pymongo"),
            ("pypi", "celery"),
            ("pypi", "elasticsearch"),
            ("pypi", "kafka-python"),
            ("pypi", "pika"),
            ("pypi", "grpcio"),
        ):
            assert classify_package(name, eco, is_dev=False) is None

    def test_http_client_libraries_are_not_misclassified_as_framework(self):
        assert classify_package("requests", "pypi", is_dev=False) is None
        assert classify_package("httpx", "pypi", is_dev=False) is None

    def test_empty_name_returns_none(self):
        assert classify_package("", "pypi", is_dev=False) is None
        assert classify_package("   ", "pypi", is_dev=False) is None

    def test_known_name_wrong_ecosystem_returns_none(self):
        # "flask" is only curated for pypi - a same-named package in a
        # different ecosystem must not silently inherit the classification.
        assert classify_package("flask", "npm", is_dev=False) is None


class TestDeterminism:
    def test_repeated_classification_is_identical(self):
        first = classify_package("flask", "pypi", is_dev=False)
        second = classify_package("flask", "pypi", is_dev=False)
        assert first == second

    def test_repeated_language_classification_is_identical(self):
        assert classify_language("pypi") == classify_language("pypi")


class TestNoFuzzyMatching:
    def test_near_miss_names_do_not_match(self):
        assert classify_package("flaskk", "pypi", is_dev=False) is None
        assert classify_package("flas", "pypi", is_dev=False) is None
        assert classify_package("flask2", "pypi", is_dev=False) is None


class TestValueValidation:
    def test_technology_rejects_unknown_category(self):
        with pytest.raises(ValueError):
            Technology("x", "X", "library")

    def test_classification_rejects_unknown_role(self):
        with pytest.raises(ValueError):
            Classification(Technology("x", "X", "framework"), "owner", 1.0)

    def test_classification_rejects_non_unity_confidence(self):
        with pytest.raises(ValueError):
            Classification(Technology("x", "X", "framework"), "primary", 0.8)
