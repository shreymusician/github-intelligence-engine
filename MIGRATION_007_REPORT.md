# Migration 007 Report — Repository Scores (Intelligence Output)

**Checkpoint:** 0.2 (Implementation, Step 7 of 9)
**Migration ID:** `007` (`007_repository_scores.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`007_intelligence_scores`) and `DATABASE_DESIGN.md` §2.10, this migration creates `repository_scores` — the intelligence layer's **output** table: difficulty score, quality score, and community-health classification, as produced by Milestone 3 (Intelligence Generation), per `SYSTEM_ARCHITECTURE.md` Module 4.

---

## 2. Architectural Rationale

### 2.1 Why separate from `repository_metrics`

`repository_metrics` (Migration 006) holds **inputs** — computed features (commit counts, README stats, dependency health). `repository_scores` holds **outputs** — scores *derived from* those features by scoring algorithms. Per `MIGRATION_STRATEGY.md` §2, the two tables have distinct owning modules (Feature Engineering vs. Intelligence Generation) and distinct consumer patterns (`repository_scores` is read by Search and Recommendations, not by Feature Engineering). Critically, **there is no database-level FK from `repository_scores` to `repository_metrics`** — scores reference `repositories` directly. The relationship exists only at the application layer (a scoring algorithm *reads* metrics to *produce* a score row), explicitly documented as a "soft/application-level dependency... not a schema dependency."

### 2.2 Why append-only and versioned

Per `DATABASE_DESIGN.md` §2.10: *"Scoring algorithms evolve... If quality score were a single column, changing the algorithm would silently rewrite history and make it impossible to answer 'did this repository's quality score go up because it improved, or because we changed the formula?'"* Every row carries `algorithm_version`, enabling side-by-side comparison and safe rollback — the same ADR-003 "versioned, auditable change" philosophy established in Checkpoint 0.1. Lifecycle is append-only, identical in spirit to `repository_metrics`: *"a 'current' score is simply the most recent row for a given `(repository, score_type)`"* — rows are never updated in place, and are removed only via `ON DELETE CASCADE` from the parent repository.

### 2.3 Why its own migration

Distinct owning module (Intelligence Generation, Milestone 3.1/3.2/3.5) from `repository_metrics` (Feature Engineering, Milestone 2) — isolating it keeps the intelligence-output schema independently evolvable from the feature-store schema.

---

## 3. Dependency Analysis

### 3.1 Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
007_intelligence_scores
        │
        └── repository_scores
              └── FK → repositories.id  (CASCADE)

(No schema dependency on 001, 003, 004, 005, or 006. Soft/application-
 level dependency only on 006_feature_store - scoring algorithms use
 metrics as input at the application layer, not via a database FK.
 Coordinated - but not FK-linked - with 005/006 for a future shared
 partitioning initiative on computed_at/snapshot_date.)
```

### 3.2 Dependencies

- **Schema (hard) dependency:** `002_core_entities` only, via `repository_id` FK → `repositories.id`.
- **No schema dependency** on 001, 003, 004, 005, or 006. Confirmed explicitly in `MIGRATION_STRATEGY.md` §2: *"this migration's only hard dependency is `002`."* Alembic chain position `006→007` is linear-ordering only.

### 3.3 Future migrations depending on it

**None by foreign key.** Per the `MIGRATION_STRATEGY.md` sequencing diagram, `008_embeddings` and `009_similarity` both depend only on `002`, with `006→007` and `008→009` drawn "for logical/application-layer reading order even though no DB-level FK enforces them." `repository_scores` is a schema leaf, like `repository_snapshot` and `repository_metrics`.

---

## 4. Design Verification (Phase 1) — No Ambiguity Found

`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.11 fully specifies the table: 7 columns, one non-unique index, JSONB justification, and partition key — all mutually consistent with `MIGRATION_STRATEGY.md`, `DATABASE_DESIGN.md` §2.10, `DATABASE_ARCHITECT_REVIEW.md`, and `DATABASE_DESIGN_CHANGELOG.md`. Two points were specifically checked and confirmed **not** to be oversights:

1. **No `UNIQUE` constraint** on `(repository_id, score_type)` — deliberate, unlike `repository_snapshot`/`repository_metrics` (Migrations 005/006), which both enforce `UNIQUE (repository_id, snapshot_date)`. Append-only versioning requires multiple rows per `(repository_id, score_type)` over time (each new `algorithm_version` or recomputation adds a row rather than overwriting).
2. **No `CHECK` constraint** on `score_value` — confirmed absent from §14.11 and all other frozen documents (unlike `repository_metrics.top_contributor_commit_share`'s documented 0–1 CHECK, added per a named Phase 2 finding). Nothing analogous exists for `score_value`, so none was added.

No stop-and-report was required for this migration.

---

## 5. Objects Created

### 5.1 `repository_scores`

| Column | Type | Constraint |
|---|---|---|
| `id` | bigint | PK, `GENERATED ALWAYS AS IDENTITY` |
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `score_type` | enum(`difficulty`, `quality`, `community_health`) | NOT NULL |
| `score_value` | numeric(5,2) | NOT NULL |
| `score_breakdown` | jsonb | nullable — component contributions genuinely vary per `score_type` |
| `algorithm_version` | text | NOT NULL |
| `computed_at` | timestamptz | NOT NULL, default `now()` |

**PK strategy:** `BIGINT GENERATED ALWAYS AS IDENTITY` — per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13, consistent with `repository_dependencies`, `repository_snapshot`, `repository_metrics`.

**New enum type:** `score_type` (`difficulty`, `quality`, `community_health`) — verified via `pg_enum`, correct values and order.

---

## 6. Constraints

| Constraint | Verification method | Result |
|---|---|---|
| `pk_repository_scores` (PRIMARY KEY on `id`) | `\d repository_scores` | ✅ Present |
| `repository_id NOT NULL` | NULL insert | ✅ Rejected |
| `score_type NOT NULL` | NULL insert | ✅ Rejected |
| `score_value NOT NULL` | NULL insert | ✅ Rejected |
| `algorithm_version NOT NULL` | NULL insert | ✅ Rejected |
| `score_type` enum domain | Insert invalid value `'popularity'` | ✅ Rejected: `invalid input value for enum score_type` |
| No `UNIQUE` constraint (deliberate) | Insert same `(repository_id, score_type)` twice with different `algorithm_version`/`computed_at` | ✅ Both succeeded — confirms append-only versioning is not blocked |
| No `CHECK` constraint on `score_value` (deliberate) | N/A — none documented, none added | ✅ Confirmed absent from frozen design |

---

## 7. Foreign Keys

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_scores_repository_id_repositories` | `repository_scores.repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed; functionally tested (invalid-FK rejection + CASCADE deletion) |

This is the only foreign key in this table.

---

## 8. Indexes

| Index | Type | Purpose | Documented target |
|---|---|---|---|
| `pk_repository_scores` | btree (PK) | Primary key lookup | — |
| `ix_repository_scores_repository_id_score_type_computed_at` | btree, `(repository_id, score_type, computed_at DESC)` | "Latest score of type X for repository Y" — per §14.11, "the single most-used access pattern outside of `repositories` itself" (Category B, C, F, J) | §14.11; §15 consolidated Cross-Cutting Index Summary |

**Empirical confirmation:** `EXPLAIN` on a `repository_id` + `score_type`-filtered, `computed_at DESC`-ordered `LIMIT 1` query (the exact "latest score" access pattern) showed the planner using `Index Scan using ix_repository_scores_repository_id_score_type_computed_at` directly, with both filter conditions applied via the index.

No partial indexes, no GIN/expression indexes are documented for this table in §14.11 (unlike `repository_metrics`'s GIN index on `extended_features`) — `score_breakdown` is not indexed, consistent with the frozen design.

---

## 9. Verification Performed

**Structural (Phase 4):** `\dt`, `\d repository_scores`, `\dT`, `alembic_version` — all 7 columns, types, defaults, nullability, PK, FK, single index, and `score_type` enum (3 values, correct order) matched `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.11 exactly. Confirmed absence of any unique or CHECK constraint, matching the frozen design.

**Functional (Phase 5):**

| # | Scenario | Result |
|---|---|---|
| 1 | Valid insert (all columns incl. JSONB `score_breakdown`) | ✅ Inserted and retrieved intact |
| 2 | `NULL repository_id` | ✅ Rejected via NOT NULL |
| 3 | `NULL score_type` | ✅ Rejected via NOT NULL |
| 4 | `NULL score_value` | ✅ Rejected via NOT NULL |
| 5 | `NULL algorithm_version` | ✅ Rejected via NOT NULL |
| 6 | Invalid enum value (`'popularity'`) | ✅ Rejected via enum domain |
| 7 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 8 | Same `(repository_id, score_type)`, different `algorithm_version`/`computed_at` (versioning) | ✅ Succeeded — no unique constraint blocks append-only history |
| 9 | Third row, same `(repository_id, score_type)`, later `computed_at` | ✅ Succeeded — confirms unlimited version accumulation |
| 10 | Different `score_type` for same repository | ✅ Succeeded — independent series per `score_type` |
| 11 | "Latest score of type X for repo Y" retrieval query | ✅ Returned the most recent row (highest `computed_at`) correctly |
| 12 | `EXPLAIN` on the latest-score query | ✅ Planner used `ix_repository_scores_repository_id_score_type_computed_at` directly |
| 13 | Full history preserved (append-only) — row count for `(repo, quality)` | ✅ 3 rows preserved, none overwritten |
| 14 | `ON DELETE CASCADE`: delete the repository, confirm all score rows removed | ✅ 4 rows before, 0 after |

All test data (`repository_scores`, `owners`, cascaded `repositories`) deleted after verification; confirmed empty via count query before proceeding to rollback testing.

---

## 10. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 007 -> 006, 007_intelligence_scores` |
| Table dropped | `\dt` | ✅ `repository_scores` removed; all 11 Migration 001-006 tables + `alembic_version` remained |
| Enum dropped | `\dT` | ✅ `score_type` removed; all 7 prior enum types remained |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `006` |
| Migration 006 objects intact | `\d repository_metrics` | ✅ FK, unique constraint, CHECK constraint, and both indexes still present, untouched |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 006 -> 007, 007_intelligence_scores` |
| Table recreated | `\dt` | ✅ All 12 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `007` |
| FK and index recreated | `\d repository_scores` | ✅ Identical to pre-rollback state |

No orphaned objects in either direction. `downgrade()` drops the single index, the table, then the `score_type` enum (correct order, per the established pattern in Migration 004's `package_ecosystem` teardown).

---

## 11. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly, no ambiguity encountered | ✅ |
| `alembic upgrade head` applies cleanly from `006` | ✅ |
| Table created with correct columns/types/defaults (7 columns) | ✅ |
| BIGINT identity PK per §13's PK strategy | ✅ |
| FK present, `ON DELETE CASCADE`, functionally tested | ✅ |
| No unique constraint (deliberate — append-only versioning), confirmed via successful duplicate `(repository_id, score_type)` inserts | ✅ |
| No CHECK constraint on `score_value` (deliberate — none documented) | ✅ |
| `score_type` enum: correct 3 values, invalid-value rejection tested | ✅ |
| `NOT NULL` tested on all 4 required columns | ✅ |
| JSONB `score_breakdown` tested for storage and retrieval | ✅ |
| Append-only/versioning behavior functionally proven (3 preserved rows, no overwrite) | ✅ |
| "Latest score" retrieval query functionally correct and `EXPLAIN`-confirmed index usage | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001-006 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `006` after rollback and `007` after re-upgrade | ✅ |

**Migration 007 is COMPLETE and VERIFIED.**

---

## 12. Observations (Frozen Design Not Modified)

1. **No design ambiguity encountered** — §14.11 is fully and unambiguously specified.
2. **Two deliberate absences confirmed, not oversights:** no unique constraint (append-only versioning requires it) and no CHECK constraint on `score_value` (none documented anywhere in the frozen design, unlike `repository_metrics`'s analogous CHECK). Both were explicitly verified absent from all five source documents before implementation, per the standing instruction not to introduce undocumented constraints.
3. **No environment issues** — Docker container was already running from the Migration 006 session.
4. **No design deviation.** Every column, constraint (and deliberate non-constraint), and index in this migration is a direct, literal implementation of `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.11.

---

## 13. Suggested Git Commit

```
Checkpoint 0.2: Migration 007 - Repository scores (intelligence output)

Creates repository_scores: versioned intelligence outputs (difficulty
score, quality score, community-health classification), per
MIGRATION_STRATEGY.md's 007_intelligence_scores plan.

Deliberately separate from repository_metrics (Migration 006):
repository_metrics holds inputs (computed features); repository_scores
holds outputs (scores derived from those features by scoring
algorithms). No database-level FK from repository_scores to
repository_metrics - scores reference repositories directly; the
relationship exists only at the application layer.

Append-only and versioned, like repository_metrics: scoring algorithms
evolve, so a single mutable column would silently rewrite history.
Every row is stamped with algorithm_version. A "current" score is
simply the most recent row for a given (repository_id, score_type).

Two deliberate omissions, each explicitly verified absent from the
frozen design before implementation:
- No UNIQUE constraint on (repository_id, score_type) - append-only
  versioning requires multiple rows over time, unlike
  repository_snapshot/repository_metrics's UNIQUE(repo_id, date).
- No CHECK constraint on score_value - nothing analogous to
  repository_metrics.top_contributor_commit_share's documented 0-1
  CHECK exists for this column anywhere in the frozen design.

Single index per QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.11:
INDEX(repository_id, score_type, computed_at DESC) - "latest score of
type X for repo Y," described as the single most-used access pattern
outside of repositories itself.

Only FK dependency is repositories.id (Migration 002). Only a
soft/application-level dependency on repository_metrics (006) - no
DB-level FK.

Verified: upgrade, NOT NULL on all 4 required columns, enum domain
rejection, FK (rejection + functional CASCADE deletion), append-only
versioning (3 preserved rows for same repo+type, no unique-constraint
blocking), "latest score" retrieval query correctness, EXPLAIN-
validated index usage, downgrade (clean drop incl. enum teardown,
Migrations 001-006 untouched), and re-upgrade.

No design deviation - direct implementation of
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.11.
```

---

**Status:** Migration 007 complete. Awaiting review before beginning Migration 008 (`008_embeddings`: `repository_embeddings`).
