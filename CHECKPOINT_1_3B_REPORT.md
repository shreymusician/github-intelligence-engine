# Checkpoint 1.3.b Report — Repository Storage Layer

**Parent Checkpoint:** 1.3 (Repository Acquisition Pipeline), Sub-checkpoint b of e
**Status:** ✅ Complete, verified (deterministic mocked tests + live local PostgreSQL)
**Date:** 2026-08-07

---

## 1. Purpose

Persist a single `github_client.rest.RepositoryData` into PostgreSQL, across exactly the tables Data Acquisition owns per `DATABASE_DESIGN.md` §5: `owners`, `licenses`, `topics`, `repositories`, `repository_topics`, `repository_snapshot`. Fetching (Checkpoint 1.1), selecting candidates (1.3.a), and tying selection → fetch → storage into one run (1.3.c) are explicitly not this module's job — `RepositoryWriter` never imports `github_client.rest.GitHubRESTClient`/`GitHubGraphQLClient` and never makes an HTTP request.

---

## 2. Phase 1 — Design Verification (confirmed before coding)

1. **Why storage is separate from selection.** `DATABASE_DESIGN.md` §5 assigns table writes to the Data Acquisition module as a whole, but `IMPLEMENTATION_ROADMAP.md`'s own Checkpoint 1.3 breakdown separates "which repos" (1.3.a, a decision, no side effects) from "how they're stored" (1.3.b, side effects, transactional, needs a live database) — the same separation `CHECKPOINT_1_3A_REPORT.md` §8 already documented as the reason 1.3.a has zero `psycopg` dependency.
2. **Tables owned by Module 1 (Data Acquisition), per `DATABASE_DESIGN.md` §5:** `owners`, `repositories`, `licenses`, `topics` (all four), and `repository_snapshot`. This checkpoint writes to exactly those five tables plus the `repository_topics` join table (an M:N edge between two Module-1-owned entities, not a separate module's data).
3. **Tables explicitly NOT owned by this checkpoint** (and therefore untouched by `RepositoryWriter`): `technologies` / `repository_technologies` / `repository_dependencies` (Feature Engineering, Module 3), `repository_metrics` (Feature Engineering), `repository_scores` (Intelligence Generation, Module 4), `repository_embeddings` (Feature Engineering), `repository_similarity` (Intelligence Generation). Confirmed by inspection: no SQL in `acquisition/storage.py` references any of these six tables.
4. **Required insert/upsert order (FK constraints, Migrations 002/003/005):**
   `owners` → `licenses` → `topics` → `repositories` → `repository_topics` → `repository_snapshot`.
   `repositories.owner_id` is `NOT NULL` (owner must exist first); `repositories.license_id` is a nullable FK (license must exist first, when present); `repository_topics` FKs into both `repositories.id` and `topics.id`; `repository_snapshot` FKs into `repositories.id`. Licenses and topics have no FK relationship to each other or to owners, so their relative order versus each other is arbitrary — both are upserted before `repositories` simply because `repositories` needs their generated ids.
5. **Transaction boundaries:** one PostgreSQL transaction per `upsert_repository(data)` call, covering all six write steps (owner, license, topics, repository row, topic links, snapshot). Implemented via psycopg3's `Connection.transaction()` context manager, which commits on clean exit and rolls back on any exception raised inside it — chosen over manual `BEGIN`/`COMMIT`/`ROLLBACK` so the rollback path cannot be silently skipped by a future edit that forgets an `except` branch.
6. **Idempotency expectations:** every write is an upsert (`ON CONFLICT ... DO UPDATE`), keyed on the same natural/unique key GitHub's response is already keyed on — `github_id` for owners/repositories, `spdx_id` for licenses, `slug` for topics, `(repository_id, snapshot_date)` for the daily snapshot row. Re-running `upsert_repository()` with the same `RepositoryData` on the same day produces the same rows, not duplicates — confirmed live (§6).

**One ambiguity was found and resolved before coding, not silently:** the checkpoint's requested method list names five per-table steps (`upsert_owner`, `upsert_license`, `upsert_topics`, `upsert_repository`, `insert_repository_snapshot`) *and* a public orchestrating method with the same repositories-table name, `upsert_repository(data)`. Naming the per-table repositories-row step identically to the public entry point would collide on the same class. Resolved by implementing the five per-table steps as private module-level helper functions (`_upsert_owner`, `_upsert_license`, `_upsert_topics`, `_upsert_repository`, `_insert_repository_snapshot`, plus `_link_repository_topics` for the join step), each doing exactly the one table's upsert the checkpoint describes, and exposing exactly one public method on `RepositoryWriter` — `upsert_repository(data)` — that orchestrates them in FK order. Documented in `acquisition/storage.py`'s module docstring.

No other ambiguity was found.

---

## 3. Architecture

```
acquisition/storage.py
├── _upsert_owner(conn, owner)                    -> owners.id
├── _upsert_license(conn, spdx_id, name)           -> licenses.id | None
├── _upsert_topics(conn, topics)                   -> list[topics.id]
├── _upsert_repository(conn, data, owner_id, ...)  -> repositories.id
├── _link_repository_topics(conn, repo_id, ...)    -> None
├── _insert_repository_snapshot(conn, repo_id, ...) -> None
└── RepositoryWriter
    ├── __init__(settings=None, connection_factory=None)
    └── upsert_repository(data: RepositoryData) -> uuid.UUID   [public API]

acquisition/exceptions.py
├── AcquisitionStorageError (base)
└── RepositoryPersistenceError (wraps the underlying psycopg error)
```

`RepositoryWriter` holds no long-lived connection — each `upsert_repository()` call opens, uses, and closes its own `psycopg.Connection`, matching `acquisition/selection.py`'s `RepositorySelector` precedent of holding no state beyond what it was constructed with (`CHECKPOINT_1_3A_REPORT.md` §4). A `connection_factory` can be injected for testing without a real database; production code builds one from `config.settings.get_settings()`'s `postgres_*` fields.

**`config/settings.py` extended, not modified in behavior:** `Settings` gained five fields (`postgres_host`, `postgres_port`, `postgres_db`, `postgres_user`, `postgres_password`), read from the same `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_USER_PASSWORD` variables `alembic/env.py` already reads (names and defaults mirrored exactly, not invented). None are required — `GITHUB_TOKEN` remains the only fail-fast variable — since not every `get_settings()` caller needs a database connection; a real connection failure surfaces naturally when `RepositoryWriter` attempts to connect. `__repr__` was extended to redact `postgres_password` the same way `github_token` was already redacted. No existing test asserts an exact `repr()` string (only substring checks), and `Settings(**fields)` construction in `tests/test_github_client.py` is unaffected since the new fields carry defaults — confirmed by the full regression run (§7).

---

## 4. Table Ownership (this checkpoint's writes)

| Table | Written by this checkpoint | Key | Upsert conflict target |
|---|---|---|---|
| `owners` | ✅ | `github_id` | `ON CONFLICT (github_id)` |
| `licenses` | ✅ (only when `license_spdx_id` present) | `spdx_id` | `ON CONFLICT (spdx_id)` |
| `topics` | ✅ | `slug` | `ON CONFLICT (slug)` |
| `repositories` | ✅ | `github_id` | `ON CONFLICT (github_id)` |
| `repository_topics` | ✅ | `(repository_id, topic_id)` | `ON CONFLICT ... DO NOTHING` |
| `repository_snapshot` | ✅ | `(repository_id, snapshot_date)` | `ON CONFLICT ... DO UPDATE` |
| `technologies`, `repository_technologies`, `repository_dependencies`, `repository_metrics`, `repository_scores`, `repository_embeddings`, `repository_similarity` | ❌ Not touched | — | — |

---

## 5. Key Design Decisions

- **`owners.account_type` case translation.** `OwnerSummary.account_type` (Checkpoint 1.1) faithfully preserves GitHub's `"User"`/`"Organization"` casing (documented, unmodified project decision). `owners.account_type` is a lower-case enum (`'user'`/`'organization'`, Migration 002). Translated at the storage boundary (`_upsert_owner`), not by changing `OwnerSummary` — keeps the API DTO a faithful mirror of GitHub's response, per the same principle `CHECKPOINT_1_1_FINAL_REPORT.md` §12.1 already established for `RepositoryData`.
- **`is_accessible` / `last_extraction_status` set unconditionally on write.** Reaching `_upsert_repository` at all means `RepositoryData` was just successfully fetched (Checkpoint 1.1), so `is_accessible=true` / `inaccessible_since=NULL` / `last_extraction_status='success'` are set every time, not conditionally. Recording `'pending'`/`'failed'` extraction outcomes, or flipping `is_accessible=false` on a confirmed-gone repository, is out of this checkpoint's scope — that requires the orchestration pipeline (1.3.c) to call this module *inside* its own error handling, which does not exist yet.
- **`licenses.is_osi_approved` left at its schema default.** GitHub's embedded repository `license` object (the only license data Checkpoint 1.1 fetches) does not carry OSI-approval status. Rather than fabricate it, the column is left untouched by both insert and conflict-update — a real, named gap, not silently defaulted to a guessed value.
- **`topics.name` is set equal to `topics.slug`.** GitHub's repository topics are already slug-form strings (e.g. `"machine-learning"`) with no separate display name available from the single endpoint Checkpoint 1.1 calls. Both columns hold the same string; this is a data-availability limitation, not a modeling error (`topics.name` remains a distinct, correctly-typed column for the day a richer topic source exists).
- **`repository_topics` links are additive-only.** A topic previously associated but absent from the current `data.topics` is not unlinked. No query in `QUERY_DRIVEN_SCHEMA_DESIGN.md`'s workload requires topic-removal tracking (unlike `repository_technologies.removed_at`, an explicit, different requirement for a different table) — logged as a known limitation (§8), not silently ignored.
- **`repository_snapshot` upserts, not plain-inserts, on `(repository_id, snapshot_date)`.** A same-day re-run of `upsert_repository()` updates that day's counters instead of raising a unique-violation — this is what makes idempotency hold for the whole call, not just the `repositories` row.

---

## 6. Verification Performed

### 6.1 Live verification against the real local PostgreSQL database

Database: `repo_intelligence_postgres` (Docker container, `pgvector/pgvector:pg17`, all 9 Alembic migrations already applied — confirmed via `information_schema.tables` before testing). Connected using `RepositoryWriter`'s default `Settings`-derived connection (`postgres_user=repo_user`, matching `.env`) — confirming the settings extension (§3) actually works end-to-end, not just in isolation.

**30/30 checks passed**, covering every item Phase 3 required:

| Area | Result |
|---|---|
| Owner insert | ✅ Row created, `account_type` correctly lower-cased (`"Organization"` → `'organization'`) |
| Owner update | ✅ Re-running with a changed `login` updates the existing row (still exactly 1 row for that `github_id`, not 2) |
| License upsert | ✅ Row created (`spdx_id='MIT'`, `name='MIT License'`) |
| Repository upsert | ✅ Row created; `is_accessible=true`, `last_extraction_status='success'` |
| Topic creation | ✅ Both topics (`machine-learning`, `python-library`) created |
| `repository_topics` links | ✅ Exactly 2 rows, matching the 2 topics on the test repository |
| `repository_snapshot` insertion | ✅ Row created with correct `stars_count`/`forks_count`/`size_kb` |
| Foreign-key integrity | ✅ Zero orphan `repository_topics` rows, zero orphan `repository_snapshot` rows, zero orphan `repositories.owner_id` references |
| Transaction rollback | ✅ A deliberately malformed write (NULL into `repositories.github_created_at`, a `NOT NULL` column) raised `RepositoryPersistenceError`; the `repositories` row was NOT created, AND the license/topic that were upserted earlier in that same failed transaction were also NOT committed — confirming rollback covers the whole transaction, not just the step that failed |
| Idempotent repeated writes | ✅ Re-running `upsert_repository()` with the same `github_id` (different field values) returned the same `repositories.id`, updated fields in place, and left `repository_snapshot` at exactly 1 row for the day (updated, not duplicated) |
| Timestamps correct | ✅ `created_at <= updated_at`; `fetched_at` populated |
| No duplicate `github_id` values | ✅ Confirmed via `GROUP BY ... HAVING count(*) > 1` on both `owners` and `repositories` — zero rows |
| No orphan records | ✅ (see FK integrity row above) |
| No-license repository | ✅ A repository with `license_spdx_id=None` correctly stores `repositories.license_id = NULL` (not an error) |

Full transcript: 1 successful insert → 1 owner-changed/description-changed re-run (idempotency + update) → 1 no-license repository → 1 deliberate rollback trigger, each with its own targeted assertions, run via a throwaway verification script (not part of the delivered code) against the live database.

### 6.2 SQL verification queries (run against every table touched, post-write, pre-cleanup)

```sql
SELECT github_id, login, account_type, avatar_url FROM owners WHERE github_id=999001;
-- (999001, 'verify-owner', 'organization', 'https://example.invalid/a.png')

SELECT spdx_id, name, is_osi_approved FROM licenses WHERE spdx_id='MIT';
-- ('MIT', 'MIT License', False)

SELECT slug, name FROM topics WHERE slug IN ('machine-learning','python-library') ORDER BY slug;
-- ('machine-learning', 'machine-learning')
-- ('python-library', 'python-library')

SELECT github_id, full_name, is_accessible, last_extraction_status,
       license_id IS NOT NULL AS has_license
FROM repositories WHERE github_id IN (555001,555002) ORDER BY github_id;
-- (555001, 'verify-owner/verify-repo', True, 'success', True)
-- (555002, 'verify-owner/no-license-repo', True, 'success', False)

SELECT r.full_name, t.slug FROM repository_topics rt
JOIN repositories r ON r.id=rt.repository_id
JOIN topics t ON t.id=rt.topic_id
WHERE r.github_id=555001 ORDER BY t.slug;
-- ('verify-owner/verify-repo', 'machine-learning')
-- ('verify-owner/verify-repo', 'python-library')

SELECT r.full_name, s.snapshot_date, s.stars_count, s.forks_count, s.size_kb
FROM repository_snapshot s JOIN repositories r ON r.id=s.repository_id
WHERE r.github_id=555001;
-- ('verify-owner/verify-repo', 2026-08-07, 2000, 56, 890)   -- 2000 = post-update value

-- FK integrity (orphan check across all three relationships this checkpoint creates)
SELECT
  (SELECT count(*) FROM repository_topics rt LEFT JOIN repositories r ON r.id=rt.repository_id WHERE r.id IS NULL) AS orphan_topics_links,
  (SELECT count(*) FROM repository_snapshot s LEFT JOIN repositories r ON r.id=s.repository_id WHERE r.id IS NULL) AS orphan_snapshots,
  (SELECT count(*) FROM repositories r LEFT JOIN owners o ON o.id=r.owner_id WHERE o.id IS NULL) AS orphan_owner_refs;
-- (0, 0, 0)

-- No duplicate github_id
SELECT
  (SELECT count(*) FROM (SELECT github_id FROM owners GROUP BY github_id HAVING count(*)>1) x) AS dup_owners,
  (SELECT count(*) FROM (SELECT github_id FROM repositories GROUP BY github_id HAVING count(*)>1) x) AS dup_repos;
-- (0, 0)

-- Rollback proof: the deliberately-failed write never committed
SELECT count(*) FROM repositories WHERE github_id=555999;
-- (0,)
```

All verification rows (`github_id` 999001/555001/555002/555999, `spdx_id` MIT, test topics) were deleted after evidence capture — this is a storage-layer unit checkpoint, not the acquisition pipeline; no real repository data should be left in the database by this checkpoint's own testing. Post-cleanup row counts for all six touched tables: **0** (confirmed).

### 6.3 Full regression suite

```
109 passed, 2 skipped in 8.56s
```

Run both before any code was written (baseline) and after all `acquisition/storage.py`, `acquisition/exceptions.py`, `acquisition/__init__.py`, and `config/settings.py` changes — identical result both times. No existing test (including `tests/test_github_client.py`'s `Settings(**fields)` construction and redaction assertions) regressed from the `Settings` field additions.

---

## 7. Definition of Done

| Criterion | Status |
|---|---|
| `RepositoryWriter` created, with the five per-table methods and one public `upsert_repository(data)` | ✅ (naming collision resolved per §2's documented decision) |
| Owner insert | ✅ Live-verified |
| Owner update | ✅ Live-verified (no duplicate row) |
| License upsert | ✅ Live-verified |
| Repository upsert | ✅ Live-verified |
| Topic creation | ✅ Live-verified |
| `repository_topics` links | ✅ Live-verified |
| `repository_snapshot` insertion | ✅ Live-verified |
| Foreign-key integrity | ✅ Live-verified (zero orphans across all three FK relationships) |
| Transaction rollback | ✅ Live-verified (whole transaction, not just the failing statement) |
| Idempotent repeated writes | ✅ Live-verified |
| Timestamps correct | ✅ Live-verified |
| No duplicate `github_id` values | ✅ Live-verified |
| No orphan records | ✅ Live-verified |
| SQL verification queries run for every table touched | ✅ §6.2 |
| Full pytest suite run afterward, no regressions | ✅ 109 passed, 2 skipped, before and after |
| No GitHub API calls | ✅ Confirmed by inspection — no `github_client.rest`/`.graphql` import in `acquisition/storage.py` |
| No acquisition pipeline / orchestration | ✅ Confirmed by inspection — no loop over candidates, no call to `RepositorySelector` |
| No retry logic | ✅ Confirmed — no retry/backoff code; a failed write raises once, immediately |
| No feature engineering, embeddings, similarity | ✅ Confirmed — none of those six tables (§4) are referenced |
| No schema/migration changes | ✅ Confirmed — no file under `alembic/` was modified |
| Reuses `psycopg`, existing config, existing logging, existing exception hierarchy | ✅ `psycopg` (already a dependency, used by `alembic/env.py`), `config.settings.get_settings()` (extended, not replaced), `logging_setup.get_logger()`, and `acquisition/exceptions.py` mirrors `github_client/exceptions.py`'s established pattern |

**Sub-checkpoint 1.3.b is COMPLETE and VERIFIED.**

---

## 8. Known Limitations

- **`repository_topics` links are additive-only** (§5) — a topic removed from a repository on GitHub since the last write is not unlinked here. No roadmap query through Milestone 5 requires this; flagged for whoever implements topic-removal tracking, if it's ever needed (mirroring `repository_technologies.removed_at`'s already-solved version of this same problem for a different table).
- **`licenses.is_osi_approved` is never populated by this module** — GitHub's embedded license object doesn't carry it; left at schema default (`false`) rather than guessed.
- **No retry or rate-limiting concern applies here** (by design, not omission) — this module makes zero GitHub API calls; retry/rate-limiting remain entirely Checkpoint 1.1's and 1.3.a's concerns, composed by whatever caller feeds this module (Checkpoint 1.3.c).
- **`config/settings.py`'s new `postgres_*` fields are not fail-fast** — unlike `GITHUB_TOKEN`, a missing/wrong Postgres credential surfaces only when `RepositoryWriter` actually tries to connect, not at `get_settings()` call time. Consistent with Checkpoint 1.1.a's original scope decision (only variables *that checkpoint* needs are required) extended the same way for 1.3.b.
- **Pre-existing environment-variable drift, not introduced or fixed here:** `docker/docker-compose.yml` (Checkpoint 0.1) sources its Postgres password from `${POSTGRES_PASSWORD}` while the project's actual `.env` and `alembic/env.py` use `POSTGRES_USER_PASSWORD` — a mismatch already called out in `.env`'s own inline comment. This checkpoint's `config/settings.py` extension deliberately follows `alembic/env.py`'s naming (the convention the live, already-migrated database actually uses, confirmed in §6.1), not `docker-compose.yml`'s. Not in this checkpoint's scope to fix (infra file, Checkpoint 0.1's ownership); noted so it isn't mistaken for a new defect.

---

## 9. Why Orchestration Is Intentionally Deferred to 1.3.c

`RepositoryWriter` never imports `github_client.rest.GitHubRESTClient`/`GitHubGraphQLClient`, never imports `acquisition.selection.RepositorySelector`, and contains no loop over a candidate list — there is no code path in `acquisition/storage.py` capable of fetching data or deciding which repositories to fetch. This isn't an oversight; it's the same sub-checkpoint discipline `CHECKPOINT_1_3A_REPORT.md` §8 established for selection: storage answers "how is one `RepositoryData` persisted," a self-contained transactional problem, testable (and tested, §6) against a real database with zero dependency on GitHub's API being reachable. Tying `RepositorySelector.select_candidates()`'s output to `GitHubRESTClient.get_repository()`'s output to `RepositoryWriter.upsert_repository()`'s input — including deciding what happens when an individual repository's fetch or write fails mid-batch, how progress is logged, and whether/how a run resumes — is a distinct engineering problem (batch control flow, partial-failure handling, resumability) reserved for Checkpoint 1.3.c, not yet started.

---

## 10. What This Enables Next

Checkpoint 1.3.c (Acquisition Orchestration Pipeline) can now be planned knowing exactly what `RepositorySelector.select_candidates()` produces (1.3.a: `list[str]` of `owner/repo` identifiers), what `GitHubRESTClient`/`GitHubGraphQLClient.get_repository()` produce (1.1: a fully-populated `RepositoryData`), and what `RepositoryWriter.upsert_repository()` consumes and guarantees (1.3.b: idempotent, transactional persistence, returning the repository's UUID or raising `RepositoryPersistenceError`). 1.3.c is the piece that will actually connect all three into one run, including this checkpoint's explicitly-deferred concerns: `last_extraction_status='failed'`/`'pending'` handling, `is_accessible=false` transitions for repositories that later 404, and resumable batch progress.

**Not started. Awaiting review before proceeding to 1.3.c.**
