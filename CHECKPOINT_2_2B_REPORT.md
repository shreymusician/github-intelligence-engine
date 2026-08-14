# Checkpoint 2.2.b Report — Technology Taxonomy Mapping

**Parent Checkpoint:** 2.2 (Technology Stack Detection), sub-checkpoint b of c (c not started)
**Status:** ✅ Complete — deterministic tests only. **No live corpus processing, no production-table writes, no 2.2.c orchestration occurred in this checkpoint.**

---

## 1. Objective

Classify `processing.manifest_parser.ParsedDependency` records (2.2.a) into the existing, frozen `technologies`/`repository_technologies` schema (Migrations 001, 003) — conservatively, deterministically, with no schema change, no orchestration, and no external calls.

## 2. Taxonomy Design

`processing/technology_taxonomy.py` produces `Classification(technology, role, confidence)` from either:
- `classify_language(ecosystem)` — a manifest's own ecosystem structurally implies a language (a `Cargo.toml` existing means the repository contains Rust code). Deliberately narrow: `npm` → `JavaScript` only, never claiming to distinguish TypeScript (2.2.a's manifest parser has no signal that could tell them apart).
- `classify_package(name, ecosystem, is_dev=...)` — an exact match against a small, hand-curated dict, or `None`.

**Categories** are fixed by the existing `technology_category` Postgres enum (Migration 001): `language`, `framework`, `database`, `testing_tool`, `devops_tool`, `platform`. No category was added.

**Curated packages (5 entries, all corpus-confirmed via a read-only query during design review):**

| Package | Ecosystem | Category |
|---|---|---|
| flask | pypi | framework |
| fastapi | pypi | framework |
| pytest | pypi | testing_tool |
| boto3 | pypi | platform |
| svelte | npm | framework |

**Plus 6 ecosystem→language entries** (npm→JavaScript, pypi→Python, cargo→Rust, go→Go, rubygems→Ruby, composer→PHP). **11 technology identities total.**

## 3. The Correction Applied (per your explicit instruction)

The design-review draft had proposed classifying `sqlalchemy`, `psycopg`/`psycopg2`, `redis`, `pymongo` as `database`, and `requests`/`httpx` as `framework`-adjacent. **This was wrong and was not implemented.** Being a database/HTTP *client* library does not make the library itself a database or framework technology. The final curated list excludes all of these — confirmed by an explicit regression test (`test_client_libraries_are_not_misclassified_as_database`, `test_http_client_libraries_are_not_misclassified_as_framework`) that asserts they classify to `None`. `django` and `express` were also excluded despite being textbook-safe examples, because neither was actually observed in the live corpus — the taxonomy is corpus-driven, not theoretical.

## 4. Normalization

Lowercase always; for `pypi` specifically, runs of `-`/`_`/`.` are folded to a single `-`, matching PyPI's own PEP 503 canonicalization rule (so `PyYAML`/`pyyaml`/`py.yaml` normalize identically). No separate "alias table" mechanism was built — normalization alone covers every real case in the current curated set; a genuinely different name (e.g. a future `reactjs`→`react` case) would just be a second dict key pointing at the same `Technology`, not a new mechanism.

## 5. Classification Semantics — Role

- `classify_language()` always returns `role='primary'` — a manifest's ecosystem is as central a language signal as this checkpoint can produce.
- `classify_package()` returns `role='dev'` when 2.2.a's own `is_dev` flag is `True`, otherwise `role='secondary'` — **never `'primary'`**, because manifest data alone gives no way to judge a package's centrality to the repository (that would require code-level usage analysis, out of scope). This is the most conservative rule available given `is_dev` was already a direct, non-invented signal from 2.2.a.

`confidence` is always `1.0` — enforced by `Classification.__post_init__`, which raises if constructed with anything else. No fuzzy-matching confidence scale exists. `detected_via` is hardcoded to `'manifest'` in the storage layer's SQL — the only detection method this checkpoint has.

## 6. Unknown-Package Policy

Confirmed as **Option A** (only explicitly recognized packages are classified). An unrecognized or ambiguous name produces no `technologies` row and no `repository_technologies` row — it is not forced into the nearest category. 823 distinct `pypi` package names were observed in the live corpus during design review; the curated list classifies a small fraction of them, intentionally.

## 7. Persistence Semantics (`processing/technology_storage.py`)

`TechnologyWriter.upsert_technologies(repository_id, classifications) -> list[uuid.UUID]`, mirroring `ContentWriter`'s exact shape (connection-per-call, one transaction per repository call, typed exception wrapping).

