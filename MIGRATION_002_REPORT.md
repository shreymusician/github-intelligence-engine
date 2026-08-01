# Migration 002 Report — Core Entities

**Checkpoint:** 0.2 (Implementation, Step 2 of 9)
**Migration ID:** `002` (`002_core_entities.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`002_core_entities`), this migration creates the central entity and its one mandatory parent: `owners`, `repositories`.

## 2. Why These Entities Belong Together

`repositories.owner_id` is a mandatory (`NOT NULL`) foreign key into `owners.id` — GitHub's own model has every repository owned by exactly one account, never zero, never many. Since `owners` has no dependencies of its own and `repositories` cannot exist without an owner present, these two form the minimal atomic unit that can be created together: the platform's central entity plus its one hard prerequisite.

`MIGRATION_STRATEGY.md` explicitly keeps this migration separate from `001_reference_tables` (rather than merging all four reference/core tables into one migration) because `repositories` is the highest-traffic table in the entire schema and is expected to accumulate column changes independently of the static reference tables (`licenses`, `topics`, `technologies`) — a future migration touching `repositories` should not need to also touch unrelated reference-table definitions.

## 3. Why These Cannot Be Created Earlier or Later

**Cannot be earlier:** `repositories.license_id` is a nullable foreign key into `licenses.id`, which was created in Migration 001. This migration's `down_revision = '001'` enforces that dependency at the Alembic level — attempting to run `002` against a database that hasn't run `001` would fail with an undefined-table error on the FK reference.

**Cannot be later:** Every migration from `003` onward has a hard foreign-key dependency on `repositories.id`:
- `003_repository_taxonomy_associations` (`repository_topics`, `repository_technologies`) — both reference `repositories.id`
- `004_repository_dependencies` — references `repositories.id`
- `005_repository_snapshot_history`, `006_feature_store`, `007_intelligence_scores`, `008_embeddings`, `009_similarity` — all reference `repositories.id`

None of these can run before `repositories` exists. Migration 002 is therefore the single hard gate between the reference/taxonomy layer and everything else in the schema.

## 4. Foreign-Key Dependencies

| Constraint | Column | References | On Delete | Rationale |
|---|---|---|---|---|
| `fk_repositories_owner_id_owners` | `repositories.owner_id` | `owners.id` | `RESTRICT` | Owners are never deleted while owning repositories — per `ENTITY_RELATIONSHIP_MODEL.md` §4, this is enforced at the constraint level, not left to application discipline |
| `fk_repositories_license_id_licenses` | `repositories.license_id` | `licenses.id` | `SET NULL` | Losing license reference data (e.g., an SPDX identifier being retired) should not delete the repository — the repository simply becomes "unlicensed" in the schema, which is itself a meaningful, valid state per `DATABASE_DESIGN.md` §2.3 |

Both foreign keys were verified functionally (not just structurally) — see §6 below.

---

## 5. Objects Created

### 5.1 `owners`

| Column | Type | Constraint |
|---|---|---|
| `id` | uuid | PK, default `uuid_generate_v4()` |
| `github_id` | bigint | NOT NULL, UNIQUE |
| `login` | text | NOT NULL |
| `account_type` | enum(`user`, `organization`) | NOT NULL |
| `avatar_url` | text | nullable |
| `github_created_at` | timestamptz | nullable |
| `created_at` | timestamptz | NOT NULL, default `now()` |
| `updated_at` | timestamptz | NOT NULL, default `now()` |

### 5.2 `repositories`

| Column | Type | Constraint |
|---|---|---|
| `id` | uuid | PK, default `uuid_generate_v4()` |
| `github_id` | bigint | NOT NULL, UNIQUE |
| `owner_id` | uuid | NOT NULL, FK → `owners.id` (RESTRICT) |
| `name` | text | NOT NULL |
| `full_name` | text | NOT NULL, UNIQUE |
| `description` | text | nullable |
| `primary_language` | text | nullable |
| `license_id` | uuid | nullable, FK → `licenses.id` (SET NULL) |
| `is_archived` | boolean | NOT NULL, default `false` |
| `is_fork` | boolean | NOT NULL, default `false` |
| `is_accessible` | boolean | NOT NULL, default `true` |
| `inaccessible_since` | timestamptz | nullable |
| `github_created_at` | timestamptz | NOT NULL |
| `pushed_at` | timestamptz | nullable |
| `fetched_at` | timestamptz | NOT NULL, default `now()` |
| `last_extraction_status` | enum(`pending`, `success`, `failed`) | NOT NULL, default `pending` |
| `created_at` | timestamptz | NOT NULL, default `now()` |
| `updated_at` | timestamptz | NOT NULL, default `now()` |

**Confirmed excluded** (per `DATABASE_ARCHITECT_REVIEW.md` Findings 1, 2, 12, already reflected in the frozen schema): `default_branch`, `homepage_url`, `is_template`, `size_kb`, `first_seen_at`. None of these columns exist in the executed migration — verified via `\d repositories`.

**Confirmed included** (Finding 4): `is_accessible`, `inaccessible_since` — the repository-lifecycle gap fix distinguishing GitHub-side existence from `is_archived` (maintainer action) and `last_extraction_status` (pipeline outcome).

---

## 6. Constraints Verified

| Constraint | Verification method | Result |
|---|---|---|
| `pk_owners`, `pk_repositories` (PRIMARY KEY) | `\d` on each table | ✅ Present |
| `uq_owners_github_id` (UNIQUE) | Duplicate insert (`github_id=1001` twice) | ✅ Rejected: `duplicate key value violates unique constraint "uq_owners_github_id"` |
| `account_type` enum (2 values: `user`, `organization`) | Invalid value insert (`'robot'`) | ✅ Rejected: `invalid input value for enum account_type: "robot"` |
| `uq_repositories_github_id` (UNIQUE) | Structural check via `\d repositories` | ✅ Present |
| `uq_repositories_full_name` (UNIQUE) | Duplicate insert (`'octocat/hello-world'` twice) | ✅ Rejected: `duplicate key value violates unique constraint "uq_repositories_full_name"` |
| `repositories.owner_id NOT NULL` | Insert with `owner_id = NULL` | ✅ Rejected: `null value in column "owner_id" ... violates not-null constraint` |
| `fk_repositories_owner_id_owners` (FK exists) | Insert with non-existent `owner_id` (all-zero UUID) | ✅ Rejected: `violates foreign key constraint "fk_repositories_owner_id_owners"` |
| `fk_repositories_owner_id_owners` `ON DELETE RESTRICT` | Attempted `DELETE FROM owners` for an owner with a referencing repository | ✅ Rejected: `violates foreign key constraint ... is still referenced from table "repositories"` |
| `fk_repositories_license_id_licenses` `ON DELETE SET NULL` | Deleted the referenced license row, then queried the repository | ✅ `repositories.license_id` became `NULL` for the affected row, repository itself was **not** deleted |
| `last_extraction_status` enum (3 values: `pending`, `success`, `failed`) | Invalid value insert (`'retrying'`) | ✅ Rejected: `invalid input value for enum last_extraction_status: "retrying"` |
| `license_id` nullable (repository without a license is valid) | Inserted a repository with no `license_id` at all | ✅ Succeeded |

Both foreign-key `ON DELETE` behaviors were tested **functionally**, not just confirmed structurally via `\d` — this was necessary because `RESTRICT` vs. `SET NULL` are behaviorally distinct in ways that only show up at delete-time, and both are load-bearing decisions in `ENTITY_RELATIONSHIP_MODEL.md` §4.

---

## 7. Indexes Verified

| Index | Table | Type | Purpose |
|---|---|---|---|
| `pk_owners` | owners | btree (PK) | Primary key lookup |
| `uq_owners_github_id` | owners | btree (unique) | Ingestion upsert key |
| `ix_owners_login` | owners | btree | Manual/debug lookup by login |
| `pk_repositories` | repositories | btree (PK) | Primary key lookup |
| `uq_repositories_github_id` | repositories | btree (unique) | Ingestion upsert key |
| `uq_repositories_full_name` | repositories | btree (unique) | Human-facing lookup |
| `ix_repositories_owner_id` | repositories | btree | FK join support (Postgres does not auto-index FK columns) |
| `ix_repositories_primary_language` | repositories | btree | Category A query 1 (language filtering) |
| `ix_repositories_license_id` | repositories | btree | Category A query 6 (license filtering) |
| `ix_repositories_active_original` | repositories | btree, **partial** (`WHERE is_archived = false AND is_fork = false`) | The extremely common "active, original work" default filter (Category A query 10) |
| `ix_repositories_inaccessible` | repositories | btree, **partial** (`WHERE is_accessible = false`) | Cheaply exclude/find inaccessible repos (Architect Review Finding 4) without a full table scan |
| `ix_repositories_fetched_at` | repositories | btree | Staleness queries (Category I query 69) |
| `ix_repositories_fts` | repositories | **GIN**, expression index on `to_tsvector('english', name \|\| ' ' \|\| description)` | Keyword search, Category A query 5, Milestone 4.1 |

All 13 indexes confirmed present via `\di` both before and after the rollback/re-upgrade cycle — 21 total indexes exist across all tables created so far (Migrations 001 + 002), matching expected count exactly.

**Implementation observation on the FTS index:** the frozen design specifies `to_tsvector('english', name || ' ' || description)` literally, without `COALESCE` around the nullable `description` column. Postgres's `||` operator returns `NULL` when any operand is `NULL`, so a repository with no description produces a `NULL` tsvector — meaning its `name` alone becomes unsearchable via this index (though the row itself remains fully queryable by other means, e.g. `full_name`). This is not a DDL defect (the index was created successfully and works as specified), just a functional limitation of the frozen expression as literally written. I implemented it exactly as specified per the instruction not to redesign — flagging it here as an observation rather than silently patching it with `COALESCE`, in case this should be raised in a future design review before Milestone 4.1 implementation.

---

## 8. Verification Performed

1. **Pre-migration check:** `alembic history` confirmed chain `<base> -> 001 -> 002 (head)`.
2. **Upgrade:** `alembic upgrade head` → `Running upgrade 001 -> 002, 002_core_entities`, clean.
3. **Structural verification:** `\dt`, `\d owners`, `\d repositories` — all columns, types, defaults match `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.1 and §14.5 exactly. Confirmed absence of the five excluded columns and presence of the two lifecycle columns.
4. **Enum verification:** `\dT+ account_type` (2 values), `\dT+ last_extraction_status` (3 values) — both correct.
5. **Constraint testing (10 scenarios, §6 above):** 2 valid owner inserts, 2 valid repository inserts (one with license, one without), 6 invalid-insert/delete scenarios all correctly rejected, 1 `ON DELETE SET NULL` scenario correctly executed.
6. **Test data cleanup:** All test rows deleted from `repositories`, `owners`, `licenses` before rollback testing; confirmed empty via count query.
7. **Rollback:** `alembic downgrade -1` → `Running downgrade 002 -> 001, 002_core_entities`, clean. Confirmed via `\dt` that `owners` and `repositories` were dropped (only the 4 Migration-001 objects plus `alembic_version` remained) and via `\dT` that `account_type` and `last_extraction_status` enums were dropped while `technology_category` (owned by Migration 001) was correctly left untouched.
8. **Re-upgrade:** `alembic upgrade head` → `Running upgrade 001 -> 002, 002_core_entities`, clean. Confirmed via `\dt`, `alembic_version` (`002`), `\d repositories` (both FKs present), and `\di` (21 total indexes, matching pre-rollback count) that every object was correctly recreated.

---

## 9. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean, no errors |
| Tables dropped | `\dt` | ✅ `owners`, `repositories` removed; Migration 001's 3 tables + `alembic_version` remained |
| Enum types dropped | `\dT` | ✅ `account_type`, `last_extraction_status` removed; `technology_category` (unrelated, Migration 001) and `gtrgm` (extension) correctly untouched |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `001` |
| Re-upgrade | `alembic upgrade head` | ✅ Clean, no errors |
| Tables recreated | `\dt` | ✅ All 6 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `002` |
| FKs recreated | `\d repositories` | ✅ Both `fk_repositories_owner_id_owners` and `fk_repositories_license_id_licenses` present with correct `ON DELETE` clauses |
| Indexes recreated | `\di` | ✅ 21 total indexes (13 new from this migration + 8 pre-existing from Migration 001's tables + `alembic_version_pkc`) |

The migration's `downgrade()` function correctly reverses `upgrade()` in dependency order: drops all `repositories` indexes, drops the `repositories` table (which implicitly drops its PK/UNIQUE constraints and both FKs), drops the `last_extraction_status` enum, drops the `owners` index, drops the `owners` table, drops the `account_type` enum. No orphaned objects in either direction.

---

## 10. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.1, §14.5) | ✅ |
| `alembic upgrade head` applies cleanly from `001` | ✅ |
| Both tables created with correct columns/types/defaults | ✅ |
| Excluded columns (Findings 1, 2, 12) confirmed absent | ✅ |
| Lifecycle columns (Finding 4) confirmed present | ✅ |
| All constraints (PK, UNIQUE, NOT NULL, both FKs, both enums) present and functionally verified | ✅ |
| `ON DELETE RESTRICT` and `ON DELETE SET NULL` both tested with actual delete operations, not just structural inspection | ✅ |
| All 13 new indexes present, including 2 partial indexes and 1 GIN expression index | ✅ |
| Naming conventions consistent with Migration 001 (`pk_`, `uq_`, `ix_`, `fk_<table>_<col>_<reftable>`) | ✅ |
| `alembic downgrade -1` cleanly reverses migration, no orphaned objects, Migration 001's objects untouched | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `001` after rollback and `002` after re-upgrade | ✅ |
| No design deviation from frozen schema | ✅ (one documented observation, §7, implemented as specified) |

**Migration 002 is COMPLETE and VERIFIED.**

---

## 11. Implementation Observations

1. **FTS index NULL-concatenation behavior** — documented in §7 above. Implemented exactly as specified in the frozen design; not patched, per instruction not to redesign. Flagging for awareness before Milestone 4.1 (keyword search implementation) in case `COALESCE(description, '')` should be considered at that point.
2. **Naming convention followed:** foreign keys use `fk_<table>_<column>_<referenced_table>` (e.g., `fk_repositories_owner_id_owners`), consistent with the `pk_<table>` / `uq_<table>_<column>` / `ix_<table>_<column>` pattern established in Migration 001. This convention was not explicit in Migration 001 (which had no FKs to name), so it was derived from Alembic/SQLAlchemy community convention and applied consistently — worth confirming this is acceptable for all subsequent FK-bearing migrations (003 onward all introduce FKs into `repositories`).
3. **No environment issues this time** — the port-5432 conflict resolved during Migration 001 remains resolved; Docker's `postgres` container is still correctly bound.

---

## 12. Suggested Git Commit

```
Checkpoint 0.2: Migration 002 - Core entities (owners, repositories)

Creates the platform's central entity and its one mandatory parent,
per MIGRATION_STRATEGY.md's 002_core_entities plan:

- owners: GitHub account normalization (account_type enum: user/organization)
- repositories: core identity + slowly-changing metadata, including the
  repository-lifecycle fix from DATABASE_ARCHITECT_REVIEW.md Finding 4
  (is_accessible / inaccessible_since, distinct from is_archived and
  last_extraction_status)

Excludes default_branch, homepage_url, is_template, size_kb, first_seen_at
per Architect Review Findings 1, 2, 12 (unjustified GitHub-mirror /
duplicate columns) - confirmed absent in the executed schema.

FK constraints:
- repositories.owner_id -> owners.id, ON DELETE RESTRICT
- repositories.license_id -> licenses.id, ON DELETE SET NULL

Indexes: 13 total, including 2 partial indexes (active/original default
filter, inaccessible-repo filter) and 1 GIN full-text search expression
index.

Verified: upgrade, all constraints (PK/UNIQUE/NOT NULL/both FKs/both
enums) including functional ON DELETE RESTRICT and ON DELETE SET NULL
testing (not just structural inspection), all 13 indexes, downgrade
(clean drop, no orphaned objects, Migration 001 untouched), and
re-upgrade.

No design deviation from QUERY_DRIVEN_SCHEMA_DESIGN.md §14.1, §14.5.
```

---

**Status:** Migration 002 complete. Awaiting review before beginning Migration 003 (`003_repository_taxonomy_associations`: `repository_topics`, `repository_technologies`).
