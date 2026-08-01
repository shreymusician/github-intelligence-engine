# Migration 005 Report — Repository Snapshot History

**Checkpoint:** 0.2 (Implementation, Step 5 of 9)
**Migration ID:** `005` (`005_repository_snapshot_history.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`005_repository_snapshot_history`) and `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10, this migration creates `repository_snapshot` — cheap, high-frequency raw GitHub counters (stars, forks, watchers, open issues, size in KB) captured at each observation point, one row per repository per snapshot date.

**Why distinct from `repository_metrics` (Migration 006, not yet implemented):** per `DATABASE_DESIGN.md` §2.9, cost and cadence differ fundamentally. Star/fork counts come free with every API call and can be captured cheaply and frequently; computed metrics (documentation readability, dependency health) are expensive and computed on a slower cadence. Conflating them would force expensive recomputation just to log a star-count delta, or would starve star-velocity tracking of the granularity it needs. This is the source of truth for "star velocity" (stars/month) and other freshness-signal calculations.

**Why its own migration rather than merged with a prior one:** owned by a different module (Data Acquisition) than the association/dependency tables in 003-004 (Feature Engineering), and has its own distinct write cadence (weekly, per the corrected assumption below).

**Schema dependency:** only `repositories.id` (Migration 002). No relationship to Migrations 001, 003, or 004. Alembic chain position `004→005` is linear-ordering only, same clarification pattern as Migration 004's report.

---

## 2. Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
005_repository_snapshot_history
        │
        └── repository_snapshot
              └── FK → repositories.id  (CASCADE)

(No dependency on 001, 003, or 004. Coordinated - but not FK-linked - with
 006_feature_store for a future shared partitioning initiative on
 snapshot_date, per DATABASE_ARCHITECT_REVIEW.md Finding 9.)
```

---

## 3. Design Ambiguity Discovered and Resolved (Phase 1)

Before writing any code, I found a genuine ambiguity in `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10's Indexes section:

> `UNIQUE (repository_id, snapshot_date)`; `INDEX (repository_id, snapshot_date)` for range scans (Category E/H trend queries)

Both entries specify the **identical columns in the identical order**, with no `DESC` or additional included column to distinguish them — unlike the directly analogous `repository_metrics` section (§14.9), which explicitly differentiates its two index entries with `DESC`: `INDEX (repository_id, snapshot_date DESC)` for "latest metrics per repository."

I stopped and reported this rather than assuming an interpretation, per your instruction. **You confirmed:** implement only the `UNIQUE` constraint; its auto-generated backing index already serves the stated range-scan purpose, and a second physically-identical index would be pure redundant overhead. This is what was implemented, and **Phase 5's `EXPLAIN` test (§7 below) confirms empirically** that the query planner does select `uq_repository_snapshot_repository_id_snapshot_date` for exactly the range-scan query pattern the frozen design names — validating that no second index was needed.

---

## 4. Objects Created

### 4.1 `repository_snapshot`

| Column | Type | Constraint |
|---|---|---|
| `id` | bigint | PK, `GENERATED ALWAYS AS IDENTITY` |
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `snapshot_date` | date | NOT NULL |
| `stars_count` | integer | NOT NULL |
| `forks_count` | integer | NOT NULL |
| `watchers_count` | integer | NOT NULL — retained per `DATABASE_ARCHITECT_REVIEW.md` Finding 3; documented (not schema-enforced, since this is an interpretation note) as a near-duplicate of `stars_count` since GitHub merged "watching" into starring — not to be treated as an independent popularity signal in any future scoring formula |
| `open_issues_count` | integer | NOT NULL |
| `size_kb` | integer | nullable — this table is the **sole authoritative home** for this field (`DATABASE_ARCHITECT_REVIEW.md` Finding 2); it was removed from `repositories` in Migration 002 |

**PK strategy:** `BIGINT GENERATED ALWAYS AS IDENTITY` — per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13, this is one of the four tables using the BIGINT-identity strategy (high-volume, append-only, internal-only).

---

## 5. Constraints

| Constraint | Verification method | Result |
|---|---|---|
| `pk_repository_snapshot` (PRIMARY KEY on `id`) | `\d repository_snapshot` | ✅ Present |
| `uq_repository_snapshot_repository_id_snapshot_date` (UNIQUE) | Duplicate `(repository_id, snapshot_date)` insert | ✅ Rejected: `duplicate key value violates unique constraint "uq_repository_snapshot_repository_id_snapshot_date"` |
| `repository_id NOT NULL` | NULL insert | ✅ Rejected |
| `stars_count NOT NULL` | NULL insert | ✅ Rejected |
| (No CHECK constraints in the frozen design for this table — none added) | — | Confirmed absent, matching `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10 exactly |

---

## 6. Foreign Keys

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_snapshot_repository_id_repositories` | `repository_snapshot.repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed and functionally tested (§7) |

This is the only foreign key in this table.

---

## 7. Indexes

| Index | Type | Purpose |
|---|---|---|
| `pk_repository_snapshot` | btree (PK) | Primary key lookup |
| `uq_repository_snapshot_repository_id_snapshot_date` | btree (unique) | Enforces "one snapshot per repo per day"; **also** serves range-scan trend queries (Category E/H — star velocity, growth curves), confirmed via `EXPLAIN` |

No second, redundant index was created — see §3 above.

**Empirical confirmation the single index is sufficient:** `EXPLAIN` on a query filtering by `repository_id` and ordering by `snapshot_date` showed the planner using `Bitmap Index Scan on uq_repository_snapshot_repository_id_snapshot_date` directly for the `repository_id` filter, with only a small `Sort` step for the final ordering (expected at this trivial test-data scale; at production scale with an index-friendly query shape this remains the correct and only necessary index).

---

## 8. Verification Performed

**Structural (Phase 4):** `\dt`, `\d repository_snapshot`, `alembic_version` — all columns, types, defaults, PK, FK, and the single unique constraint matched `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10 exactly, with no extraneous index.

**Functional (Phase 5):**

| # | Scenario | Result |
|---|---|---|
| 1 | Insert 3 weekly snapshots for one repository (`2026-07-01`, `2026-07-08`, `2026-07-15`), simulating trend/history capture | ✅ All 3 inserted; third row omitted `size_kb` (nullable), correctly stored as `NULL` |
| 2 | Query snapshots ordered by date, joined through `repositories` | ✅ Returned all 3 rows in chronological order with correct `stars_count`/`size_kb` values |
| 3 | Duplicate `(repository_id, snapshot_date)` — same repo, same date as an existing row | ✅ Rejected via unique constraint |
| 4 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 5 | `NULL` `repository_id` | ✅ Rejected via NOT NULL |
| 6 | `NULL` `stars_count` | ✅ Rejected via NOT NULL |
| 7 | Same repository, **different** `snapshot_date` (`2026-07-22`) | ✅ Succeeded — confirms the unique constraint scopes correctly to the `(repository_id, snapshot_date)` pair, not to `repository_id` alone |
| 8 | `EXPLAIN` on a `repository_id`-filtered, `snapshot_date`-ordered query | ✅ Planner used the unique constraint's index directly for the filter — empirically validates the Phase 1 index-ambiguity resolution |
| 9 | `ON DELETE CASCADE`: delete the repository, confirm all 4 snapshot rows removed | ✅ 4 rows before, 0 after |

All test data (`repository_snapshot`, `owners`) deleted after verification; confirmed empty via count query before proceeding to rollback testing.

---

## 9. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 005 -> 004, 005_repository_snapshot_history` |
| Table dropped | `\dt` | ✅ `repository_snapshot` removed; all 9 Migration 001-004 tables + `alembic_version` remained |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `004` |
| Migration 004 objects intact | `\d repository_dependencies` | ✅ FK and all 3 indexes still present, untouched |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 004 -> 005, 005_repository_snapshot_history` |
| Table recreated | `\dt` | ✅ All 10 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `005` |
| FK and unique constraint recreated | `\d repository_snapshot` | ✅ Identical to pre-rollback state |

No orphaned objects in either direction. This table has no enum type to drop/recreate (unlike Migrations 001-004), so `downgrade()` is a single `op.drop_table()` call — correctly simple given the table's structure.

---

## 10. Future Migrations Enabled

**None, by foreign key.** Like Migration 004, `repository_snapshot` is a schema leaf — no subsequent table in `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.11-14.13 (`repository_scores`, `repository_embeddings`, `repository_similarity`) references it. `repository_metrics` (Migration 006) is **coordinated** with this table for a future shared partitioning initiative (both keyed on `snapshot_date`, per `DATABASE_ARCHITECT_REVIEW.md` Finding 9) but has no schema-level (FK) dependency on it — both independently depend only on `repositories.id`.

---

## 11. Performance Considerations

- **Corrected cadence assumption (Finding 5) is the main growth-rate lever for this table.** At the frozen design's weekly default (not the originally-speculated daily/every-API-touch cadence): 1,000 repos → ~52K rows/year. At 1M repos (the Architect Review's stated scale assumption): ~52M rows/year — reaching the review's 100M-snapshot-row assumption within roughly 2 years at weekly cadence, a 7x reduction in growth rate versus the daily assumption with no loss of query capability (no query in the 83-query workload needs sub-weekly resolution for star velocity or growth trends).
- **Declared (not implemented) partition key**, per `DATABASE_ARCHITECT_REVIEW.md` Finding 9: `snapshot_date` (RANGE, monthly/quarterly) — same key and rationale as `repository_metrics` (Migration 006), and the two tables should be partitioned together as one coordinated Version 2/3 initiative when either approaches 10-50M actual rows, not independently. This migration does **not** implement physical partitioning, consistent with the frozen design's explicit YAGNI stance — no `PARTITION BY` DDL was added.
- **BIGINT identity PK** preserves insert locality and avoids random-order index bloat that a UUID PK would cause at this table's projected row counts (millions to tens of millions), per §13's stated rationale.
- **Single index (not two) is the correct, minimal design** — confirmed both by removing the would-be-redundant second index (§3) and by empirically validating via `EXPLAIN` that the remaining unique-constraint index fully serves the stated query need. This avoids doubling the per-row write cost (every INSERT/UPDATE would otherwise maintain two structurally-identical btree indexes) for zero query benefit — directly relevant given this table's status as one of the highest-growth-rate tables in the schema.

