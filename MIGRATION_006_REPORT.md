# Migration 006 Report — Feature Store

**Checkpoint:** 0.2 (Implementation, Step 6 of 9)
**Migration ID:** `006` (`006_feature_store.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`006_feature_store`) and `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.9, this migration creates `repository_metrics` — the platform's **feature store**: computed, engineered features (activity, documentation quality, code size, dependency health rollups) described exhaustively in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 and `REPOSITORY_INTELLIGENCE_ENGINE.md` §2. This is the single largest table definition in the schema (27 typed columns + 2 JSONB columns) and the one most likely to accumulate new columns as Milestone 2's checkpoints (2.1 Activity, 2.2 Technology, 2.3 Documentation, 2.4 Code Complexity, 2.5 Dependency) each contribute their own feature groups over time.

**Why distinct from `repository_snapshot` (Migration 005):** per `DATABASE_DESIGN.md` §2.8 vs §2.9, `repository_snapshot` holds cheap, raw GitHub counters fetched at near-zero cost with every API call; `repository_metrics` holds expensive, computed features requiring actual analysis work. Conflating them would force expensive recomputation just to log a star-count delta — the same rationale documented in Migration 005's report, from the other side.

**Why its own migration:** isolating it means future column-addition migrations (e.g., a later `add_security_features` migration) have a clean, singular target, rather than bundling unrelated schema changes into whichever migration happened to touch this table last.

**Schema dependency:** only `repositories.id` (Migration 002). No relationship to Migrations 001, 003, 004, or 005. Alembic chain position `005→006` is linear-ordering only, same clarification pattern as prior migration reports.

---

## 2. Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
006_feature_store
        │
        └── repository_metrics
              └── FK → repositories.id  (CASCADE)

(No dependency on 001, 003, 004, or 005. Coordinated - but not FK-linked -
 with 005_repository_snapshot_history for a future shared partitioning
 initiative on snapshot_date, per DATABASE_ARCHITECT_REVIEW.md Finding 9.
 007_intelligence_scores has only a soft/application-level dependency on
 this table - no DB-level FK, per MIGRATION_STRATEGY.md §2.)
```

---

## 3. Design Verification (Phase 1) — No Ambiguity Found

Unlike Migration 005, where `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10 listed a `UNIQUE` constraint and a plain `INDEX` on identical columns with no distinguishing detail, §14.9's index section for this table is **unambiguous**: the two btree entries are explicitly differentiated by `DESC`:

> `UNIQUE (repository_id, snapshot_date)`; `INDEX (repository_id, snapshot_date DESC)` — "latest metrics per repository" access path; `GIN INDEX (extended_features)`.

This is also cross-confirmed by §14's consolidated Access Pattern table: `"Latest metrics for repo" → repository_metrics (repository_id, snapshot_date DESC)`. All 28 columns, the CHECK constraint, and JSONB usage were verified against §14.9 with no gaps or conflicts across `MIGRATION_STRATEGY.md`, `QUERY_DRIVEN_SCHEMA_DESIGN.md`, `DATABASE_DESIGN.md` §2.8, `DATABASE_ARCHITECT_REVIEW.md`, and `DATABASE_DESIGN_CHANGELOG.md`. No stop-and-report was required for this migration.

---

## 4. Objects Created

### 4.1 `repository_metrics`

| Column | Type | Constraint |
|---|---|---|
| `id` | bigint | PK, `GENERATED ALWAYS AS IDENTITY` |
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `snapshot_date` | date | NOT NULL |
| `commits_last_3mo` | integer | |
| `commits_last_6mo` | integer | |
| `commits_last_12mo` | integer | |
| `unique_contributors_last_12mo` | integer | |
| `top_contributor_commit_share` | numeric(4,3) | CHECK (0 ≤ value ≤ 1) — added per Phase 2 Category F finding |
| `days_since_last_commit` | integer | |
| `commit_frequency_stddev` | numeric | |
| `issues_open_count` | integer | |
| `issues_closed_count` | integer | |
| `issue_close_rate` | numeric(4,3) | |
| `avg_issue_close_days` | numeric | |
| `prs_merged_count` | integer | |
| `pr_merge_rate` | numeric(4,3) | |
| `avg_pr_merge_hours` | numeric | |
| `readme_present` | boolean | |
| `readme_word_count` | integer | |
| `readme_section_count` | integer | |
| `readme_code_example_count` | integer | |
| `readme_readability_score` | numeric | |
| `loc_total` | integer | |
| `test_file_ratio` | numeric(4,3) | |
| `comment_to_code_ratio` | numeric(4,3) | |
| `direct_dependency_count` | integer | |
| `transitive_dependency_count` | integer | |
| `outdated_dependency_pct` | numeric(4,3) | |
| `loc_by_language` | jsonb | genuinely variable-shaped (unbounded language keys), justified JSONB use |
| `extended_features` | jsonb | overflow bucket for long-tail/experimental features not yet promoted to columns |
| `computed_at` | timestamptz | NOT NULL, default `now()` |

**PK strategy:** `BIGINT GENERATED ALWAYS AS IDENTITY` — per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13, one of the four high-volume, append-only, internal-only tables using this strategy (alongside `repository_dependencies`, `repository_snapshot`, `repository_scores`).

**JSONB restraint confirmed:** only 2 of 30 columns are JSONB, each individually justified (§14.9) — every feature named explicitly in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 as a "Stage 1: Essential Feature" gets its own typed column instead, since these are queried/filtered/indexed constantly (Category B-F, ~40 of 83 queries touch this table).

---

## 5. Constraints

| Constraint | Verification method | Result |
|---|---|---|
| `pk_repository_metrics` (PRIMARY KEY on `id`) | `\d repository_metrics` | ✅ Present |
| `uq_repository_metrics_repository_id_snapshot_date` (UNIQUE) | Duplicate `(repository_id, snapshot_date)` insert | ✅ Rejected: `duplicate key value violates unique constraint` |
| `ck_repository_metrics_top_contributor_commit_share_range` (CHECK 0-1) | Insert `1.5`, insert `-0.1`, insert boundary `0.000` and `1.000` | ✅ Out-of-range rejected; boundary values accepted |
| `repository_id NOT NULL` | NULL insert | ✅ Rejected |
| `snapshot_date NOT NULL` | NULL insert | ✅ Rejected |
| All other columns nullable per frozen design | Insert row supplying only `repository_id`/`snapshot_date` | ✅ Succeeded, all other columns stored `NULL` |

---

## 6. Foreign Keys

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_metrics_repository_id_repositories` | `repository_metrics.repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed; functionally tested (invalid-FK rejection + CASCADE deletion) |

This is the only foreign key in this table.

---

## 7. JSONB Usage

| Column | Purpose | Verification |
|---|---|---|
| `loc_by_language` | Per-language LOC breakdown, unbounded key set | ✅ Inserted `{"Python": 12000, "JavaScript": 3000}`, retrieved intact |
| `extended_features` | Overflow bucket for unpromoted experimental features | ✅ Inserted `{"experimental_flag": true}`, retrieved intact; containment query (`@>`) executed successfully |

---

## 8. Indexes

| Index | Type | Purpose | Documented target |
|---|---|---|---|
| `pk_repository_metrics` | btree (PK) | Primary key lookup | — |
| `uq_repository_metrics_repository_id_snapshot_date` | btree (unique) | One metrics snapshot per repo per day | §14.9 |
| `ix_repository_metrics_repository_id_snapshot_date` | btree, `(repository_id, snapshot_date DESC)` | "Latest metrics per repository" — used by nearly every intelligence and search query (Category B, C, F, J) | §14.9; §14 consolidated Access Pattern table |
| `ix_repository_metrics_extended_features` | GIN | Ad-hoc querying into the JSONB overflow bucket without requiring a migration per new experimental feature | §14.9 |

**Empirical confirmation:** `EXPLAIN` on a `repository_id`-filtered, `snapshot_date DESC`-ordered `LIMIT 1` query (the exact "latest metrics" access pattern) showed the planner using `Index Scan using ix_repository_metrics_repository_id_snapshot_date` directly. The GIN-index `EXPLAIN` test used a `Seq Scan` instead at this trivial test-data scale (5 rows) — same expected behavior documented in `MIGRATION_005_REPORT.md` §7; the index is structurally present and will be selected by the planner once the table holds a realistic row count.

---

## 9. Verification Performed

**Structural (Phase 4):** `\dt`, `\d repository_metrics`, `alembic_version` — all 30 columns, types, defaults, nullability, PK, FK, unique constraint, CHECK constraint, and all 3 indexes (including the GIN index) matched `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.9 exactly.

**Functional (Phase 5):**

| # | Scenario | Result |
|---|---|---|
| 1 | Valid insert with typed columns + both JSONB columns | ✅ Inserted and retrieved intact |
| 2 | `NULL repository_id` | ✅ Rejected via NOT NULL |
| 3 | `NULL snapshot_date` | ✅ Rejected via NOT NULL |
| 4 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 5 | Duplicate `(repository_id, snapshot_date)` | ✅ Rejected via unique constraint |
| 6 | Same repository, different `snapshot_date` | ✅ Succeeded — confirms unique constraint scopes to the pair, not `repository_id` alone |
| 7 | CHECK violation: `top_contributor_commit_share = 1.5` | ✅ Rejected |
| 8 | CHECK violation: `top_contributor_commit_share = -0.1` | ✅ Rejected |
| 9 | CHECK boundary values `0.000` and `1.000` | ✅ Both accepted |
| 10 | `computed_at` default applied on every row | ✅ Confirmed non-null on all inserted rows |
| 11 | `EXPLAIN` on "latest metrics per repo" query | ✅ Planner used `ix_repository_metrics_repository_id_snapshot_date` directly |
| 12 | `EXPLAIN` on JSONB containment query (`extended_features @>`) | ✅ GIN index present; planner chose Seq Scan at trivial test-data scale (expected, see §8) |
| 13 | Insert supplying only required columns | ✅ All other columns correctly stored `NULL` |
| 14 | `ON DELETE CASCADE`: delete the repository, confirm all metrics rows removed | ✅ 5 rows before, 0 after |

All test data (`repository_metrics`, `owners`, cascaded `repositories`) deleted after verification; confirmed empty via count query before proceeding to rollback testing.

---

## 10. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 006 -> 005, 006_feature_store` |
| Table dropped | `\dt` | ✅ `repository_metrics` removed; all 10 Migration 001-005 tables + `alembic_version` remained |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `005` |
| Migration 004 objects intact | `\d repository_dependencies` | ✅ FK and all 3 indexes still present, untouched |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 005 -> 006, 006_feature_store` |
| Table recreated | `\dt` | ✅ All 11 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `006` |
| FK, unique, CHECK, and both indexes recreated | `\d repository_metrics` | ✅ Identical to pre-rollback state |

No orphaned objects in either direction. This table has no enum type to drop/recreate, so `downgrade()` is `drop_index` × 2 + `op.drop_table()`.

---

## 11. Future Migrations Enabled

**None, by foreign key.** `repository_scores` (Migration 007) references `repositories` directly — per `MIGRATION_STRATEGY.md` §2, "no database-level foreign key from `repository_scores` to `repository_metrics`," only a soft/application-level dependency (scoring algorithms *use* metrics as input at the application layer). `repository_metrics` is coordinated with `repository_snapshot` (005) for a future shared partitioning initiative on `snapshot_date`, but neither has a schema-level dependency on the other.

---

## 12. Performance Considerations

- **Expected volume:** one row per repository per week (batch update cadence, `SYSTEM_ARCHITECTURE.md` §3 Stage 4). At 1,000 repos: ~52K rows/year. At the review's assumed scale (1M repos): ~52M rows/year — well past the point where partitioning is non-optional (§14.9).
- **Declared (not implemented) partition key**, per `DATABASE_ARCHITECT_REVIEW.md` Finding 9: `snapshot_date` (RANGE, monthly/quarterly) — same key and rationale as `repository_snapshot` (005); the two should be partitioned together as one coordinated Version 2/3 initiative when either approaches 10-50M actual rows. No `PARTITION BY` DDL added, consistent with the frozen design's explicit YAGNI stance.
- **BIGINT identity PK** preserves insert locality and avoids random-order index bloat versus a UUID PK, at this table's projected row counts (tens of millions), per §13's stated rationale.
- **Three indexes, each independently justified** — unlike Migration 005 where a would-be-redundant second index was avoided, here all three indexes (unique, DESC range, GIN) serve genuinely distinct query needs per §14.9 and were each empirically or structurally confirmed.

---

## 13. Observations (Frozen Design Not Modified)

1. **No design ambiguity encountered** — §14.9's index section explicitly differentiates its two btree entries via `DESC` (unlike §14.10's ambiguity in Migration 005), so no stop-and-report was required.
2. **CHECK constraint pattern reused from Migration 003** — `top_contributor_commit_share`'s `CHECK (0 ≤ value ≤ 1)` follows the same `ck_<table>_<description>` naming and range-check style as `repository_technologies.confidence` (Migration 003), maintaining convention consistency across migrations.
3. **GIN index EXPLAIN result is expected, not a defect** — at trivial test-data scale, the planner correctly prefers Seq Scan over any index; this mirrors the precedent set in `MIGRATION_005_REPORT.md` §7 and is not evidence of a problem with the index itself.
4. **No environment issues** beyond Docker Desktop requiring a manual start before this session's Phase 3 execution; the `postgres` container was otherwise correctly configured and healthy once started.
5. **No design deviation.** Every column, constraint, and index in this migration is a direct, literal implementation of `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.9.

---

## 14. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly, no ambiguity encountered | ✅ |
| `alembic upgrade head` applies cleanly from `005` | ✅ |
| Table created with correct columns/types/defaults (30 columns) | ✅ |
| BIGINT identity PK per §13's PK strategy | ✅ |
| FK present, `ON DELETE CASCADE`, functionally tested | ✅ |
| Unique constraint present and correctly scoped to `(repository_id, snapshot_date)`, tested for both rejection and acceptance | ✅ |
| CHECK constraint on `top_contributor_commit_share` tested for rejection (out-of-range) and acceptance (boundary values) | ✅ |
| Both JSONB columns (`loc_by_language`, `extended_features`) tested for storage and retrieval | ✅ |
| GIN index on `extended_features` structurally present; containment query executes correctly | ✅ |
| DESC range index empirically confirmed via `EXPLAIN` for the "latest metrics per repo" access pattern | ✅ |
| `NOT NULL` constraints tested on `repository_id` and `snapshot_date`; all other columns confirmed nullable | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001-005 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `005` after rollback and `006` after re-upgrade | ✅ |

**Migration 006 is COMPLETE and VERIFIED.**

---

## 15. Suggested Git Commit

```
Checkpoint 0.2: Migration 006 - Repository metrics feature store

Creates repository_metrics: the platform's feature store for computed,
engineered features (activity, documentation quality, code size,
dependency health), per MIGRATION_STRATEGY.md's 006_feature_store plan.

Deliberately separate from repository_snapshot (Migration 005):
repository_snapshot holds cheap, raw GitHub counters; repository_metrics
holds expensive, computed features. Conflating them would force
expensive recomputation just to log a star-count delta.

Largest single table in the schema (30 columns, 2 JSONB) - isolated as
its own migration so future column-addition migrations (e.g. a later
add_security_features migration) have a clean, singular target.

top_contributor_commit_share has a CHECK (0-1 range) per Phase 2
Category F finding, following the same ck_ naming pattern established
for repository_technologies.confidence in Migration 003.

Three indexes, each independently justified per QUERY_DRIVEN_SCHEMA_
DESIGN.md Sec14.9: UNIQUE(repository_id, snapshot_date) prevents
duplicate computation runs; INDEX(repository_id, snapshot_date DESC)
serves the "latest metrics per repo" access pattern used by nearly
every intelligence/search query; GIN INDEX(extended_features) supports
ad-hoc querying into the JSONB overflow bucket. No index ambiguity
encountered here (unlike Migration 005's Sec14.10 question) - Sec14.9
explicitly differentiates its two btree entries via DESC.

Only FK dependency is repositories.id (Migration 002). repository_scores
(007) will have only a soft/application-level dependency on this table,
no DB-level FK.

Verified: upgrade, unique constraint (duplicate rejection + different-
date acceptance), CHECK constraint (out-of-range rejection + boundary
acceptance), FK (rejection + functional CASCADE deletion), NOT NULL on
repository_id/snapshot_date, JSONB storage/retrieval for both columns,
EXPLAIN-validated DESC index usage for latest-metrics query, downgrade
(clean drop, Migrations 001-005 untouched), and re-upgrade.

No design deviation - direct implementation of
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.9.
```

---

**Status:** Migration 006 complete. Awaiting review before beginning Migration 007 (`007_intelligence_scores`: `repository_scores`).
