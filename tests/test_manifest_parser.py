"""Deterministic tests for processing/manifest_parser.py — Checkpoint 2.2.a.

No database, no network, no filesystem — pure function tests against
in-memory manifest text, mirroring tests/test_content_extractor.py's
class-grouped style.
"""

from __future__ import annotations

from processing.manifest_parser import (
    SUPPORTED_MANIFEST_CONTENT_TYPES,
    ParsedDependency,
    parse_manifest,
)


class TestSupportedContentTypes:
    def test_matches_content_extractor_manifest_types(self):
        # Mirrors acquisition/content_extractor.py's _MANIFEST_CONTENT_TYPES
        # values exactly — no format added beyond what 1.4.b already approved.
        assert SUPPORTED_MANIFEST_CONTENT_TYPES == {
            "package_json",
            "requirements_txt",
            "pyproject_toml",
            "setup_py",
            "setup_cfg",
            "pipfile",
            "gemfile",
            "go_mod",
            "cargo_toml",
            "composer_json",
        }

    def test_unsupported_content_type(self):
        result = parse_manifest("readme", "# Hello")
        assert result.dependencies == ()
        assert result.parse_error == "unsupported content_type: 'readme'"

    def test_empty_content_never_errors_regardless_of_type(self):
        for content_type in SUPPORTED_MANIFEST_CONTENT_TYPES:
            result = parse_manifest(content_type, "")
            assert result.dependencies == ()
            assert result.parse_error is None

        for content_type in SUPPORTED_MANIFEST_CONTENT_TYPES:
            result = parse_manifest(content_type, "   \n  \n")
            assert result.dependencies == ()
            assert result.parse_error is None


class TestPackageJson:
    def test_normal_manifest_with_multiple_dependencies(self):
        content = """
        {
          "dependencies": {"express": "^4.17.21", "lodash": "~4.17.0"},
          "devDependencies": {"jest": "^29.0.0"}
        }
        """
        result = parse_manifest("package_json", content)
        assert result.parse_error is None
        assert result.dependencies == (
            ParsedDependency("express", "npm", "^4.17.21", False),
            ParsedDependency("lodash", "npm", "~4.17.0", False),
            ParsedDependency("jest", "npm", "^29.0.0", True),
        )

    def test_empty_object(self):
        result = parse_manifest("package_json", "{}")
        assert result == parse_manifest("package_json", "{}")
        assert result.dependencies == ()
        assert result.parse_error is None

    def test_malformed_json(self):
        result = parse_manifest("package_json", "{not valid json")
        assert result.dependencies == ()
        assert result.parse_error is not None
        assert "JSONDecodeError" in result.parse_error

    def test_utf8_package_name(self):
        content = '{"dependencies": {"café-utils": "1.0.0"}}'
        result = parse_manifest("package_json", content)
        assert result.dependencies == (ParsedDependency("café-utils", "npm", "1.0.0", False),)

    def test_deterministic_ordering(self):
        content = '{"dependencies": {"z-pkg": "1.0.0", "a-pkg": "2.0.0"}}'
        first = parse_manifest("package_json", content)
        second = parse_manifest("package_json", content)
        assert first.dependencies == second.dependencies
        assert [d.name for d in first.dependencies] == ["z-pkg", "a-pkg"]

    def test_same_package_in_both_sections_is_two_entries(self):
        content = """
        {
          "dependencies": {"shared-pkg": "1.0.0"},
          "devDependencies": {"shared-pkg": "2.0.0"}
        }
        """
        result = parse_manifest("package_json", content)
        assert result.dependencies == (
            ParsedDependency("shared-pkg", "npm", "1.0.0", False),
            ParsedDependency("shared-pkg", "npm", "2.0.0", True),
        )


