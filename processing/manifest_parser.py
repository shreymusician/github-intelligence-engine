"""Deterministic manifest parsing — Checkpoint 2.2.a.

Given the `content_type` and raw text of one already-persisted
`repository_content` row (Checkpoint 1.4.b/1.4.c), parses declared
package/dependency declarations into a small, normalized intermediate
representation. This module never calls GitHub, never touches
PostgreSQL, never classifies technologies, and never raises for
malformed input - a parse failure is reported as data (`parse_error`
on the result), matching 1.4.b's "skip and log, don't crash" outcome
for a layer that (unlike 1.4.b) has no batch/orchestration context of
its own yet - that's Checkpoint 2.2.c's job.

Explicitly NOT this module's job (later sub-checkpoints):
- Mapping raw package names to the curated `technologies` taxonomy,
  or deciding a technology's category (2.2.b).
- Persisting anything to PostgreSQL (2.2.b/2.2.c).
- Orchestrating this across many repositories (2.2.c).
- Querying package registries for transitive dependencies or
  vulnerability data (Checkpoint 2.5, out of scope here).
- Any AI/LLM/embedding-based analysis.

Supported manifests are exactly the ones `acquisition/content_extractor.py`
(Checkpoint 1.4.b) already recognizes and persists - no format is added
here that isn't already an approved `repository_content.content_type`
value. Some of these (Pipfile, Gemfile, go.mod, composer.json) have zero
rows in the current 92-repository corpus but are still in scope: they
are already-approved formats, not new ones invented for this checkpoint.

Every parser is stdlib-only (json, tomllib, configparser, ast, re) - no
new third-party dependency was needed for 2.2.a.
"""

from __future__ import annotations

import ast
import configparser
import json
import re
import tomllib
from dataclasses import dataclass
from typing import Callable

_PACKAGE_JSON = "package_json"
_REQUIREMENTS_TXT = "requirements_txt"
_PYPROJECT_TOML = "pyproject_toml"
_SETUP_PY = "setup_py"
_SETUP_CFG = "setup_cfg"
_PIPFILE = "pipfile"
_GEMFILE = "gemfile"
_GO_MOD = "go_mod"
_CARGO_TOML = "cargo_toml"
_COMPOSER_JSON = "composer_json"


@dataclass(frozen=True)
class ParsedDependency:
    """One dependency declaration found in a manifest.

    Fields are exactly what Checkpoint 2.2.b needs to map a raw
    declaration onto the curated `technologies` taxonomy and a
    `repository_technologies.role` ('primary'/'secondary'/'dev') - no
    transitive-resolution or registry data (Checkpoint 2.5's job, not
    this one) is stored here.
    """

    name: str
    ecosystem: str  # 'npm' | 'pypi' | 'cargo' | 'go' | 'rubygems' | 'composer'
    version_constraint: str | None
    is_dev: bool


@dataclass(frozen=True)
class ManifestParseResult:
    """The outcome of parsing one manifest's content.

    `parse_error` is `None` on success (including a manifest that
    parses cleanly but declares zero dependencies). It is set, and
    `dependencies` is empty, when the content could not be parsed as
    its declared format - this never raises, so a caller iterating
    over many manifests (2.2.c) can treat every result uniformly.
    """

    dependencies: tuple[ParsedDependency, ...]
    parse_error: str | None


_PEP508_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*?)\s*$")


def _split_pep508(spec: str) -> tuple[str, str | None]:
    """Split a PEP 508-ish requirement string into (name, version_constraint).

    Drops any environment marker (the part after `;`). Not a full PEP
    508 parser - just enough to recover name and raw version text from
    the strings these manifest formats actually contain.
    """
    spec = spec.split(";", 1)[0].strip()
    match = _PEP508_NAME_RE.match(spec)
    if not match:
        return spec, None
    name, rest = match.groups()
    return name, (rest.strip() or None)


def _parse_package_json(content: str) -> list[ParsedDependency]:
    data = json.loads(content)
    deps: list[ParsedDependency] = []
    if not isinstance(data, dict):
        return deps
    for section, is_dev in (("dependencies", False), ("devDependencies", True)):
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for name, version in section_data.items():
            deps.append(
                ParsedDependency(
                    name=name,
                    ecosystem="npm",
                    version_constraint=str(version) if version is not None else None,
                    is_dev=is_dev,
                )
            )
    return deps


_REQUIREMENTS_LINE_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s*\[[^\]]*\])?\s*(.*?)\s*$")


def _parse_requirements_txt(content: str) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            continue  # pip options: -r, -e, --index-url, etc. - not a dependency declaration
        if "://" in line or line.startswith("git+"):
            continue  # VCS/URL requirements have no stable (name, version) pair to extract
        match = _REQUIREMENTS_LINE_RE.match(line)
        if not match:
            continue
        name, version_part = match.groups()
        deps.append(
            ParsedDependency(
                name=name,
                ecosystem="pypi",
                version_constraint=version_part or None,
                is_dev=False,
            )
        )
    return deps


