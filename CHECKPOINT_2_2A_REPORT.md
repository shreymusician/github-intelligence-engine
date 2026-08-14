# Checkpoint 2.2.a Report — Manifest Parsing

**Parent Checkpoint:** 2.2 (Technology Stack Detection), sub-checkpoint a of c (b/c not started)
**Status:** ✅ Complete, deterministic tests only (no live/database verification applicable — this checkpoint touches neither)

---

## 1. Objective

Deterministically parse the manifest formats already recognized and persisted by Checkpoint 1.4.b/1.4.c (`repository_content`) into a small, normalized intermediate representation, with no technology classification, no persistence, no orchestration, and no external calls of any kind.

## 2. Design Verification (performed before coding)

Re-read `IMPLEMENTATION_ROADMAP.md` Checkpoint 2.2, `repository_content`'s schema, and `acquisition/content_extractor.py` in full. Confirmed the exact, already-approved manifest set (`_MANIFEST_CONTENT_TYPES` in `content_extractor.py`): `package.json`, `requirements.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `Gemfile`, `go.mod`, `Cargo.toml`, `composer.json`. All ten are in scope here — no format was added beyond this list. Six have live rows in the current 92-repository corpus (`pyproject_toml`, `requirements_txt`, `setup_py`, `setup_cfg`, `package_json`, `cargo_toml`); four (`pipfile`, `gemfile`, `go_mod`, `composer_json`) are already-approved formats with zero current rows, included for correctness against future corpus growth, not speculatively invented.

## 3. Architecture Decision

Code placed in a new top-level `processing/` package, sibling to `acquisition/` — matching this project's actual, working structure (`acquisition/`, `github_client/`), not the empty `src/processing/` scaffold from the original architecture document. `src/` was not modified. No existing package was moved or restructured. `processing/` did not previously exist; confirmed via search that nothing imports it.

## 4. What Was Implemented

`processing/manifest_parser.py`:
- `ParsedDependency(name, ecosystem, version_constraint, is_dev)` — the smallest representation Checkpoint 2.2.b needs (technology-taxonomy mapping + `repository_technologies.role`), with no transitive/registry data (Checkpoint 2.5's job).
- `ManifestParseResult(dependencies, parse_error)` — never-raising outcome type; `parse_error` is set (dependencies empty) on malformed/unparseable input instead of an exception, since this layer has no batch/orchestration context (that's 2.2.c) to isolate a raised error from.
- `parse_manifest(content_type, content) -> ManifestParseResult` — the single public entry point, dispatching to one parser per format.
- Ten per-format parsers, stdlib-only (`json`, `tomllib`, `configparser`, `ast`, `re`) — **no new third-party dependency was added**.
- `setup.py` is parsed via `ast.parse` + static lookup of `install_requires`/`extras_require` inside a `setup(...)` call — the file is never executed.
- `pyproject.toml` supports both PEP 621 (`[project.dependencies]`) and Poetry (`[tool.poetry.dependencies]`/`[tool.poetry.group.*.dependencies]`) styles, since real persisted files use both.
- Empty/whitespace-only content always yields zero dependencies, no error, for every format uniformly.

## 5. Explicitly Not Done (scope boundary held)

No technology classification, no PostgreSQL writes, no repository orchestration, no GitHub API calls, no package-registry queries, no AI/LLM usage, no embeddings, no schema/migration change, no modification to `acquisition/*` or any other existing file.

## 6. Tests

`tests/test_manifest_parser.py`, 38 tests, class-grouped per format (mirrors `tests/test_content_extractor.py`'s style):
- Normal manifests, multiple dependencies, version constraints — all 10 formats
- Empty manifest (all 10 formats, one parametrized test) and format-specific "declares nothing" cases
- Malformed input per format (bad JSON/TOML/INI/Python syntax) — always degrades to `parse_error`, never raises
- UTF-8 package names (`package_json`)
- Duplicate declarations where the format allows them (`requirements_txt`)
- Comments (`requirements_txt`, `Gemfile`)
- Dev/optional-dependency scope distinction where the format supports it (`package_json`, `pyproject_toml`, `setup_py`, `setup_cfg`, `pipfile`, `Cargo.toml`, `Gemfile`); explicit "no dev scope in this format" assertions for `requirements_txt`/`go.mod`
- Deterministic output ordering (same input twice → identical tuple)
- Unsupported `content_type`
- A structural test (`TestNoExternalDependencies`) asserting the module's own source contains no `psycopg`/`subprocess`/`requests.`/socket usage — enforcing "no DB, no network" at the source level, not just by test behavior

**All 38 pass.**

## 7. Validation

- `pytest tests/test_manifest_parser.py`: 38 passed
- Full regression suite: **296 passed, 4 skipped, 0 failed** (was 258 passed, 4 skipped before this checkpoint — exactly +38, no regression)
- `git status`/`git diff --stat`: only `processing/` (new) and `tests/test_manifest_parser.py` (new) present — no existing file touched
- Secret scan (`ghp_`, `github_pat_`, `POSTGRES_USER_PASSWORD=`, `api_key`) across new files: zero matches

## 8. Files Changed

| File | Change |
|---|---|
| `processing/__init__.py` | New |
| `processing/manifest_parser.py` | New |
| `tests/test_manifest_parser.py` | New |
| `CHECKPOINT_2_2A_REPORT.md` | New — this report |

No existing file was modified.

## 9. Known Limitations (documented, not fixed here — out of 2.2.a's scope)

- `setup.py`/`Gemfile` parsing is static/best-effort by necessity (Python and Ruby are executable languages, not data formats) — a dynamically computed `install_requires` or an unusual `Gemfile` DSL construct yields fewer dependencies than the file might declare at runtime, not an error.
- `requirements.txt`/`go.mod` have no dev/prod distinction in the format itself — every dependency from these formats is `is_dev=False` by convention, documented in-line and tested explicitly.
- No deduplication across formats or repositories — that's a 2.2.b/2.2.c concern once technology mapping exists.

## 10. Definition of Done

| Criterion | Status |
|---|---|
| Parses only already-approved manifest formats | ✅ §2 |
| No technology classification | ✅ §5 |
| No persistence, no orchestration, no GitHub/registry/AI calls | ✅ §5 |
| Never crashes on malformed/empty input | ✅ §6, §4 |
| Reuses existing structure/conventions, no restructuring | ✅ §3 |
| Deterministic tests for every supported format | ✅ §6, 38/38 pass |
| Full regression suite green | ✅ §7, 296 passed / 4 skipped / 0 failed |
| No unintended file changes | ✅ §7 |
| Security scan clean | ✅ §7 |

**Sub-checkpoint 2.2.a is COMPLETE.**

## 11. What This Enables Next

2.2.b (Technology Taxonomy Mapping) can consume `ManifestParseResult.dependencies` directly to classify raw package names into the curated `technologies` table. **Not started — awaiting review before proceeding.**