---

## 12. Observations (Frozen Design Not Modified)

1. **Index-count ambiguity in §14.10, resolved per your explicit confirmation** (§3 above) — implemented as the single `UNIQUE` constraint only. This is the one point in this migration where the frozen text was genuinely ambiguous (not merely terse), and I stopped rather than guessing, consistent with your standing instruction.
2. **`watchers_count`'s "documented but not schema-enforced" status is a data-interpretation note, not a database constraint** — there is no way to encode "don't use this as an independent signal" as DDL; this is guidance for the Feature Engineering/Intelligence Generation modules that will eventually read this table, not something this migration can or should enforce structurally. Restating it here for continuity since Migration 002's report made the same observation about `repositories`-adjacent columns.
3. **No environment issues.** Docker's `postgres` container remains correctly bound to port 5432; no repeated intervention needed.
4. **No design deviation beyond the confirmed index resolution.** Every column, constraint, and the single index in this migration is a direct, literal implementation of `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10 as clarified by your Phase 1 confirmation.

---

## 13. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly, with the one confirmed index-ambiguity resolution (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.10) | ✅ |
| `alembic upgrade head` applies cleanly from `004` | ✅ |
| Table created with correct columns/types/defaults | ✅ |
| BIGINT identity PK per §13's PK strategy | ✅ |
| FK present, `ON DELETE CASCADE`, functionally tested | ✅ |
| Unique constraint present and correctly scoped to `(repository_id, snapshot_date)`, tested for both rejection (duplicate) and acceptance (different date) | ✅ |
| No redundant second index created; single index's sufficiency empirically confirmed via `EXPLAIN` | ✅ |
| Multiple weekly snapshots inserted and retrieved in chronological order, simulating real trend-query usage | ✅ |
| `NOT NULL` constraints tested on `repository_id` and `stars_count` | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001-004 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `004` after rollback and `005` after re-upgrade | ✅ |
| Genuine ambiguity found, reported, and resolved per your explicit decision rather than assumed | ✅ |

**Migration 005 is COMPLETE and VERIFIED.**

---

## 14. Suggested Git Commit

```
Checkpoint 0.2: Migration 005 - Repository snapshot history

