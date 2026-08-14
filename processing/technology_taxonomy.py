"""Deterministic package/ecosystem -> technology classification — Checkpoint 2.2.b.

Given one processing.manifest_parser.ParsedDependency's (name, ecosystem,
is_dev) (Checkpoint 2.2.a), or a manifest's ecosystem alone, classifies
it against a small, hand-curated set of technologies. A package or
ecosystem is classified only when the mapping is genuinely unambiguous;
everything else returns `None`, deliberately, rather than guessing.

Explicitly NOT this module's job:
- Persisting anything to PostgreSQL (technology_storage.py).
- Orchestrating this across many repositories (2.2.c).
- Classifying every dependency in the corpus. Most real packages
  (numpy, pandas, requests, httpx, sqlalchemy, psycopg, redis-py,
  kafka-python, pika, grpcio, elasticsearch-py, celery, ...) genuinely
  are not a language, framework, testing tool, database, devops tool,
  or platform in the sense `repository_technologies.category` requires
  - being a database/queue/RPC *client* library does not make the
  library itself a database, queue, or RPC technology - and are left
  intentionally unclassified rather than forced into the nearest
  available category. Accuracy over coverage. `boto3` is the one
  exception in the curated set below: it is not a general utility, it
  exists specifically as the AWS platform SDK.

Categories are fixed by the existing `technology_category` enum
(Migration 001, alembic/versions/001_reference_tables.py) - language,
framework, database, testing_tool, devops_tool, platform. No category
is added here; no migration accompanies this checkpoint.

No fuzzy matching, no machine learning, no popularity-based inference,
no network/registry/GitHub/LLM calls anywhere in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CATEGORIES = frozenset({"language", "framework", "database", "testing_tool", "devops_tool", "platform"})
_ROLES = frozenset({"primary", "secondary", "dev"})


@dataclass(frozen=True)
class Technology:
    """One canonical technology identity - mirrors the `technologies`
    table's (slug, name, category) columns exactly; nothing here that
    table doesn't already have a place for."""

    slug: str
    name: str
    category: str

    def __post_init__(self) -> None:
        if self.category not in _CATEGORIES:
            raise ValueError(f"unknown technology category: {self.category!r}")


@dataclass(frozen=True)
class Classification:
    """One (technology, role, confidence) verdict for a single
    dependency or ecosystem observation.

    `confidence` is always 1.0 - this module has no fuzzy matching, so
    every classification is exact-or-absent (see module docstring);
    there is deliberately no sliding confidence scale.
    """

    technology: Technology
    role: str
    confidence: float

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"unknown role: {self.role!r}")
        if self.confidence != 1.0:
            raise ValueError("this module only ever produces confidence 1.0 (no fuzzy matching)")


# ---------------------------------------------------------------------------
# Ecosystem -> language: structural, not corpus-dependent. A manifest of a
# given format existing is, by definition, evidence the repository
# contains code in that ecosystem's language. Deliberately narrow: `npm`
# means "JavaScript", not "JavaScript or TypeScript" - 2.2.a has no signal
# that distinguishes a TypeScript project from a JavaScript one (both use
# package.json), so no such distinction is claimed here.
# ---------------------------------------------------------------------------

_ECOSYSTEM_LANGUAGES: dict[str, Technology] = {
    "npm": Technology("javascript", "JavaScript", "language"),
    "pypi": Technology("python", "Python", "language"),
    "cargo": Technology("rust", "Rust", "language"),
    "go": Technology("go", "Go", "language"),
    "rubygems": Technology("ruby", "Ruby", "language"),
    "composer": Technology("php", "PHP", "language"),
}

# ---------------------------------------------------------------------------
# Curated package classifications. Small, deliberately conservative, and
# built from what actually appears in the live 92-repository corpus (a
# read-only query run during 2.2.b's design review) plus this checkpoint's
# explicit correction: only packages whose category is definitional (a web
# framework is a web framework) are included - never inferred from what a
# library talks to or how popular it is.
# ---------------------------------------------------------------------------

_CURATED_PACKAGES: dict[tuple[str, str], Technology] = {
    ("pypi", "flask"): Technology("flask", "Flask", "framework"),
    ("pypi", "fastapi"): Technology("fastapi", "FastAPI", "framework"),
    ("pypi", "pytest"): Technology("pytest", "pytest", "testing_tool"),
    ("pypi", "boto3"): Technology("aws-sdk-python", "AWS SDK (boto3)", "platform"),
    ("npm", "svelte"): Technology("svelte", "Svelte", "framework"),
}

_PYPI_SEPARATOR_RE = re.compile(r"[-_.]+")


def _normalize(name: str, ecosystem: str) -> str:
    """Lowercase always; for `pypi`, additionally fold runs of `-`/`_`/`.`
    to a single `-`, matching PyPI's own PEP 503 name-canonicalization
    rule, so e.g. `PyYAML`, `pyyaml`, and `py.yaml` normalize identically
    - without merging genuinely different package names (only separator
    characters are folded, not arbitrary substrings)."""
    normalized = name.strip().lower()
    if ecosystem == "pypi":
        normalized = _PYPI_SEPARATOR_RE.sub("-", normalized)
    return normalized


def classify_language(ecosystem: str) -> Classification | None:
    """The language a manifest's own ecosystem structurally implies.

    `role` is always `'primary'`: a manifest existing for an ecosystem
    is as central a language signal as this checkpoint can produce.
    Returns `None` for an ecosystem this module doesn't recognize.
    """
    technology = _ECOSYSTEM_LANGUAGES.get(ecosystem)
    if technology is None:
        return None
    return Classification(technology=technology, role="primary", confidence=1.0)


def classify_package(name: str, ecosystem: str, *, is_dev: bool) -> Classification | None:
    """Classify one package name against the curated list.

    Returns `None` for anything not an exact match after normalization -
    no fuzzy matching, no inference from popularity or behavior, and
    unknown/ambiguous packages are never forced into a category.

    `role` is `'dev'` when the manifest declared this as a dev/optional
    dependency (2.2.a's own `is_dev` signal), otherwise `'secondary'` -
    this module has no way to judge a dependency's centrality to the
    repository from manifest data alone, so it never claims `'primary'`
    for a package classification (only `classify_language` does).
    """
    if not name.strip():
        return None
    normalized = _normalize(name, ecosystem)
    technology = _CURATED_PACKAGES.get((ecosystem, normalized))
    if technology is None:
        return None
    role = "dev" if is_dev else "secondary"
    return Classification(technology=technology, role=role, confidence=1.0)
