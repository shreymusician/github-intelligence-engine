# Migration 008 Report — Repository Embeddings (Semantic Vectors)

**Checkpoint:** 0.2 (Implementation, Step 8 of 9)
**Migration ID:** `008` (`008_repository_embeddings.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-03

---

## 1. Purpose

Per `DATABASE_DESIGN.md` §2.11 and `REPOSITORY_INTELLIGENCE_ENGINE.md` §3, this migration creates `repository_embeddings`: model-scoped dense vector representations of each repository (generated from README + technology stack + metadata), enabling semantic search and similarity computation.

It also resolves `DATABASE_ARCHITECT_REVIEW.md` Finding 7 (High severity): the original design typed the embedding column `vector(1536)` (OpenAI `text-embedding-ada-002` dimensionality), while the project's actual V1 tooling (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §5–6) is Hugging Face Sentence Transformers, which does not produce 1536-dimensional output. The column is typed `vector(768)`, matching a standard Sentence Transformers model (e.g. `all-mpnet-base-v2`).

---

## 2. Phase 0 — Infrastructure Prerequisite (pgvector Upgrade)

Before continuing Migration 008, the required pgvector infrastructure was verified/completed:

| Check | Result |
|---|---|
| Docker image is `pgvector/pgvector:pg17` (not a different PostgreSQL major version) | ✅ `docker-compose.yml` already declared this image; container was already running it |
| Existing Docker volume preserved | ✅ `github-ai-analytics_repo_intelligence_data` reused unchanged — no data loss, no reinitialization |
| PostgreSQL starts successfully | ✅ `17.10 (Debian 17.10-1.pgdg12+1)` |
| Docker health check passes | ✅ `healthy` |
| `CREATE EXTENSION IF NOT EXISTS vector` succeeds | ✅ (`already exists, skipping` — extension was already installed) |
| `pg_extension` confirms `vector` installed | ✅ `vector` 0.8.6 |
| No previous migration required modification | ✅ Confirmed — the extension is infrastructure, not schema; Migrations 001–007 are untouched |

**Anomaly discovered and resolved before continuing:** `alembic_version` was found to already read `008`, with a `repository_embeddings` table already present in the database (structurally identical to the migration file), despite `DATABASE_STATUS.md` and this report not yet existing. This indicated the migration had been applied in a prior, undocumented session. Per explicit instruction, the database was rolled back to `007` first (see §7) to re-establish a clean, documented baseline before re-running the full Migration 008 workflow from scratch.

---

## 3. Architectural Rationale

### 3.1 Why separate from `repository_scores` (Migration 007)

Different data shape and purpose entirely: `repository_scores` holds scalar intelligence outputs; `repository_embeddings` holds dense vectors for semantic similarity. This is the first table with a genuinely different storage/index type (pgvector), isolated so a future embedding-model migration or index-tuning migration has an isolated blast radius.

### 3.2 Why model-scoped, not a single vector column

An embedding is meaningless without knowing which model produced it — a cosine-similarity comparison between vectors from two different models is not valid. Every row is scoped to `(repository_id, model_name)`; `model_version` is tracked alongside for finer-grained versioning within a model family. Lifecycle: recomputed when source text changes meaningfully or a new/upgraded model is adopted; old model's rows are NOT deleted, enabling comparison of search quality across model versions (`DATABASE_DESIGN.md` §2.11).

### 3.3 Why `vector(768)`

Per Architect Review Finding 7 (High severity) — see §1. Supporting a second, differently-dimensioned model later is explicitly out of V1 scope (would require a new table or column-width migration) — a deliberate YAGNI call, not a gap.

### 3.4 Why no ANN index in this migration

`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.12 explicitly defers the specific ANN index type (HNSW/IVFFlat) and parameters to a Milestone 4.2 implementation decision, "not fixed at schema-design time, since it depends on eventual corpus size." Only the implicit btree backing the composite PK exists here.

---

## 4. Dependency Analysis

### 4.1 Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
008_embeddings
        │
        └── repository_embeddings
              └── FK → repositories.id  (CASCADE)

(No schema dependency on 001, 003, 004, 005, 006, or 007. Alembic chain
 position 007->008 is linear-ordering only. 009_similarity has only a
 soft/application-level dependency on this migration - no DB-level FK -
 similarity_method can be 'embedding_cosine' (sourced from this table)
 or 'feature_distance' (sourced from 006 instead).)
```

### 4.2 Dependencies

- **Schema (hard) dependency:** `002_core_entities` only, via `repository_id` FK → `repositories.id`.
- **No schema dependency** on 001, 003, 004, 005, 006, or 007.

### 4.3 Future migrations depending on it

`009_similarity` has only a soft/application-level dependency (no DB-level FK).

---

## 5. Design Verification (Phase 1) — No Ambiguity Found

The migration file's docstring cites `DATABASE_DESIGN.md` §2.11, `REPOSITORY_INTELLIGENCE_ENGINE.md` §3, `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.12, and `DATABASE_ARCHITECT_REVIEW.md` Finding 7 as the frozen design sources — all mutually consistent. No stop-and-report was required for the schema itself (the only stop-and-report was the Phase 0 anomaly in §2, resolved via explicit user direction).

---

## 6. Objects Created

### 6.1 `repository_embeddings`

| Column | Type | Constraint |
|---|---|---|
| `repository_id` | uuid | PK (part 1), NOT NULL, FK → `repositories.id` (CASCADE) |
| `model_name` | text | PK (part 2), NOT NULL |
| `model_version` | text | NOT NULL |
| `embedding` | vector(768) | NOT NULL |
| `source_text_hash` | text | NOT NULL |
| `computed_at` | timestamptz | NOT NULL, default `now()` |

**PK strategy:** composite `(repository_id, model_name)`, per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.12. Both PK columns are implicitly NOT NULL by SQL PK semantics, same treatment as `repository_technologies`'s composite PK in Migration 003.

**No new enum type** introduced by this migration.

---

## 7. Constraints

| Constraint | Verification method | Result |
|---|---|---|
| `pk_repository_embeddings` (composite PK) | `\d repository_embeddings` | ✅ Present |
| `repository_id NOT NULL` | NULL insert | ✅ Rejected |
| `model_name NOT NULL` | NULL insert | ✅ Rejected |
| `model_version NOT NULL` | NULL insert | ✅ Rejected |
| `embedding NOT NULL` | NULL insert | ✅ Rejected |
| `source_text_hash NOT NULL` | NULL insert | ✅ Rejected |
| `embedding` dimension = 768 | Insert 384-dim vector | ✅ Rejected: `expected 768 dimensions, not 384` |
| Duplicate `(repository_id, model_name)` | Insert same pair twice | ✅ Rejected: `duplicate key value violates unique constraint "pk_repository_embeddings"` |
| Model-scoping allows multiple models per repo | Insert same `repository_id`, different `model_name` | ✅ Succeeded — two independent rows |

---

## 8. Foreign Keys

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_embeddings_repository_id_repositories` | `repository_embeddings.repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed; functionally tested (invalid-FK rejection + CASCADE deletion) |

This is the only foreign key in this table.

---

## 9. Indexes

| Index | Type | Purpose |
|---|---|---|
| `pk_repository_embeddings` | btree (composite PK) | Primary key lookup / model-scoped uniqueness |

No ANN index (HNSW/IVFFlat) is created in this migration — deliberately deferred per §3.4. `EXPLAIN` on a nearest-neighbor query (see §10, Test 12) confirmed the planner uses a `Seq Scan` + `Sort` on the `<=>` distance operator, consistent with no vector index existing yet.

---

## 10. Verification Performed

**Structural (Phase 4):** `\dt`, `\d repository_embeddings`, `alembic_version` — all 6 columns, types, defaults, nullability, composite PK, and FK matched the migration file exactly. 13 tables total (12 from Migrations 001–007 + `repository_embeddings`).

**Functional (Phase 5):**

| # | Scenario | Result |
|---|---|---|
| 1 | Valid insert (768-dim vector, all columns) | ✅ Inserted and retrieved intact |
| 2 | `NULL repository_id` | ✅ Rejected via NOT NULL |
| 3 | `NULL model_name` | ✅ Rejected via NOT NULL |
| 4 | `NULL model_version` | ✅ Rejected via NOT NULL |
| 5 | `NULL embedding` | ✅ Rejected via NOT NULL |
| 6 | `NULL source_text_hash` | ✅ Rejected via NOT NULL |
| 7 | Wrong vector dimension (384 instead of 768) | ✅ Rejected: `expected 768 dimensions, not 384` |
| 8 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 9 | Duplicate `(repository_id, model_name)` | ✅ Rejected via composite PK |
| 10 | Same `repository_id`, different `model_name` (model-scoping) | ✅ Succeeded — 2 independent rows |
| 11 | Nearest-neighbor query (`<=>` cosine distance operator, `ORDER BY ... LIMIT`) | ✅ Executed correctly; nearest vectors ranked by ascending cosine distance |
| 12 | `EXPLAIN` on nearest-neighbor query | ✅ `Seq Scan` + `Sort` on `<=>` — confirms no ANN index exists yet, matching frozen design |
| 13 | `ON DELETE CASCADE`: delete the repository, confirm all embedding rows removed | ✅ 3 rows before, 0 after |

All test data (`repository_embeddings`, `repositories`, `owners`) deleted after verification; confirmed empty via count query before proceeding to rollback testing.

---

## 11. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 008 -> 007, 008_embeddings` |
| Table dropped | `\dt` | ✅ `repository_embeddings` removed; all 11 Migration 001–007 tables + `alembic_version` remained (12 tables total) |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `007` |
| Migration 007 object intact | `\d repository_scores` | ✅ Unaffected, structurally identical to pre-rollback state |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 007 -> 008, 008_embeddings` |
| Table recreated | `\dt` | ✅ All 13 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `008` |
| Table empty after re-upgrade | `SELECT count(*) FROM repository_embeddings` | ✅ `0` |

No orphaned objects in either direction. `downgrade()` drops the table only (no enum to tear down, unlike Migrations 004/007).

---

## 12. Definition of Done

| Criterion | Status |
|---|---|
| Phase 0 infrastructure (pgvector image, extension) verified before continuing | ✅ |
| Migration file matches frozen design exactly, no ambiguity encountered | ✅ |
| `alembic upgrade head` applies cleanly from `007` | ✅ |
| Table created with correct columns/types/defaults (6 columns) | ✅ |
| Composite PK `(repository_id, model_name)` per §14.12's PK strategy | ✅ |
| FK present, `ON DELETE CASCADE`, functionally tested | ✅ |
| `embedding` typed `vector(768)`, dimension mismatch rejected | ✅ |
| `NOT NULL` tested on all 5 required columns | ✅ |
| Model-scoping behavior functionally proven (multiple models per repo) | ✅ |
| Nearest-neighbor query functionally correct | ✅ |
| `EXPLAIN` confirms no ANN index exists yet (matches deferred-index design) | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001–007 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `007` after rollback and `008` after re-upgrade | ✅ |
| No previous migration modified | ✅ |
| No progression to Migration 009 | ✅ |

**Migration 008 is COMPLETE and VERIFIED.**

---

## 13. Observations (Frozen Design Not Modified)

1. **Phase 0 anomaly, resolved, not a design issue.** The pgvector Docker image swap (`postgres:17-alpine` → `pgvector/pgvector:pg17`) had already been applied to `docker-compose.yml` and the running container in a prior, undocumented session — along with an undocumented application of Migration 008 itself. Both were discovered during verification. The infrastructure change required no action (already correct); the migration state was reset to `007` and Migration 008 re-run in full per explicit instruction, so this report reflects a clean, from-scratch execution of the documented 7-phase workflow.
2. **No design ambiguity encountered** in the schema itself — the migration file's docstring is fully and unambiguously specified against five frozen source documents.
3. **No design deviation.** Every column, constraint, and index in this migration is a direct, literal implementation of the frozen design; the deliberate absence of an ANN index is confirmed, not an oversight.
4. **Architect Review Finding 7 (High severity) is now physically realized**: `vector(768)`, not `vector(1536)`.

---

## 14. Suggested Git Commit

```
Checkpoint 0.2: Migration 008 - Repository embeddings (semantic vectors)

Creates repository_embeddings: model-scoped dense vector representations
of each repository (README + tech stack + metadata), enabling semantic
search and similarity computation, per DATABASE_DESIGN.md Sec2.11 and
REPOSITORY_INTELLIGENCE_ENGINE.md Sec3.

Infrastructure prerequisite: postgres image upgraded to
pgvector/pgvector:pg17 (same PostgreSQL major version, existing data
volume preserved). Verified: container starts healthy, vector extension
installs, all Migration 001-007 tables and enum types intact, no prior
migration required modification.

Resolves DATABASE_ARCHITECT_REVIEW.md Finding 7 (High severity):
embedding column typed vector(768), matching the project's actual V1
Sentence Transformers tooling, not the original vector(1536)
(OpenAI-ada-002-shaped) column that would have broken on first use.

Model-scoped, not a single vector column: composite PK
(repository_id, model_name), since a similarity comparison between
vectors from two different models is not valid. model_version tracked
alongside for within-model-family versioning. Old model rows are not
deleted on re-embedding, enabling cross-model-version search-quality
comparison.

Deliberately separate from repository_scores (Migration 007): first
table with a genuinely different storage/index type (pgvector), isolated
so a future embedding-model or index-tuning migration has an isolated
blast radius. No ANN index (HNSW/IVFFlat) created here - deferred to a
Milestone 4.2 implementation decision per
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.12, since index choice depends on
eventual corpus size.

Only FK dependency is repositories.id (Migration 002). Only a
soft/application-level dependency for 009_similarity - no DB-level FK.

Verified: upgrade, NOT NULL on all 5 required columns, vector-dimension
rejection (384-dim insert fails), FK (rejection + functional CASCADE
deletion), composite-PK duplicate rejection, model-scoping (multiple
models per repository), nearest-neighbor query correctness via the <=>
cosine-distance operator, EXPLAIN-confirmed absence of an ANN index
(Seq Scan + Sort, matching the deferred-index design), downgrade (clean
drop, Migrations 001-007 untouched), and re-upgrade.

No design deviation - direct implementation of DATABASE_DESIGN.md
Sec2.11, REPOSITORY_INTELLIGENCE_ENGINE.md Sec3, and
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.12.
```

---

**Status:** Migration 008 complete. Awaiting review before beginning Migration 009 (`009_similarity`: `repository_similarity`).