- **`technologies`**: `INSERT ... ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id` — category is never overwritten on conflict (a slug's category is fixed by the curated taxonomy, not something that should drift).
- **`repository_technologies`**: `INSERT ... ON CONFLICT (repository_id, technology_id) DO UPDATE SET role, confidence, last_confirmed_at = now(), removed_at = NULL` — `first_detected_at` is never included in the SET clause, so its `server_default now()` (fired only on INSERT) is preserved automatically across re-runs, exactly as the schema's own design intends.
- **Duplicate relationships**: prevented by the existing composite primary key — no application-level uniqueness mechanism was added.
- **Idempotency**: re-processing the same repository with the same classifications advances `last_confirmed_at`, leaves `first_detected_at` untouched, and creates no duplicate row — verified by dedicated tests.
- **`removed_at` reconciliation: explicitly NOT implemented.** This checkpoint only ever records "observed now" (`removed_at = NULL` on every upsert touching a technology). Marking a previously-associated, now-unobserved technology as removed requires knowing the complete set of repositories/technologies actually reprocessed in a run — orchestration-level context 2.2.b does not have. Deferred to a future checkpoint, per your explicit instruction not to invent this here.

## 8. Transaction Behavior

One `conn.transaction()` per `upsert_technologies()` call, covering every classification in the batch. On any `psycopg.Error`, the transaction rolls back (verified: no partial rows survive a mid-batch failure, including a failure on the *second* of two classifications — the first classification's row is rolled back too, not just the failing one) and `TechnologyPersistenceError` (new, `processing/exceptions.py`, mirrors `ContentPersistenceError`) is raised, wrapping the original `psycopg.Error`. The connection is always closed, success or failure.

## 9. Tests

**`tests/test_technology_taxonomy.py` — 32 tests, zero DB/network:** every ecosystem→language mapping, known-package mapping (flask/fastapi/pytest/boto3/svelte), case normalization, PyPI separator normalization (including a synthetic test proving the normalization rule itself, independent of curated-list contents), unknown-package→`None`, the explicit client-library misclassification regression guard (§3), empty name→`None`, known-name-wrong-ecosystem→`None`, deterministic repeated classification, no-fuzzy-matching (near-miss names don't match), and `Technology`/`Classification` value-validation (`__post_init__` rejects bad category/role/confidence).

**`tests/test_technology_storage.py` — 16 tests, fake `psycopg` connection (mirrors `test_content_storage.py`'s `FakeConnection`/`FakeCursor`/`FakeTransaction` pattern), no live PostgreSQL:** new technology creation, repository association creation, multiple technologies in one transaction, empty-list no-op, `detected_via='manifest'`, confidence passthrough, existing-technology reuse across repositories (same `id`, not duplicated), duplicate-association PK enforcement, `first_detected_at` preservation across re-runs, `last_confirmed_at` advancement, `removed_at` reset on re-observation, no stale reconciliation for a technology absent from a given call, rollback on failure (no partial rows, including a mid-batch second-item failure), `TechnologyPersistenceError` wraps the original cause, connection always closed.

**All 48 new tests pass.**

## 10. Files Changed

| File | Change |
|---|---|
| `processing/technology_taxonomy.py` | New |
| `processing/technology_storage.py` | New |
| `processing/exceptions.py` | New — `TechnologyPersistenceError` |
| `tests/test_technology_taxonomy.py` | New — 32 tests |
| `tests/test_technology_storage.py` | New — 16 tests |
| `CHECKPOINT_2_2B_REPORT.md` | New — this report |

No existing file modified. **No migration. No schema change.**

## 11. Explicit Non-Goals (not built, by design)

Package-registry queries, vulnerability scanning, AI/LLM classification, embeddings, any ML model, GitHub API calls, a generalized rule/plugin engine, a "library" or any other new category, `removed_at`/stale-technology reconciliation, batch orchestration across repositories (2.2.c), fuzzy/confidence-scaled matching, any write to the real `technologies`/`repository_technologies` tables (this checkpoint's persistence code was exercised only against fake connections).

## 12. Known Limitations

- The curated list (5 packages) classifies a small fraction of real dependency rows — intentional (§6), not a gap to be silently grown without evidence.
- `role='secondary'` for every non-dev package match is a conservative default, not a measured "how central is this to the repo" judgment — documented as such (§5), not a hidden assumption.
- Reprocessing a repository whose manifests changed (a technology genuinely removed) leaves the old `repository_technologies` row exactly as it was (§7) — correct per this checkpoint's explicit scope, but means `repository_technologies` can only grow, never shrink, until reconciliation is built.

## 13. Git

| Commit | Message |
|---|---|
| `15491c5` | Checkpoint 2.2.b: Technology taxonomy |
| `24c1cbb` | Checkpoint 2.2.b: Technology persistence |

Both pushed to `origin/main` individually, verified `local HEAD == origin/main` after each. This report is the third, final commit for 2.2.b.

## 14. What This Enables Next

2.2.c (orchestration + live verification against the real 92-repository corpus) can compose `manifest_parser.parse_manifest` → `technology_taxonomy.classify_language`/`classify_package` → `technology_storage.TechnologyWriter.upsert_technologies`, mirroring 1.4.d's orchestration precedent exactly. **Not started — awaiting review before proceeding.**
