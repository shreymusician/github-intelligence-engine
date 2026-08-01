# Migration 004 Report — Repository Dependencies

**Checkpoint:** 0.2 (Implementation, Step 4 of 9)
**Migration ID:** `004` (`004_repository_dependencies.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`004_repository_dependencies`) and `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8, this migration creates `repository_dependencies` — fine-grained, package-level dependency data (exact package name, ecosystem, version constraints, staleness, vulnerability count).

**Why this is deliberately distinct from `repository_technologies` (Migration 003):** per `DATABASE_DESIGN.md` §2.7, Technology answers "what does this repo use, broadly" (curated, deduplicated, ~hundreds of distinct values total, suitable for filtering/faceting), while Dependency answers "what exact packages, at what versions, how risky" (raw, high-cardinality, package-registry-derived). A repository might have 200 npm dependencies but only 5-10 first-class Technologies worth surfacing in search filters — merging these would either pollute the curated taxonomy with noise or lose per-package granularity needed for vulnerability/staleness scoring.

**Why isolated in its own migration rather than grouped with `003`:** `DATABASE_ARCHITECT_REVIEW.md` Finding 9 flags `repository_dependencies` as the schema's **highest-volume table by repository-count growth alone** (average 30-100 dependencies per repository, vs. 5-15 for `repository_technologies`) and its first partitioning candidate. Isolating it means a future partitioning migration for this table doesn't need to also touch the taxonomy association tables from `003`.

**Schema dependency clarification:** this migration's only actual foreign-key dependency is `repositories.id` (Migration 002). It has no relationship whatsoever to `licenses`, `topics`, or `technologies` (001), or to `repository_topics`/`repository_technologies` (003). The Alembic revision chain remains linear (`003 → 004`) per `MIGRATION_STRATEGY.md`'s sequencing diagram, but that linear position is a review-ordering convention, not a schema dependency — I'm stating this explicitly per the instruction to flag any ambiguity rather than assume it away.

---

## 2. Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
004_repository_dependencies
        │
        └── repository_dependencies
              └── FK → repositories.id  (CASCADE)

(No dependency on 001_reference_tables or 003_repository_taxonomy_associations.
 Alembic chain position is 003 -> 004 for linear review ordering only.)
```

---

## 3. Objects Created

### 3.1 `repository_dependencies`

| Column | Type | Constraint |
|---|---|---|
| `id` | bigint | PK, `GENERATED ALWAYS AS IDENTITY` |
| `repository_id` | uuid | NOT NULL, FK → `repositories.id` (CASCADE) |
| `package_name` | text | NOT NULL |
| `ecosystem` | enum(`npm`, `pypi`, `cargo`, `go`, `maven`, `nuget`, `rubygems`) | NOT NULL |
| `version_constraint` | text | nullable |
| `resolved_version` | text | nullable |
| `is_direct` | boolean | NOT NULL |
| `is_dev` | boolean | NOT NULL, default `false` |
| `dependency_age_days` | integer | nullable |
| `is_deprecated` | boolean | NOT NULL, default `false` |
| `vulnerability_count` | integer | NOT NULL, default `0` |
| `last_checked_at` | timestamptz | NOT NULL, default `now()` |

**PK strategy:** `BIGINT GENERATED ALWAYS AS IDENTITY`, not UUID — per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §13, this table is explicitly named as one of the four tables using the BIGINT-identity strategy (high-volume, append-only, internal-only, never referenced from outside the system), preserving insert locality and compact index size at the row counts this table is expected to reach.

---

## 4. Constraints

| Constraint | Type | Verified |
|---|---|---|
| `pk_repository_dependencies` | PRIMARY KEY on `id` | ✅ Present, structural |
| `fk_repository_dependencies_repository_id_repositories` | FOREIGN KEY, `ON DELETE CASCADE` | ✅ Present, functionally tested |
| `repository_id NOT NULL` | NOT NULL | ✅ Functionally tested (rejected NULL insert) |
| `is_direct NOT NULL` | NOT NULL | ✅ Functionally tested (rejected NULL insert) |
| `ecosystem` enum (7 values) | ENUM type check | ✅ Functionally tested (rejected invalid value `'composer'`) |

**No uniqueness constraint exists on `(repository_id, package_name, ecosystem)`** — this is not an oversight on my part; it is exactly what the frozen schema specifies (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8 lists no `UNIQUE` constraint for this table, unlike `repository_technologies`' composite PK in Migration 003). I confirmed this functionally (§6, Test 5) rather than silently adding a constraint that isn't in the frozen design. See §9 Observations for discussion.

---

## 5. Foreign-Key Relationships

| Constraint | Column | References | On Delete | Rationale |
|---|---|---|---|---|
| `fk_repository_dependencies_repository_id_repositories` | `repository_dependencies.repository_id` | `repositories.id` | `CASCADE` | A dependency row has no independent existence outside its owning repository (`ENTITY_RELATIONSHIP_MODEL.md` §3.5) — a `left-pad` dependency row in repo A is a distinct fact from a `left-pad` row in repo B; there is no canonical `left-pad` entity in V1, unlike the deduplicated `technologies` table |

This is the **only** foreign key in this table — no relationship to any Migration-001 or Migration-003 table exists.

---

## 6. Indexes

| Index | Type | Query Supported |
|---|---|---|
| `ix_repository_dependencies_repository_id` | btree | Basic FK join support (Postgres does not auto-index FK columns) |
| `ix_repository_dependencies_package_name_ecosystem` | btree, composite `(package_name, ecosystem)` | Category D query 34: "find repos using package X at a specific version range" |
| `ix_repository_dependencies_vulnerable` | btree, **partial** `WHERE vulnerability_count > 0` | Category D query 33: "find repositories with known-vulnerable dependencies" — keeps this filter cheap without a full scan as the table grows into the highest-row-count table in the schema |

All 3 indexes confirmed present via `\d repository_dependencies`, matching `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8 exactly.

---

## 7. Functional Verification

Setup: 1 owner, 1 repository.

| # | Scenario | Result |
|---|---|---|
| 1 | Valid dependency insert, `pypi` ecosystem, full column set (`requests`, direct, not dev, age 120 days, 0 vulnerabilities) | ✅ Inserted |
| 2 | Valid dependency insert, `npm` ecosystem, transitive dev dependency with vulnerabilities (`left-pad`, indirect, dev, 3 vulnerabilities) | ✅ Inserted |
| 3 | Valid dependency insert, `cargo` ecosystem, minimal column set (only required fields — nullable columns omitted) | ✅ Inserted; nullable columns (`version_constraint`, `resolved_version`, `dependency_age_days`) correctly `NULL`, defaulted columns (`is_dev`, `is_deprecated`, `vulnerability_count`) correctly applied their defaults |
| 4 | Query confirms all 3 dependencies correctly attributed to the one repository across 3 different ecosystems | ✅ `requests`/pypi, `left-pad`/npm, `serde`/cargo all returned |
| 5 | Invalid `ecosystem` value (`'composer'`, not in the 7-value enum) | ✅ Rejected: `invalid input value for enum package_ecosystem: "composer"` |
| 6 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected: `violates foreign key constraint "fk_repository_dependencies_repository_id_repositories"` |
| 7 | `NULL` `repository_id` | ✅ Rejected: `null value in column "repository_id" ... violates not-null constraint` |
| 8 | `NULL` `is_direct` | ✅ Rejected: `null value in column "is_direct" ... violates not-null constraint` |
| 9 | Duplicate `(repository_id, package_name, ecosystem)` tuple (`requests`/pypi inserted a second time) | ✅ **Succeeded** — confirms the frozen design genuinely has no uniqueness constraint here, tested deliberately rather than assumed |
| 10 | Partial index correctness: count of `vulnerability_count > 0` rows (1) vs. total rows (4) | ✅ Matched expected: 1 vulnerable (`left-pad`), 4 total |
| 11 | `EXPLAIN` on `WHERE vulnerability_count > 0` query | Planner chose a sequential scan (table has only 4 rows) — expected and correct at this scale; the partial index exists and will be selected automatically once the table grows large enough for the planner to prefer it. Not a defect. |
| 12 | `ON DELETE CASCADE`: delete the repository, confirm all 4 dependency rows removed | ✅ 4 rows before, 0 rows after |

All test data (`repository_dependencies`, `owners`) deleted after verification; confirmed empty via count query (`repository_dependencies`: 0, `repositories`: 0, `owners`: 0) before proceeding to rollback testing.

---

## 8. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 004 -> 003, 004_repository_dependencies` |
| Table dropped | `\dt` | ✅ `repository_dependencies` removed; all 8 Migration 001-003 tables + `alembic_version` remained |
| Enum type dropped | `\dT` | ✅ `package_ecosystem` removed; `account_type`, `detected_via`, `last_extraction_status`, `technology_category`, `technology_role` (Migrations 001-003) and `gtrgm` (extension) all correctly untouched |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `003` |
| Migration 003 objects intact | `\d repository_technologies` | ✅ Both FKs and the `confidence` CHECK constraint still present, untouched by this rollback |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 003 -> 004, 004_repository_dependencies` |
| Table recreated | `\dt` | ✅ All 9 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `004` |
| FK and all 3 indexes recreated | `\d repository_dependencies` | ✅ Confirmed identical to pre-rollback state |

No orphaned objects in either direction. `downgrade()` drops the 3 non-PK indexes, drops the table (implicitly removing the PK and FK), then drops the `package_ecosystem` enum — correct reverse-dependency order.

---

## 9. Future Migrations Enabled

**None, by foreign key.** I checked `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.9-14.13 (`repository_metrics`, `repository_snapshot`, `repository_scores`, `repository_embeddings`, `repository_similarity`) — none reference `repository_dependencies` at the database level. `repository_metrics` (Migration 006) will carry *computed aggregate* columns (`direct_dependency_count`, `transitive_dependency_count`, `outdated_dependency_pct`) that are conceptually derived from this table's contents, but that is an application-layer computation performed by the Feature Engineering module reading this table and writing aggregates elsewhere — not a schema-level relationship. `repository_dependencies` is a schema leaf: Migration 004 does not unblock any subsequent migration the way Migrations 001→002→003 each unblocked the next.

---

## 10. Performance Considerations

- **This is the schema's highest-cardinality table by design**, per `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8: average 30-100 dependencies per repository (including transitive). At 1,000 repos: 30K-100K rows. At 100K repos: 3M-10M rows. This is 2-20x the row count of `repository_technologies` at equivalent repository counts.
- **Declared (not implemented) partition key**, per `DATABASE_ARCHITECT_REVIEW.md` Finding 9 / `QUERY_DRIVEN_SCHEMA_DESIGN.md` §16: `repository_id` (HASH) or `ecosystem` (LIST) — this table's growth is driven by repository count, not time, so it has no natural date column to partition by, unlike `repository_metrics`/`repository_snapshot`/`repository_scores` (RANGE on a timestamp). This migration does **not** implement physical partitioning — consistent with the frozen design's explicit YAGNI stance ("None of the four tables are within an order of magnitude of needing physical partitioning at V1's actual near-term scale"). No `PARTITION BY` DDL was added; this is documentation only, carried in the migration's docstring for future reference.
- **BIGINT identity PK** (not UUID) was chosen specifically, per §13, to preserve future partitioning/insert-locality options — a UUID PK on a table already flagged as the highest-volume in the schema would work against this table's own stated growth trajectory.
- **`ix_repository_dependencies_vulnerable` (partial index) is the second-most-important performance decision in this migration** (after the PK strategy itself): without it, "find vulnerable dependencies" queries would require scanning the entire highest-row-count table in the schema. The `EXPLAIN` test in this session correctly showed a sequential scan at 4 test rows — this is expected planner behavior at trivial scale and does not indicate the index is broken; it will be selected automatically once row counts justify it.
- **No uniqueness constraint on `(repository_id, package_name, ecosystem)`** means duplicate rows are structurally possible if the Feature Engineering pipeline (Milestone 2.5) doesn't itself deduplicate before insert. This is a pipeline-implementation responsibility, not a schema gap per the frozen design — see Observation 1 below.

---

## 11. Observations (Frozen Design Not Modified)

1. **No uniqueness constraint on `(repository_id, package_name, ecosystem)`.** I verified this functionally (§7, Test 9) rather than assuming it. The frozen `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8 table definition does not specify one, in contrast to `repository_technologies`' composite PK (Migration 003) which explicitly prevents a repository from having two rows for the same technology. This means the Milestone 2.5 dependency-analysis pipeline is implicitly responsible for its own deduplication (e.g., upsert-by-`(repository_id, package_name, ecosystem)` logic at the application layer) if duplicate rows are undesired — the schema itself permits them. I have **not** added a unique constraint, per the explicit instruction not to introduce constraints beyond the frozen design. Flagging this for awareness, not as a defect requiring a fix.
2. **`vulnerability_count` has no upper-bound or non-negative CHECK constraint**, unlike `repository_technologies.confidence` in Migration 003. The frozen design specifies `NOT NULL, default 0` only. Not adding one, per instruction.
3. **No environment issues.** Docker's `postgres` container remains correctly bound to port 5432; no repeated intervention needed.
4. **No design deviation.** Every column, constraint, and index in this migration is a direct, literal implementation of `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8. No ambiguity was discovered that required stopping — the one clarification worth surfacing (Alembic chain position vs. actual schema dependency, §1) is explicitly resolved within the frozen documents themselves, not an inconsistency between them.

---

## 12. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.8) | ✅ |
| `alembic upgrade head` applies cleanly from `003` | ✅ |
| Table created with correct columns/types/defaults | ✅ |
| BIGINT identity PK (not UUID) per §13's PK strategy | ✅ |
| FK present, `ON DELETE CASCADE`, functionally tested | ✅ |
| `ecosystem` enum (7 values) present, invalid-value rejection tested | ✅ |
| All 3 indexes present, including 1 composite and 1 partial | ✅ |
| Multiple dependencies across multiple ecosystems linked to one repository, verified via query | ✅ |
| Duplicate-tuple behavior tested and confirmed to match frozen design (no constraint added) | ✅ |
| `NOT NULL` constraints tested on both `repository_id` and `is_direct` | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001-003 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `003` after rollback and `004` after re-upgrade | ✅ |
| No design deviation; no ambiguity required stopping | ✅ |

**Migration 004 is COMPLETE and VERIFIED.**

---

## 13. Suggested Git Commit

```
Checkpoint 0.2: Migration 004 - Repository dependencies

Creates repository_dependencies: fine-grained, package-level dependency
data, per MIGRATION_STRATEGY.md's 004_repository_dependencies plan.

Deliberately distinct from repository_technologies (Migration 003):
Technology answers "what does this repo use, broadly" (curated,
~hundreds of values); Dependency answers "what exact packages, at what
versions, how risky" (raw, high-cardinality, package-registry-derived).

Uses BIGINT GENERATED ALWAYS AS IDENTITY (not UUID) per
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec13's PK strategy - this is the schema's
highest-volume table by repository-count growth alone (avg. 30-100
deps/repo) and DATABASE_ARCHITECT_REVIEW.md Finding 9's first
partitioning candidate. Declared (not implemented) future partition key:
repository_id (HASH) or ecosystem (LIST) - documented in migration
docstring only, no PARTITION BY DDL added.

Only FK dependency is repositories.id (Migration 002) - no relationship
to licenses/topics/technologies (001) or the association tables (003).

Indexes: repository_id (FK support), (package_name, ecosystem) composite
(Category D query 34), and a partial index on vulnerable dependencies
(Category D query 33).

Verified: upgrade, all constraints (FK CASCADE tested functionally,
NOT NULL on repository_id/is_direct, ecosystem enum rejection), multi-
ecosystem dependency linkage, confirmed absence of any uniqueness
constraint on (repository_id, package_name, ecosystem) matches the
frozen design exactly (tested, not assumed), downgrade (clean drop,
Migrations 001-003 untouched), and re-upgrade.

No design deviation from QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.8. No
ambiguity required stopping.
```

---

**Status:** Migration 004 complete. Awaiting review before beginning Migration 005 (`005_repository_snapshot_history`).