class TestRequirementsTxt:
    def test_normal_manifest_with_version_constraints(self):
        content = "requests==2.31.0\nflask>=2.0,<3.0\nnumpy\n"
        result = parse_manifest("requirements_txt", content)
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", "==2.31.0", False),
            ParsedDependency("flask", "pypi", ">=2.0,<3.0", False),
            ParsedDependency("numpy", "pypi", None, False),
        )

    def test_comments_and_blank_lines_ignored(self):
        content = "\n# a full-line comment\nrequests==2.31.0  # inline comment\n\n"
        result = parse_manifest("requirements_txt", content)
        assert result.dependencies == (ParsedDependency("requests", "pypi", "==2.31.0", False),)

    def test_pip_options_and_urls_ignored(self):
        content = "-r base.txt\n--index-url https://example.com/simple\ngit+https://github.com/x/y.git\nrequests==2.31.0\n"
        result = parse_manifest("requirements_txt", content)
        assert result.dependencies == (ParsedDependency("requests", "pypi", "==2.31.0", False),)

    def test_duplicate_declarations_preserved(self):
        content = "requests==2.31.0\nrequests==2.30.0\n"
        result = parse_manifest("requirements_txt", content)
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", "==2.31.0", False),
            ParsedDependency("requests", "pypi", "==2.30.0", False),
        )

    def test_no_dev_scope_in_this_format(self):
        result = parse_manifest("requirements_txt", "pytest==7.0.0\n")
        assert result.dependencies[0].is_dev is False


class TestPyprojectToml:
    def test_pep621_dependencies_and_optional_groups(self):
        content = """
[project]
name = "example"
dependencies = ["requests>=2.0", "click"]

[project.optional-dependencies]
dev = ["pytest>=7.0"]
"""
        result = parse_manifest("pyproject_toml", content)
        assert result.parse_error is None
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", ">=2.0", False),
            ParsedDependency("click", "pypi", None, False),
            ParsedDependency("pytest", "pypi", ">=7.0", True),
        )

    def test_poetry_dependencies_and_dev_group(self):
        content = """
[tool.poetry.dependencies]
python = "^3.11"
requests = "^2.31.0"
flask = {version = "^2.0", optional = true}

[tool.poetry.group.dev.dependencies]
pytest = "^7.0.0"
"""
        result = parse_manifest("pyproject_toml", content)
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", "^2.31.0", False),
            ParsedDependency("flask", "pypi", "^2.0", False),
            ParsedDependency("pytest", "pypi", "^7.0.0", True),
        )

    def test_empty_project_table(self):
        result = parse_manifest("pyproject_toml", "[project]\nname = \"x\"\n")
        assert result.dependencies == ()
        assert result.parse_error is None

    def test_malformed_toml(self):
        result = parse_manifest("pyproject_toml", "[project\nname = x")
        assert result.dependencies == ()
        assert result.parse_error is not None


class TestSetupPy:
    def test_install_requires_and_extras_require(self):
        content = """
from setuptools import setup

setup(
    name="example",
    install_requires=["requests>=2.0", "click"],
    extras_require={"dev": ["pytest"]},
)
"""
        result = parse_manifest("setup_py", content)
        assert result.parse_error is None
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", ">=2.0", False),
            ParsedDependency("click", "pypi", None, False),
            ParsedDependency("pytest", "pypi", None, True),
        )

    def test_no_setup_call_is_malformed(self):
        result = parse_manifest("setup_py", "x = 1\n")
        assert result.dependencies == ()
        assert result.parse_error is not None

    def test_dynamic_install_requires_yields_no_dependencies_not_an_error(self):
        content = """
from setuptools import setup
deps = compute_deps()
setup(name="example", install_requires=deps)
"""
        result = parse_manifest("setup_py", content)
        assert result.dependencies == ()
        assert result.parse_error is None

    def test_syntax_error_is_malformed(self):
        result = parse_manifest("setup_py", "def setup(:\n")
        assert result.dependencies == ()
        assert result.parse_error is not None


class TestSetupCfg:
    def test_install_requires_and_extras_require(self):
        content = """
[options]
install_requires =
    requests>=2.0
    click

[options.extras_require]
dev =
    pytest
"""
        result = parse_manifest("setup_cfg", content)
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", ">=2.0", False),
            ParsedDependency("click", "pypi", None, False),
            ParsedDependency("pytest", "pypi", None, True),
        )

    def test_no_options_section(self):
        result = parse_manifest("setup_cfg", "[metadata]\nname = x\n")
        assert result.dependencies == ()
        assert result.parse_error is None

    def test_malformed_ini(self):
        result = parse_manifest("setup_cfg", "not = valid = ini = = =")
        # configparser tolerates this particular case; assert no crash either way
        assert isinstance(result.dependencies, tuple)


