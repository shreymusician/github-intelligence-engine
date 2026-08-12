# Checkpoint 1.4.c Report — Content Persistence (repository_content)

**Parent Checkpoint:** 1.4 (Repository Content Download), sub-checkpoint c of e
**Status:** ✅ Complete, verified (deterministic mocked tests + live PostgreSQL persistence, independently cross-checked)
**Date:** 2026-08-12

---

## 1. Objective

Provide durable PostgreSQL persistence for the `ExtractedFile` objects already produced by Checkpoint 1.4.b (`acquisition/content_extractor.py`), across the flow:

```
ExtractedFile → ContentWriter → repository_content → PostgreSQL
```

This module owns none of the upstream lifecycle: it does not clone repositories, invoke Git, discover files, extract README/manifest content, orchestrate which repositories to process, perform feature engineering, or perform AI analysis. It is handed a `repository_id` (an existing `repositories.id`, produced by Checkpoint 1.3's `RepositoryWriter`) and a list of already-extracted `ExtractedFile` objects, and persists them — nothing more.

---

## 2. Schema Provenance

`repository_content` is **not** one of `DATABASE_DESIGN.md`'s twelve core entities — that document froze at Checkpoint 0.2, before Checkpoint 1.4 existed. This table's schema was introduced and explicitly approved as part of the **Checkpoint 1.4 design specification** (this checkpoint's design-verification exchange), not retroactively added to the frozen Checkpoint 0.2 model. `DATABASE_DESIGN.md` is intentionally left unmodified. Migration 010's own docstring and this report are the schema's provenance record.

---

## 3. Phase 1 — Design Verification (confirmed before coding)

Reviewed `DATABASE_DESIGN.md`, `SYSTEM_ARCHITECTURE.md`, all nine existing Alembic migrations (naming/constraint conventions), `acquisition/content_extractor.py` (1.4.b's exact `ExtractedFile` contract), `acquisition/storage.py` (the established `RepositoryWriter` upsert/transaction pattern), `acquisition/exceptions.py`, `config/settings.py`, `tests/test_storage.py` (the fake-connection mocking pattern), and `CHECKPOINT_1_4A_REPORT.md`/`CHECKPOINT_1_4B_REPORT.md`, before writing any code.

Key findings, all confirmed consistent with the approved schema:

1. **Migration sequence:** last migration was `009_repository_similarity.py`; Migration 010 follows immediately (`down_revision='009'`), using the exact `op.f('pk_...')`/`op.f('fk_..._..._...')`/`op.f('ck_..._...')` naming conventions every prior migration uses.
2. **No `updated_at` trigger exists anywhere in this schema.** Every table sets `updated_at` explicitly in application SQL (confirmed in `acquisition/storage.py`). This meant the approved idempotency semantics — "bump `updated_at` only when content changes, preserve it otherwise" — had to be expressed as a `CASE ... IS DISTINCT FROM ...` expression inside the `ON CONFLICT DO UPDATE` clause itself, not a generic trigger.
3. **1.4.b's `ExtractedFile`** fields (`content_type`, `file_path`, `content`, `content_hash`, `content_size_bytes`) map directly to `repository_content`'s columns — no translation layer needed.
4. Two corrections were made to the initial design-verification proposal per your review before implementation began:
   - `repository_id` is a parameter of `ContentWriter.upsert_content(repository_id, files)`, not a field added to `ExtractedFile` — `ExtractedFile` remains solely the extraction layer's concern; `repository_id` belongs to this persistence boundary.
   - `content_type = EXCLUDED.content_type` is unconditionally refreshed on conflict (approved upsert semantics, not an open ambiguity).

No contradiction was found between the approved schema and the current codebase. No stop condition was triggered.

---

## 4. Architecture

```
alembic/versions/010_repository_content.py   — creates repository_content
acquisition/content_storage.py
├── ContentWriter
│   ├── __init__(settings=None, connection_factory=None)
│   └── upsert_content(repository_id, files: list[ExtractedFile]) -> list[uuid.UUID]
└── (module-level) _default_connection_factory, _upsert_content_row
acquisition/exceptions.py (extended)
└── ContentPersistenceError(ContentAcquisitionError)  — mirrors RepositoryPersistenceError's shape
```

`ContentWriter` holds no long-lived database connection — each `upsert_content()` call opens, uses, and closes its own connection, mirroring `RepositoryWriter`'s established precedent exactly.

---

## 5. Schema (Migration 010)

```sql
CREATE TABLE repository_content (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repository_id       UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    content_type        TEXT NOT NULL,
    file_path           TEXT NOT NULL,
    content              TEXT NOT NULL,
    content_hash         TEXT NOT NULL,
    content_size_bytes   INTEGER NOT NULL CHECK (content_size_bytes >= 0),
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repository_id, file_path)
);
```

No secondary indexes beyond the PK and the unique constraint (auto-indexed by Postgres) — no query in this checkpoint's scope justified one.

---

## 6. The Upsert (Idempotency Semantics)

```sql
INSERT INTO repository_content (
    repository_id, content_type, file_path, content, content_hash, content_size_bytes
)
VALUES (%(repository_id)s, %(content_type)s, %(file_path)s, %(content)s, %(content_hash)s, %(content_size_bytes)s)
ON CONFLICT (repository_id, file_path) DO UPDATE SET
    content_type        = EXCLUDED.content_type,
    content              = EXCLUDED.content,
    content_hash         = EXCLUDED.content_hash,
    content_size_bytes   = EXCLUDED.content_size_bytes,
    fetched_at           = now(),
    updated_at = CASE
        WHEN repository_content.content_hash IS DISTINCT FROM EXCLUDED.content_hash
        THEN now()
        ELSE repository_content.updated_at
    END
RETURNING id
```

- **Same `file_path` + same `content_hash`:** `fetched_at` refreshed, `updated_at` preserved, no new row.
- **Same `file_path` + different `content_hash`:** `content`/`content_hash`/`content_size_bytes`/`fetched_at` all refreshed, `updated_at` bumped.
- **Different `file_path`:** a separate row (the unique constraint is a composite of both columns).

---

## 7. Files Changed

| File | Change |
|---|---|
| `alembic/versions/010_repository_content.py` | New — `repository_content` table |
| `acquisition/content_storage.py` | New — `ContentWriter` |
| `acquisition/exceptions.py` | Extended — `ContentPersistenceError` |
| `acquisition/__init__.py` | Extended — exports `ContentWriter`, `ContentPersistenceError` |
| `tests/test_content_storage.py` | New — 20 deterministic tests |
| `CHECKPOINT_1_4C_REPORT.md` | New — this report |

No file outside this list was touched. `acquisition/clone_workspace.py` (1.4.a) and `acquisition/content_extractor.py` (1.4.b) are untouched — confirmed by `git diff` before committing. No existing (frozen) migration was modified.

---

## 8. Deterministic Test Suite (`tests/test_content_storage.py`, 20 tests)

Mirrors `tests/test_storage.py`'s fake-connection pattern, extended with a `FakeContentRegistry` that models `repository_content`'s actual conditional-touch semantics for `updated_at` (new logic, scoped to this file only — no prior table in this schema has conditional-touch semantics). The fake registry's in-memory state is snapshotted at transaction start and restored on rollback, so "no partial rows survive a rollback" assertions are meaningful against the double, not merely against `conn.rolled_back`.

| Class | Tests | Covers |
|---|---|---|
| `TestBasicPersistence` | 7 | Single insert; multiple files/one repository; multiple repositories are independent (same `file_path`, different repos → separate rows); multiple content types; empty-batch no-op (no connection opened); `content_size_bytes` correctness; `content_hash` persistence |
| `TestIdempotency` | 4 | Same file/same hash → `fetched_at` refreshed, `updated_at` preserved, no duplicate; same file/changed hash → row updated in place, `updated_at` bumped; different `file_path` → separate row; identical batch reinserted twice → no duplicate rows |
| `TestTransactionBehavior` | 6 | Commits on success; rolls back on failure with zero partial rows; failure midway through a multi-file batch leaves zero partial rows; `psycopg.Error` translated to `ContentPersistenceError`, confirmed to be a `ContentAcquisitionError` subclass wrapping the original `psycopg.Error` as `.cause`; connection always closed |
| `TestParameterizedSQL` | 4 | SQL uses named placeholders, never string-interpolated (a malicious content payload is proven to land only in bound parameters, never in the executed SQL text); `ON CONFLICT (repository_id, file_path)` targets the correct constraint; `content_type = EXCLUDED.content_type` is present (approved refresh-on-conflict behavior); `repository_id` passed as a bound parameter on every row in a batch |

**All 20 pass.**

---

## 9. Live Verification

Ran the real `RepositoryCloner` → `extract_content()` → `ContentWriter.upsert_content()` pipeline against two repositories already present in the Checkpoint 1.3 corpus (`vinta/awesome-python`, `public-apis/public-apis` — the same two 1.4.b used for its own live verification; `octocat/Hello-World` was not part of the actual corpus and was excluded here), via a throwaway driver script (not delivered code, deleted after evidence capture, matching 1.3.b/1.4.a/1.4.b precedent).

### 9.1 Migration applied to the real local database

```
alembic_version.version_num = 010
```

Table structure independently inspected via `\d repository_content` in `psql`: all ten columns present with correct types/nullability, `pk_repository_content` (PK on `id`), `uq_repository_content_repository_id_file_path` (UNIQUE), `ck_repository_content_content_size_bytes_non_negative` (CHECK), and `fk_repository_content_repository_id_repositories` (FK, `ON DELETE CASCADE`) — all matching the approved schema exactly.

### 9.2 Persistence and independent SQL verification

```
vinta/awesome-python: repo_id=e587e072-1f5f-483d-ac40-d0507b80b96b files=2
public-apis/public-apis: repo_id=fb316719-ab0a-481d-8fd9-ad673c4b6b17 files=1

vinta/awesome-python: 2 rows in repository_content
  pyproject.toml: hash_ok=True size_ok=True content_ok=True type_ok=True
  README.md: hash_ok=True size_ok=True content_ok=True type_ok=True

public-apis/public-apis: 1 rows in repository_content
  README.md: hash_ok=True size_ok=True content_ok=True type_ok=True

ALL INDEPENDENT CHECKS PASSED: True
```

Every hash (`hashlib.sha256`, recomputed independently from the file's own content, not via `ContentWriter`), byte size, decoded content, and `content_type` was cross-checked against a value queried directly from PostgreSQL via plain `psycopg` — not trusting `ContentWriter`'s own return values, matching the independent-verification discipline established in 1.3.b/1.3.e/1.4.b.

### 9.3 Idempotency — unchanged content

```
pyproject.toml: id_unchanged=True fetched_at_moved=True updated_at_unchanged=True
README.md: id_unchanged=True fetched_at_moved=True updated_at_unchanged=True
row_count_unchanged=True
```

Re-running `upsert_content()` with the identical `ExtractedFile` batch confirmed, via direct SQL timestamp comparison (not `ContentWriter`'s return value): same `id`, `fetched_at` moved forward, `updated_at` frozen, no new row.

### 9.4 Idempotency — changed content

```
README.md: id_unchanged=True content_changed=True updated_at_changed=True
```

A mutated `ExtractedFile` (content + a marker comment, with a freshly computed SHA-256 hash) was re-upserted for the same `file_path`: same row `id`, `content` changed, `updated_at` moved forward — confirmed via direct SQL, then the original content was restored via a further upsert so the row reflects the real repository's actual state afterward.

### 9.5 Different `file_path` → separate row

```
vinta/awesome-python: distinct file_paths persisted = ['README.md', 'pyproject.toml']
```

Already demonstrated by the natural two-file extraction from a single real repository.

### 9.6 FK enforcement

```
EXPECTED ContentPersistenceError raised: ContentPersistenceError: Failed to persist content
for repository UUID('a8db32b9-d3fb-4ee1-b0aa-32dd98d6e424'):
insert or update on table "repository_content" violates foreign key constraint
"fk_repository_content_repository_id_repositories"
DETAIL:  Key (repository_id)=(a8db32b9-d3fb-4ee1-b0aa-32dd98d6e424) is not present in table "repositories".
rows for bogus repository_id after failed insert: 0 (expect 0)
```

A random, non-existent `repository_id` correctly raised `ContentPersistenceError` (wrapping PostgreSQL's real `ForeignKeyViolation`), and independent SQL confirmed zero rows were left behind.

### 9.7 `content_size_bytes >= 0` CHECK constraint

```
EXPECTED ContentPersistenceError raised: ContentPersistenceError: Failed to persist content
for repository UUID('e587e072-1f5f-483d-ac40-d0507b80b96b'):
new row for relation "repository_content" violates check constraint
"ck_repository_content_content_size_bytes_non_negative"
rows for NEG_TEST.md: 0 (expect 0)
```

A crafted `ExtractedFile` with `content_size_bytes=-1` correctly raised `ContentPersistenceError` (wrapping PostgreSQL's real `CheckViolation`), and independent SQL confirmed zero rows were left behind. The constraint is enforced by the database itself, not merely by application code.

### 9.8 `ON DELETE CASCADE`

```
rows before delete: 1 (expect 1)
rows after repository delete (CASCADE): 0 (expect 0)
```

Verified using a throwaway dummy `owners`/`repositories` row created and deleted solely for this test (**not** any real Checkpoint 1.3 corpus data) — a `repository_content` row was persisted for it, the parent `repositories` row was then deleted directly via SQL, and the cascade was confirmed to remove the child row. The dummy owner row was also deleted, leaving no residue.

### 9.9 Transaction rollback

Demonstrated live by both 9.6 and 9.7: each failure path left the transaction rolled back with zero partial rows, confirmed independently via SQL — not merely by inspecting `ContentWriter`'s return value or exception.

### 9.10 Cleanup / no leftover test data

- The bogus-FK and negative-size test attempts wrote nothing (confirmed above).
- The Step 8 dummy repository, its owner row, and its `repository_content` row were all explicitly deleted at the end of the live-verification run; independent SQL confirmed 0 rows remained under that `repository_id`.
- No leftover `ghia_clone_*` directories remained in the OS temp directory after the run (checked directly, not via the driver script's own claim).
- The driver script itself was scratch-only, written to the session's scratchpad directory (outside the git repository) and never added to git.

---

## 10. Environmental Observation — Connection Latency (Not a Production Defect)

During live verification, individual `psycopg.connect()` calls (both from the driver script and from a standalone diagnostic check) took anywhere from ~5 seconds up to several minutes to establish, stretching what should have been a several-minute live-verification run to roughly 45 minutes. Diagnosis performed while the run was still in progress found:

- The PostgreSQL container (`repo_intelligence_postgres`) reported `healthy` throughout, with no elevated CPU, no restarts, and no errors in its own logs.
- `pg_stat_activity` showed no idle-in-transaction sessions and no blocked queries at any inspection point; `pg_locks` showed no contention.
- The driver script's own process was low-CPU (0–5%), consistent with blocking on socket/connection setup, not an infinite loop or deadlock in this checkpoint's code.
- The pattern (fast initial connection immediately after Docker Desktop's engine came up, then increasingly slow subsequent connections) is consistent with Docker Desktop's WSL2 network stack still stabilizing after a cold start — Docker Desktop had been off at the start of this checkpoint's live-verification phase and was started specifically to run it.

**This was an environmental condition of this development machine's Docker Desktop/WSL2 networking, not a defect discovered in Migration 010 or `ContentWriter`.** The eventual clean exit (code 0) with every assertion passing, combined with the absence of any lock contention, open transaction, or database-side anomaly at any point during diagnosis, confirms the underlying persistence logic behaved correctly throughout — it was simply waiting on slow TCP connection setup outside this checkpoint's code. No change was made to `content_storage.py`, Migration 010, or any other 1.4.c file as a result of this observation.

---

## 11. Defects Discovered/Fixed

**None in `acquisition/content_storage.py` or Migration 010.** One gap was found and fixed in the test double itself, before any test was reported as passing: the initial `FakeTransaction` in `tests/test_content_storage.py` did not roll back the fake registry's in-memory state on a simulated failure, which made a "no partial rows survive a rollback" assertion vacuously true. Fixed by snapshotting the registry's state at transaction start (`copy.deepcopy`) and restoring it on rollback — a test-fixture correction, not a change to any delivered persistence code.

---

## 12. Security Verification

- Grepped `acquisition/content_storage.py`, `acquisition/exceptions.py`, `acquisition/__init__.py`, `alembic/versions/010_repository_content.py`, and `tests/test_content_storage.py` for `GITHUB_TOKEN`, `github_pat_`, `ghp_`, and `POSTGRES_PASSWORD` — the only match was an existing docstring reference in `acquisition/exceptions.py` (predating this checkpoint, describing `RepositoryCloneError`'s no-token-leakage guarantee), not a real secret.
- No credentials appear in any SQL statement — all values are bound parameters (`%(name)s` placeholders), verified both by direct code inspection and by `TestParameterizedSQL::test_sql_uses_parameter_placeholders_not_string_interpolation`.
- No credentials appear in any test fixture.
- The live-verification driver script lived only in the session's scratchpad directory (outside the git repository) and was never staged or committed; `git status` confirms no scratch file exists in the working tree.
- No leftover clone directories remained in the OS temp directory after the live run.
- `.env`, `PAT.txt`, and `PAT (2).txt` remain git-ignored (confirmed via `git status --porcelain --ignored`), consistent with prior checkpoints — none of this checkpoint's changes touch that.

---

## 13. Known Limitations

- **No secondary indexes beyond the PK and the unique constraint** — no query in this checkpoint's scope justifies one; if a future consumer needs to query `repository_content` by `content_type` alone across many repositories, an index would need to be added deliberately then, not speculatively here.
- **`content_type` is refreshed unconditionally on conflict** (`content_type = EXCLUDED.content_type`) — per your explicit approval, the same physical `file_path` is not expected to change its `content_type` in practice, but the column is not treated as immutable at the schema level.
- **Connection-per-call, no pooling** — matches `RepositoryWriter`'s established precedent; a future high-throughput batch (1.4.d) may want to reconsider this, but that is explicitly out of this checkpoint's scope.

---

## 14. Definition of Done

| Criterion | Status |
|---|---|
| Migration 010 creates `repository_content` exactly per the approved schema | ✅ §5, §9.1 |
| `CHECK (content_size_bytes >= 0)` present with the full expression, not just a name | ✅ §5, live-confirmed via `\d repository_content` and a real `CheckViolation` (§9.7) |
| `ContentWriter.upsert_content(repository_id, files)` — `repository_id` explicit, not a field on `ExtractedFile` | ✅ §4, §6 |
| `ON CONFLICT (repository_id, file_path)` upsert implemented | ✅ §6, §9.5 |
| Idempotency: same hash → `fetched_at` refreshed, `updated_at` preserved, no duplicate | ✅ §9.3 |
| Idempotency: different hash → row updated, `updated_at` bumped | ✅ §9.4 |
| Idempotency: different `file_path` → separate row | ✅ §9.5 |
| FK enforcement (`repositories.id`, `ON DELETE CASCADE`) | ✅ §9.6, §9.8 |
| Transaction commit/rollback verified | ✅ §9.9, `TestTransactionBehavior` |
| Parameterized SQL only | ✅ §9, `TestParameterizedSQL` |
| Typed exception (`ContentPersistenceError`), extends existing hierarchy | ✅ §4, §12 |
| No SQLAlchemy/ORM models, no new DB abstraction, no new unrelated dependency | ✅ Confirmed by inspection — only `psycopg` (already a dependency) |
| Deterministic tests, no live dependency in normal execution | ✅ §8, 20/20 pass |
| Live PostgreSQL verification with independent SQL evidence | ✅ §9 |
| Security verification | ✅ §12 |
| Full regression suite green | ✅ 238 passed, 4 skipped, 0 failed |
| 1.4.a / 1.4.b untouched | ✅ Confirmed by `git diff` |
| `DATABASE_DESIGN.md` not retroactively modified | ✅ §2 |
| No progression to 1.4.d | ✅ Confirmed — no orchestration/batch code added |

**Sub-checkpoint 1.4.c is COMPLETE and VERIFIED.**

---

## 15. Git

- **Commit hash:** *(recorded after commit — see final message)*
- **Commit message:** `Checkpoint 1.4.c: Repository content persistence`
- **Push status:** *(recorded after push — see final message)*

---

## 16. What This Enables Next

1.4.d (multi-repository orchestration across clone → extract → persist, plus disk-space policy across a batch) can now be built directly against `ContentWriter.upsert_content(repository_id, files)`'s contract.

**Not started. Awaiting review before proceeding to 1.4.d.**