def _parse_poetry_section(section: object, *, is_dev: bool) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    if not isinstance(section, dict):
        return deps
    for name, spec in section.items():
        if name == "python":
            continue  # the interpreter constraint itself, not a dependency
        if isinstance(spec, str):
            version = spec or None
        elif isinstance(spec, dict):
            version = spec.get("version")
        else:
            version = None
        deps.append(ParsedDependency(name, "pypi", version, is_dev))
    return deps


def _parse_pyproject_toml(content: str) -> list[ParsedDependency]:
    data = tomllib.loads(content)
    deps: list[ParsedDependency] = []

    project = data.get("project")
    if isinstance(project, dict):
        for spec in project.get("dependencies") or []:
            if isinstance(spec, str):
                name, version = _split_pep508(spec)
                deps.append(ParsedDependency(name, "pypi", version, False))
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group_specs in optional.values():
                if not isinstance(group_specs, list):
                    continue
                for spec in group_specs:
                    if isinstance(spec, str):
                        name, version = _split_pep508(spec)
                        deps.append(ParsedDependency(name, "pypi", version, True))

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            deps.extend(_parse_poetry_section(poetry.get("dependencies"), is_dev=False))
            deps.extend(_parse_poetry_section(poetry.get("dev-dependencies"), is_dev=True))
            group = poetry.get("group")
            if isinstance(group, dict):
                for group_data in group.values():
                    if isinstance(group_data, dict):
                        deps.extend(_parse_poetry_section(group_data.get("dependencies"), is_dev=True))

    return deps


def _extract_string_list(node: ast.expr, *, is_dev: bool) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    if isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                name, version = _split_pep508(element.value)
                deps.append(ParsedDependency(name, "pypi", version, is_dev))
    return deps


def _extract_dict_of_lists(node: ast.expr) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    if isinstance(node, ast.Dict):
        for value in node.values:
            deps.extend(_extract_string_list(value, is_dev=True))
    return deps


def _parse_setup_py(content: str) -> list[ParsedDependency]:
    """Statically locate `install_requires`/`extras_require` inside a
    `setup(...)` call via `ast` - the file is never executed. If
    `install_requires`/`extras_require` is not a literal list/dict (e.g.
    computed at runtime), it contributes no dependencies but is not
    treated as an error - that is a real limitation of static analysis,
    not malformed input.
    """
    tree = ast.parse(content)
    deps: list[ParsedDependency] = []
    found_setup_call = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_setup_call = (isinstance(func, ast.Name) and func.id == "setup") or (
            isinstance(func, ast.Attribute) and func.attr == "setup"
        )
        if not is_setup_call:
            continue
        found_setup_call = True
        for keyword in node.keywords:
            if keyword.arg == "install_requires":
                deps.extend(_extract_string_list(keyword.value, is_dev=False))
            elif keyword.arg == "extras_require":
                deps.extend(_extract_dict_of_lists(keyword.value))
        break  # only the first setup(...)/setuptools.setup(...) call is meaningful

    if not found_setup_call:
        raise ValueError("no setup() call found")
    return deps


def _parse_line_list(raw: str, *, is_dev: bool) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    for line in raw.splitlines():
        entry = line.strip().strip(";").strip()
        if not entry:
            continue
        name, version = _split_pep508(entry)
        deps.append(ParsedDependency(name, "pypi", version, is_dev))
    return deps


def _parse_setup_cfg(content: str) -> list[ParsedDependency]:
    parser = configparser.ConfigParser()
    parser.read_string(content)
    deps: list[ParsedDependency] = []

    if parser.has_option("options", "install_requires"):
        deps.extend(_parse_line_list(parser.get("options", "install_requires"), is_dev=False))

    if parser.has_section("options.extras_require"):
        for _group, raw in parser.items("options.extras_require"):
            deps.extend(_parse_line_list(raw, is_dev=True))

    return deps


def _parse_pipfile(content: str) -> list[ParsedDependency]:
    data = tomllib.loads(content)
    deps: list[ParsedDependency] = []
    for section, is_dev in (("packages", False), ("dev-packages", True)):
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for name, spec in section_data.items():
            if isinstance(spec, str):
                version = None if spec == "*" else spec
            elif isinstance(spec, dict):
                raw_version = spec.get("version")
                version = None if raw_version in (None, "*") else raw_version
            else:
                version = None
            deps.append(ParsedDependency(name, "pypi", version, is_dev))
    return deps