Creates repository_snapshot: cheap, high-frequency raw GitHub counters
(stars, forks, watchers, open issues, size_kb), per
MIGRATION_STRATEGY.md's 005_repository_snapshot_history plan.

Deliberately separate from repository_metrics (Migration 006, not yet
implemented): star/fork counts are cheap and frequent; computed metrics
are expensive and infrequent. Conflating them would force expensive
recomputation just to log a star-count delta.

size_kb is this table's sole authoritative home (DATABASE_ARCHITECT_REVIEW.md
Finding 2), removed from repositories in Migration 002. Cadence corrected
to weekly, not daily (Finding 5) - 7x growth-rate reduction with no
query-capability loss.

Design ambiguity found and resolved per user confirmation:
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.10 listed a UNIQUE constraint and a
plain INDEX on identical columns/order with no distinguishing detail
(unlike repository_metrics' analogous DESC-differentiated section).
Implemented only the UNIQUE constraint - its auto-generated index
empirically confirmed (via EXPLAIN) to fully serve the stated range-scan
query need, avoiding a redundant duplicate index.

Only FK dependency is repositories.id (Migration 002) - no relationship
to Migrations 001, 003, or 004.

Verified: upgrade, unique constraint (duplicate rejection + different-date
acceptance), FK (rejection + functional CASCADE deletion), NOT NULL on
repository_id/stars_count, multi-week trend-query simulation, EXPLAIN-
validated index sufficiency, downgrade (clean drop, Migrations 001-004
untouched), and re-upgrade.

No design deviation beyond the confirmed index-ambiguity resolution.
```

---

**Status:** Migration 005 complete. Awaiting review before beginning Migration 006 (`006_feature_store`: `repository_metrics`).