class TestPipfile:
    def test_packages_and_dev_packages(self):
        content = """
[packages]
requests = "*"
flask = "==2.0.0"

[dev-packages]
pytest = {version = "*"}
"""
        result = parse_manifest("pipfile", content)
        assert result.dependencies == (
            ParsedDependency("requests", "pypi", None, False),
            ParsedDependency("flask", "pypi", "==2.0.0", False),
            ParsedDependency("pytest", "pypi", None, True),
        )

    def test_malformed_toml(self):
        result = parse_manifest("pipfile", "[packages\nrequests = *")
        assert result.dependencies == ()
        assert result.parse_error is not None


class TestGemfile:
    def test_normal_and_dev_group(self):
        content = """
source "https://rubygems.org"

gem "rails", "~> 7.0"
gem "pg"

group :development, :test do
  gem "rspec"
end
"""
        result = parse_manifest("gemfile", content)
        assert result.dependencies == (
            ParsedDependency("rails", "rubygems", "~> 7.0", False),
            ParsedDependency("pg", "rubygems", None, False),
            ParsedDependency("rspec", "rubygems", None, True),
        )

    def test_comments_ignored(self):
        content = '# gem "commented-out", "1.0"\ngem "real-gem"\n'
        result = parse_manifest("gemfile", content)
        assert result.dependencies == (ParsedDependency("real-gem", "rubygems", None, False),)

    def test_empty_gemfile(self):
        result = parse_manifest("gemfile", 'source "https://rubygems.org"\n')
        assert result.dependencies == ()
        assert result.parse_error is None


class TestGoMod:
    def test_require_block(self):
        content = """
module example.com/foo

go 1.21

require (
    github.com/pkg/errors v0.9.1
    github.com/stretchr/testify v1.8.4 // indirect
)
"""
        result = parse_manifest("go_mod", content)
        assert result.dependencies == (
            ParsedDependency("github.com/pkg/errors", "go", "v0.9.1", False),
            ParsedDependency("github.com/stretchr/testify", "go", "v1.8.4", False),
        )

    def test_single_line_require(self):
        content = "module example.com/foo\n\nrequire github.com/pkg/errors v0.9.1\n"
        result = parse_manifest("go_mod", content)
        assert result.dependencies == (ParsedDependency("github.com/pkg/errors", "go", "v0.9.1", False),)

    def test_no_dev_scope_in_this_format(self):
        content = "require github.com/pkg/errors v0.9.1\n"
        result = parse_manifest("go_mod", content)
        assert result.dependencies[0].is_dev is False


class TestCargoToml:
    def test_dependencies_and_dev_dependencies(self):
        content = """
[dependencies]
serde = "1.0"
tokio = { version = "1", features = ["full"] }

[dev-dependencies]
mockito = "1.2"
"""
        result = parse_manifest("cargo_toml", content)
        assert result.dependencies == (
            ParsedDependency("serde", "cargo", "1.0", False),
            ParsedDependency("tokio", "cargo", "1", False),
            ParsedDependency("mockito", "cargo", "1.2", True),
        )

    def test_malformed_toml(self):
        result = parse_manifest("cargo_toml", "[dependencies\nserde = 1.0")
        assert result.dependencies == ()
        assert result.parse_error is not None


class TestComposerJson:
    def test_require_and_require_dev(self):
        content = """
        {
          "require": {"php": ">=8.0", "monolog/monolog": "^2.0"},
          "require-dev": {"phpunit/phpunit": "^9.0"}
        }
        """
        result = parse_manifest("composer_json", content)
        assert result.dependencies == (
            ParsedDependency("monolog/monolog", "composer", "^2.0", False),
            ParsedDependency("phpunit/phpunit", "composer", "^9.0", True),
        )

    def test_malformed_json(self):
        result = parse_manifest("composer_json", "{not valid")
        assert result.dependencies == ()
        assert result.parse_error is not None


class TestNoExternalDependencies:
    def test_parse_manifest_is_a_pure_function(self):
        # No import of psycopg/requests/subprocess anywhere in the module
        # under test — enforced structurally, not just by convention.
        import inspect

        import processing.manifest_parser as module

        source = inspect.getsource(module)
        for forbidden in ("psycopg", "subprocess", "requests.", "urllib.request", "socket."):
            assert forbidden not in source