_GEM_LINE_RE = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+)['"])?""")
_GROUP_START_RE = re.compile(r"^\s*group\s+(.+?)\s+do\b")
_GROUP_END_RE = re.compile(r"^\s*end\b")
_DEV_GROUP_NAMES = ("development", "test")


def _parse_gemfile(content: str) -> list[ParsedDependency]:
    """Best-effort static scan of a Gemfile (Ruby, not a data format) -
    regex-based, deterministic for identical input, never executes Ruby.
    Recognizes `gem "name", "constraint"` lines and `group :development
    do ... end` / `group :test do ... end` blocks."""
    deps: list[ParsedDependency] = []
    group_stack: list[bool] = []

    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0]
        group_match = _GROUP_START_RE.match(line)
        if group_match:
            names = group_match.group(1)
            is_dev_group = any(dev_name in names for dev_name in _DEV_GROUP_NAMES)
            group_stack.append(is_dev_group)
            continue
        if _GROUP_END_RE.match(line) and group_stack:
            group_stack.pop()
            continue
        gem_match = _GEM_LINE_RE.match(line)
        if gem_match:
            name, version = gem_match.groups()
            deps.append(ParsedDependency(name, "rubygems", version, any(group_stack)))
    return deps


_GO_REQUIRE_LINE_RE = re.compile(r"^\s*([^\s]+)\s+(v[^\s]+)")


def _parse_go_mod(content: str) -> list[ParsedDependency]:
    deps: list[ParsedDependency] = []
    in_require_block = False

    for raw_line in content.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("require") and line.endswith("("):
            in_require_block = True
            continue
        if in_require_block and line == ")":
            in_require_block = False
            continue

        if in_require_block:
            candidate = line
        elif line.startswith("require "):
            candidate = line[len("require ") :].strip()
        else:
            continue

        match = _GO_REQUIRE_LINE_RE.match(candidate)
        if match:
            module_path, version = match.groups()
            deps.append(ParsedDependency(module_path, "go", version, False))
    return deps


def _parse_cargo_toml(content: str) -> list[ParsedDependency]:
    data = tomllib.loads(content)
    deps: list[ParsedDependency] = []
    for section, is_dev in (("dependencies", False), ("dev-dependencies", True)):
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for name, spec in section_data.items():
            if isinstance(spec, str):
                version = spec
            elif isinstance(spec, dict):
                version = spec.get("version")
            else:
                version = None
            deps.append(ParsedDependency(name, "cargo", version, is_dev))
    return deps


def _parse_composer_json(content: str) -> list[ParsedDependency]:
    data = json.loads(content)
    deps: list[ParsedDependency] = []
    if not isinstance(data, dict):
        return deps
    for section, is_dev in (("require", False), ("require-dev", True)):
        section_data = data.get(section)
        if not isinstance(section_data, dict):
            continue
        for name, version in section_data.items():
            if name == "php":
                continue  # the interpreter constraint itself, not a package
            deps.append(
                ParsedDependency(
                    name=name,
                    ecosystem="composer",
                    version_constraint=str(version) if version is not None else None,
                    is_dev=is_dev,
                )
            )
    return deps


_PARSERS: dict[str, Callable[[str], list[ParsedDependency]]] = {
    _PACKAGE_JSON: _parse_package_json,
    _REQUIREMENTS_TXT: _parse_requirements_txt,
    _PYPROJECT_TOML: _parse_pyproject_toml,
    _SETUP_PY: _parse_setup_py,
    _SETUP_CFG: _parse_setup_cfg,
    _PIPFILE: _parse_pipfile,
    _GEMFILE: _parse_gemfile,
    _GO_MOD: _parse_go_mod,
    _CARGO_TOML: _parse_cargo_toml,
    _COMPOSER_JSON: _parse_composer_json,
}

SUPPORTED_MANIFEST_CONTENT_TYPES = frozenset(_PARSERS)


def parse_manifest(content_type: str, content: str) -> ManifestParseResult:
    """Parse one manifest's raw text into declared dependencies.

    Never raises. Empty/whitespace-only content always yields zero
    dependencies and no error - "no content" is treated as "no declared
    dependencies", the same outcome for every format, rather than
    letting each format's own parser decide what an empty file means.
    An unsupported `content_type` (e.g. `"readme"`) yields a
    `parse_error` describing that, not an exception.
    """
    if not content.strip():
        return ManifestParseResult(dependencies=(), parse_error=None)

    if content_type not in _PARSERS:
        return ManifestParseResult(dependencies=(), parse_error=f"unsupported content_type: {content_type!r}")

    parser = _PARSERS[content_type]
    try:
        dependencies = parser(content)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any malformed manifest degrades to
        # parse_error, never propagates - this module has no batch/logging context of its own to isolate
        # a failure from (that is 2.2.c's job); see module docstring.
        return ManifestParseResult(dependencies=(), parse_error=f"{exc.__class__.__name__}: {exc}")

    return ManifestParseResult(dependencies=tuple(dependencies), parse_error=None)
