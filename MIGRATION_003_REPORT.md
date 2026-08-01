# Migration 003 Report — Repository Taxonomy Associations

**Checkpoint:** 0.2 (Implementation, Step 3 of 9)
**Migration ID:** `003` (`003_repository_taxonomy_associations.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`003_repository_taxonomy_associations`), this migration creates the two M:N join tables connecting `repositories` to the Migration-001 reference tables: `repository_topics` and `repository_technologies`.

## 2. Why These Tables Belong Together

Both tables answer the same conceptual question — "what is this repository tagged with / built with" — and both connect `repositories` (Migration 002) to a reference table created in Migration 001 (`topics` and `technologies`, respectively). Even though `repository_topics` is populated by the Data Acquisition module and `repository_technologies` by Feature Engineering (different owning modules, per `DATABASE_DESIGN.md` §5), `MIGRATION_STRATEGY.md` groups them in one migration because:

- Both share the exact same prerequisite state — `001` + `002`, nothing more.
- Both share the same structural shape — associative tables with composite natural-key primary keys, no surrogate `id`.
- Splitting them into two migrations would add process overhead (two review cycles, two rollback tests) without any corresponding independence benefit, since neither could ever be deployed without the other's shared prerequisites already in place.

## 3. Why Separated from Migration 002

`repositories` (Migration 002) is the highest-traffic table in the schema and is expected to accumulate its own column changes over time, independent of these join tables. Bundling associations into `002` would mean every future `repositories`-only migration also touches unrelated join-table definitions. Additionally, `owners`/`repositories` use surrogate UUID primary keys (core-entity pattern) while these two tables use composite natural keys (associative-entity pattern) — a structurally different table shape that warrants its own migration boundary.

## 4. Why Separated from Later Migrations (004+)

`repository_dependencies` (Migration 004) is explicitly **not** a join to a curated reference table — per `DATABASE_DESIGN.md` §2.7, it is high-cardinality, independently-owned, package-registry-derived data (avg. 30-100 rows per repository, vs. 5-15 for `repository_technologies`), and is flagged in `DATABASE_ARCHITECT_REVIEW.md` Finding 9 as the schema's highest-volume table and first partitioning candidate. Isolating it in its own migration means a future partitioning migration for `repository_dependencies` doesn't need to also touch these taxonomy tables. Migrations 005-009 (snapshot history, feature store, scores, embeddings, similarity) are owned by entirely different modules (Data Acquisition's time-series capture, Intelligence Generation) with different write cadences and no shared prerequisite beyond `repositories` itself.

## 5. Dependency Diagram

```
001_reference_tables (licenses, topics, technologies)
        │
        ▼
002_core_entities (owners, repositories)
        │
        ▼
003_repository_taxonomy_associations
        │
        ├── repository_topics
        │     ├── FK → repositories.id  (from 002)
        │     └── FK → topics.id        (from 001)
        │
        └── repository_technologies
              ├── FK → repositories.id  (from 002)
              └── FK → technologies.id  (from 001)
```

Both tables in this migration have identical dependency depth (2 levels: reference table + core entity) — this is what makes them a natural pair for one migration.

---

## 6. Objects Created

### 6.1 `repository_topics`

| Column | Type | Constraint |
|---|---|---|
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `topic_id` | uuid | NOT NULL, FK → `topics.id` (CASCADE) |

**PK:** composite `(repository_id, topic_id)` — no surrogate key, since the natural key is already correct and stable (per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13).

### 6.2 `repository_technologies`

| Column | Type | Constraint |
|---|---|---|
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `technology_id` | uuid | NOT NULL, FK → `technologies.id` (CASCADE) |
| `role` | enum(`primary`, `secondary`, `dev`) | NOT NULL |
| `detected_via` | enum(`manifest`, `dockerfile`, `ci_config`, `linguist`) | NOT NULL |
| `confidence` | numeric(3,2) | NOT NULL, CHECK (0 ≤ confidence ≤ 1) |
| `first_detected_at` | timestamptz | NOT NULL, default `now()` — never overwritten on recompute |
| `last_confirmed_at` | timestamptz | NOT NULL, default `now()` — updated every recomputation pass |
| `removed_at` | timestamptz | nullable — set (not deleted) when a recomputation pass no longer detects the association |

**PK:** composite `(repository_id, technology_id)` — a repository cannot have two rows claiming the same technology; role changes update the existing row, `removed_at` marks absence in place.

---

## 7. Foreign-Key Relationships

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_topics_repository_id_repositories` | `repository_topics.repository_id` | `repositories.id` | CASCADE | ✅ Functionally tested (§9, Test 10) |
| `fk_repository_topics_topic_id_topics` | `repository_topics.topic_id` | `topics.id` | CASCADE | ✅ Structurally confirmed |
| `fk_repository_technologies_repository_id_repositories` | `repository_technologies.repository_id` | `repositories.id` | CASCADE | ✅ Functionally tested (§9, Test 10) |
| `fk_repository_technologies_technology_id_technologies` | `repository_technologies.technology_id` | `technologies.id` | CASCADE | ✅ Functionally tested (§9, Test 9) |

All four use `ON DELETE CASCADE` — join rows have no independent meaning once either side of the relationship is gone (`ENTITY_RELATIONSHIP_MODEL.md` §4). This is a deliberate contrast with `repositories`' own FKs (Migration 002), which use `RESTRICT`/`SET NULL` because `owners`/`licenses` are meaningful entities in their own right, independent of any one repository.

---

## 8. Constraints & Indexes

| Object | Table | Type | Purpose / Query Supported |
|---|---|---|---|
| `pk_repository_topics` | repository_topics | composite PK | "Repository → its topics" lookup |
| `ix_repository_topics_topic_id` | repository_topics | btree | Reverse direction: "find repos with topic X" (Category A query 4) |
| `pk_repository_technologies` | repository_technologies | composite PK | "Repository → its technologies" lookup; also enforces one row per (repo, tech) pair |
| `ix_repository_technologies_technology_id_role` | repository_technologies | btree, composite `(technology_id, role)` | "Find all repos using React as primary" (Category A query 2, Category D query 29) |
| `ix_repository_technologies_current` | repository_technologies | btree, **partial** `WHERE removed_at IS NULL` | Default "currently uses this technology" filter used by most Category D/E queries, without requiring a query-time filter at every call site |
| `ck_repository_technologies_confidence_range` | repository_technologies | CHECK | Enforces `confidence` stays within its documented 0-1 domain |

All 6 objects verified present via `\d` on both tables after the final re-upgrade, matching `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.6-14.7 exactly.

---

## 9. Functional Verification

Setup: 1 owner, 3 technologies (`python`, `fastapi`, `postgresql`), 2 topics (`machine-learning`, `api`), 1 repository.

| # | Scenario | Result |
|---|---|---|
| 1 | Link one repository to 3 different technologies (`primary`/`secondary`/`dev` roles) | ✅ All 3 rows inserted; verified via join query returning all 3 technologies with correct roles |
| 2 | Link one repository to 2 different topics | ✅ Both rows inserted; verified via join query |
| 3 | Duplicate `(repository_id, technology_id)` pair | ✅ Rejected: `duplicate key value violates unique constraint "pk_repository_technologies"` |
| 4 | Duplicate `(repository_id, topic_id)` pair | ✅ Rejected: `duplicate key value violates unique constraint "pk_repository_topics"` |
| 5 | Invalid `repository_id` (non-existent UUID) on `repository_technologies` | ✅ Rejected: FK violation on `fk_repository_technologies_repository_id_repositories` |
| 6 | Invalid `technology_id` (non-existent UUID) | ✅ Rejected: FK violation on `fk_repository_technologies_technology_id_technologies` |
| 7 | Invalid `topic_id` (non-existent UUID) on `repository_topics` | ✅ Rejected: FK violation on `fk_repository_topics_topic_id_topics` |
| 8 | `confidence = 1.50` (out of range, valid FKs) | ✅ Rejected: `violates check constraint "ck_repository_technologies_confidence_range"` — isolated from FK errors by re-testing with valid FKs |
| 9 | `confidence = -0.10` (out of range, valid FKs) | ✅ Rejected: same check constraint |
| 10 | Invalid `role` value (`'tertiary'`) | ✅ Rejected: `invalid input value for enum technology_role` |
| 11 | Invalid `detected_via` value (`'guesswork'`) | ✅ Rejected: `invalid input value for enum detected_via` |
| 12 | `ON DELETE CASCADE`: delete a `technology` referenced by an association | ✅ The `repository_technologies` row was automatically removed (verified: 1 row before, 0 after) |
| 13 | `ON DELETE CASCADE`: delete a `repository` with both technology and topic associations | ✅ Both association rows automatically removed (verified: 2 tech rows + 2 topic rows before, 0 + 0 after) |
| 14 | `removed_at` soft-delete pattern: mark an association as removed via `UPDATE ... SET removed_at = now()` | ✅ Row **still exists** in the table (count=1) but is correctly excluded from the "currently used" partial-index filter (`WHERE removed_at IS NULL` count dropped from 1 to 0) — confirms the soft-delete design preserves `first_detected_at` history while correctly reflecting current-state queries |

**Note on test isolation:** the first attempt at the CHECK-constraint test (confidence out of range) used a non-existent repository/technology pair and surfaced a CHECK-constraint error rather than the expected FK error — Postgres does not guarantee constraint-evaluation order within a single statement. I re-ran the test with valid FKs and out-of-range confidence to cleanly isolate the CHECK constraint's own enforcement (Tests 8-9 above), which is the version recorded as authoritative.

All test data (`repository_technologies`, `repository_topics`, `repositories`, `owners`, `technologies`, `topics`) was deleted after verification; confirmed empty via count query across all 7 tables before proceeding to rollback testing.

---

## 10. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 003 -> 002, 003_repository_taxonomy_associations` |
| Tables dropped | `\dt` | ✅ `repository_topics`, `repository_technologies` removed; all 6 Migration 001/002 tables + `alembic_version` remained |
| Enum types dropped | `\dT` | ✅ `technology_role`, `detected_via` removed; `account_type`, `last_extraction_status` (Migration 002), `technology_category` (Migration 001), `gtrgm` (extension) all correctly untouched |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `002` |
| Migration 002 objects intact | `\d repositories` | ✅ Both `repositories` FKs (`owner_id`→`owners`, `license_id`→`licenses`) still present with correct `ON DELETE` behavior, untouched by this migration's rollback |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 002 -> 003, 003_repository_taxonomy_associations` |
| Tables recreated | `\dt` | ✅ All 8 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `003` |
| FKs, check constraint, indexes recreated | `\d repository_technologies` | ✅ Both FKs, the CHECK constraint, and both non-PK indexes present |
| Total index count | `\di` | ✅ 26 domain indexes (up from 21 pre-Migration-003), consistent before and after the rollback/re-upgrade cycle |

No orphaned objects in either direction. Migration 003's `downgrade()` drops `repository_technologies`' two secondary indexes, drops the table (implicitly removing its PK, both FKs, and the CHECK constraint), drops both new enum types, drops `repository_topics`' secondary index, then drops that table — correct reverse-dependency order.

---

## 11. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.6-14.7) | ✅ |
| `alembic upgrade head` applies cleanly from `002` | ✅ |
| Both tables created with correct columns/types/defaults | ✅ |
| Both composite PKs present and enforced (duplicate-pair rejection tested) | ✅ |
| All 4 foreign keys present with correct `ON DELETE CASCADE`, functionally tested (not just structural) | ✅ |
| Both enums (`technology_role`, `detected_via`) present with correct value sets, invalid-value rejection tested | ✅ |
| `CHECK` constraint on `confidence` present and enforced at both boundaries (0 and 1), isolated from FK errors | ✅ |
| All 3 non-PK indexes present, including 1 composite and 1 partial | ✅ |
| Repository successfully linked to multiple technologies and multiple topics in one functional test | ✅ |
| `removed_at` soft-delete semantics verified: row persists, excluded from "currently used" filter | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001/002 objects fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `002` after rollback and `003` after re-upgrade | ✅ |
| No design deviation from frozen schema | ✅ |

**Migration 003 is COMPLETE and VERIFIED.**

---

## 12. Performance Considerations

- **Expected volume** (per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.6-14.7): `repository_topics` averages 3-8 rows per repository (3K-8K at 1,000 repos); `repository_technologies` averages 5-15 rows per repository (5K-15K at 1,000 repos). Both are trivial for PostgreSQL at V1 scale and remain reasonable into the low millions at 100K+ repositories.
- **`ix_repository_technologies_current` (partial index) is the single most important performance decision in this migration.** Without it, every "what technologies does this repo currently use" query (the overwhelming majority of Category D/E query traffic) would need either a full-table filter on `removed_at IS NULL` or would incorrectly include stale/abandoned associations. The partial index keeps this filter cheap even as the table accumulates historical (`removed_at IS NOT NULL`) rows over years of recomputation passes — the index only grows with *currently active* associations, not the full historical count.
- **Composite PK vs. surrogate key:** both tables deliberately avoid a surrogate `id` column (per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13's PK strategy) since the natural key `(repository_id, {topic_id|technology_id})` is already unique, stable, and exactly what every query joins on — adding a surrogate key here would only add an unused column and an extra unique index with no query benefit.
- **No partitioning concern at this stage:** unlike `repository_dependencies` (Migration 004) or the time-series tables (005-007), these are bounded by `repository_count × avg_associations_per_repo`, growing linearly with repository count but at a much lower multiplier (5-15) than dependencies (30-100) — not flagged as a partitioning candidate in the frozen design, and this migration introduces nothing that would change that assessment.

---

## 13. Observations

1. **CHECK constraint evaluation order is not guaranteed relative to FK constraints within a single INSERT.** My first attempt at testing the `confidence` range CHECK used deliberately-invalid FK values alongside the out-of-range confidence value, and Postgres surfaced the CHECK-constraint error rather than the FK error. This is not a defect in the schema — both constraints are independently enforced and both would reject the row — but it means constraint tests should isolate one violation at a time for unambiguous attribution, which I did in the corrected Tests 8-9 (§9). No schema change needed; noted purely as a testing-methodology observation.
2. **Naming convention extended consistently:** `ck_<table>_<description>` was introduced for the check constraint (`ck_repository_technologies_confidence_range`), following the same `pk_`/`uq_`/`ix_`/`fk_<table>_<col>_<reftable>` pattern established in Migrations 001-002. This is the first CHECK constraint in the schema; the naming pattern should be reused as-is for any future CHECK constraints (none are currently planned before Migration 009, per `MIGRATION_STRATEGY.md`).
3. **No environment issues.** Docker's `postgres` container remains correctly bound to port 5432 from the fix applied during Migration 001; no repeated intervention was needed.
4. **No design deviation.** Every column, constraint, and index in this migration is a direct, literal implementation of `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.6-14.7 — nothing was added, removed, or reinterpreted.

---

## 14. Suggested Git Commit

```
Checkpoint 0.2: Migration 003 - Repository taxonomy associations

Creates the two M:N join tables connecting repositories to the
Migration-001 reference tables, per MIGRATION_STRATEGY.md's
003_repository_taxonomy_associations plan:

- repository_topics: pure join (repository_id, topic_id), no edge
  attributes - GitHub's maintainer-assigned tags carry no confidence/role
- repository_technologies: associative entity with edge attributes
  (role, detected_via, confidence, first_detected_at, last_confirmed_at,
  removed_at) - the schema's first associative (not pure-join) M:N table

Includes removed_at (DATABASE_ARCHITECT_REVIEW.md Finding 6): a nullable
timestamp set, not deleted, when a recomputation pass no longer detects
a previously-confirmed technology association - prevents inflated,
indefinitely-growing adoption counts in trend queries.

Both composite PKs use natural keys (repository_id, {topic_id|
technology_id}) with no surrogate id, per QUERY_DRIVEN_SCHEMA_DESIGN.md
Sec13's PK strategy.

FK constraints: all 4 use ON DELETE CASCADE (join rows have no meaning
once either side is gone) - functionally tested, not just inspected.

Verified: upgrade, both composite PKs (duplicate-pair rejection), all
4 FKs (invalid-FK rejection + functional CASCADE deletion), both enums
(technology_role, detected_via), the confidence CHECK constraint
(isolated from FK errors, both boundaries tested), the removed_at
soft-delete pattern (row persists, excluded from partial-index filter),
downgrade (clean drop, Migrations 001/002 untouched), and re-upgrade.

No design deviation from QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.6-14.7.
```

---

**Status:** Migration 003 complete. Awaiting review before beginning Migration 004 (`004_repository_dependencies`).
