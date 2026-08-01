# Migration 001 Report — Reference Tables

**Checkpoint:** 0.2 (Implementation, Step 1 of 9)
**Migration ID:** `001` (`001_reference_tables.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-01

---

## 1. Purpose

Per `MIGRATION_STRATEGY.md` §2 (`001_reference_tables`), this migration creates the three reference/taxonomy tables that have no foreign keys pointing *into* the core entity graph: `licenses`, `topics`, `technologies`.

**Why these three tables belong together in one migration:**

- All three are independent lookup tables — none depends on `repositories` or `owners` existing first, so they can be created against a completely empty database.
- Grouping them avoids three near-empty migrations for what is conceptually one step: "seed the taxonomies the rest of the schema will reference."
  - `licenses` — normalizes SPDX license identifiers (`DATABASE_DESIGN.md` §2.3)
  - `topics` — GitHub's maintainer-assigned tag taxonomy (`DATABASE_DESIGN.md` §2.4)
  - `technologies` — curated, deduplicated technology taxonomy (`DATABASE_DESIGN.md` §2.5)
- This is the first migration in the sequence (`down_revision = None`) and depends on nothing but the `uuid-ossp` extension already installed during Checkpoint 0.1's infrastructure setup.
- Owning modules differ (`licenses`/`topics` populated by Data Acquisition, `technologies` by Feature Engineering), but the migration itself is pure schema — data population happens in application code later, not here.

---

## 2. Tables Created

### 2.1 `licenses`

| Column | Type | Constraint |
|---|---|---|
| `id` | uuid | PK, default `uuid_generate_v4()` |
| `spdx_id` | text | NOT NULL, UNIQUE |
| `name` | text | NOT NULL |
| `is_osi_approved` | boolean | NOT NULL, default `false` |

### 2.2 `topics`

| Column | Type | Constraint |
|---|---|---|
| `id` | uuid | PK, default `uuid_generate_v4()` |
| `slug` | text | NOT NULL, UNIQUE |
| `name` | text | NOT NULL |

### 2.3 `technologies`

| Column | Type | Constraint |
|---|---|---|
| `id` | uuid | PK, default `uuid_generate_v4()` |
| `slug` | text | NOT NULL, UNIQUE |
| `name` | text | NOT NULL |
| `category` | enum(`language`,`framework`,`database`,`testing_tool`,`devops_tool`,`platform`) | NOT NULL |
| `homepage_url` | text | nullable |
| `created_at` | timestamptz | NOT NULL, default `now()` |

All column definitions verified to match `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.2–14.4 exactly — no drift between the frozen design and the executed migration.

---

## 3. Constraints Verified

| Constraint | Table | Verification method | Result |
|---|---|---|---|
| `pk_licenses` (PRIMARY KEY on `id`) | licenses | `\d licenses` | ✅ Present |
| `uq_licenses_spdx_id` (UNIQUE on `spdx_id`) | licenses | Attempted duplicate insert (`spdx_id='MIT'` twice) | ✅ Rejected: `duplicate key value violates unique constraint "uq_licenses_spdx_id"` |
| `pk_topics` (PRIMARY KEY on `id`) | topics | `\d topics` | ✅ Present |
| `uq_topics_slug` (UNIQUE on `slug`) | topics | `\d topics` | ✅ Present |
| `pk_technologies` (PRIMARY KEY on `id`) | technologies | `\d technologies` | ✅ Present |
| `uq_technologies_slug` (UNIQUE on `slug`) | technologies | `\d technologies` | ✅ Present |
| `technology_category` enum, 6 values | technologies | `\dT+ technology_category` + invalid-value insert test | ✅ All 6 values present (`language`, `framework`, `database`, `testing_tool`, `devops_tool`, `platform`); invalid value `'not_a_category'` correctly rejected with `invalid input value for enum technology_category` |
| `NOT NULL` on required columns | all three | `\d` on each table | ✅ Matches spec exactly |

---

## 4. Indexes Verified

| Index | Table | Type | Purpose |
|---|---|---|---|
| `pk_licenses` | licenses | btree (PK) | Primary key lookup |
| `uq_licenses_spdx_id` | licenses | btree (unique) | License filtering (Category A query 6) |
| `pk_topics` | topics | btree (PK) | Primary key lookup |
| `uq_topics_slug` | topics | btree (unique) | Topic lookup by slug |
| `pk_technologies` | technologies | btree (PK) | Primary key lookup |
| `uq_technologies_slug` | technologies | btree (unique) | Technology lookup by slug |
| `ix_technologies_category` | technologies | btree | Supports "list all databases" / "list all languages" queries (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.4, Category D query 29) |

All 7 indexes confirmed present via `\di` after the final re-upgrade — no drift from the design.

---

## 5. Verification Steps Performed

1. **Pre-migration state check:** `alembic current` → empty (no version applied); `alembic history` → confirmed `001_reference_tables` is the sole revision, head of chain.
2. **Environment fix:** Discovered and resolved a host-level port conflict — a native Windows PostgreSQL 18 service (`postgresql-x64-18`) was bound to port 5432, preventing the Docker `postgres` container from publishing its port. User stopped the native service; `docker inspect` confirmed the container then correctly published `127.0.0.1:5432 -> 5432/tcp`.
3. **Upgrade:** `alembic upgrade head` → applied cleanly, `Running upgrade  -> 001, 001_reference_tables`.
4. **Structural verification:** `\dt`, `\d licenses`, `\d topics`, `\d technologies` — all three tables present with exact column types, defaults, and constraints matching the frozen schema.
5. **Enum verification:** `\dT+ technology_category` — confirmed all 6 category values in the correct order.
6. **Constraint enforcement testing:**
   - Inserted one valid row into each table — succeeded.
   - Attempted a duplicate `spdx_id='MIT'` — correctly rejected by the unique constraint.
   - Attempted an invalid enum value (`'not_a_category'`) — correctly rejected by PostgreSQL's enum type check.
7. **Test data cleanup:** Deleted all test rows before rollback testing to verify rollback against a clean-but-migrated state.
8. **Rollback verification:** `alembic downgrade base` → applied cleanly, `Running downgrade 001 -> , 001_reference_tables`. Confirmed via `\dt` that all three tables were dropped (only `alembic_version` remained) and via `\dT` that the `technology_category` enum type was also dropped (only the pre-existing `gtrgm` type from the `pg_trgm` extension remained).
9. **Re-upgrade verification:** `alembic upgrade head` → re-applied cleanly from the dropped state. Confirmed via `\dt` that all three tables exist again, `alembic_version` shows `001`, and via `\di` that all 7 indexes are present.

---

## 6. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade base` | ✅ Clean, no errors |
| Tables dropped | `\dt` | ✅ Only `alembic_version` remained |
| Enum type dropped | `\dT` | ✅ `technology_category` removed; `gtrgm` (unrelated, pre-existing) remained |
| Re-upgrade | `alembic upgrade head` | ✅ Clean, no errors |
| Tables recreated | `\dt` | ✅ All 3 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `001` |
| Indexes recreated | `\di` | ✅ All 7 indexes present |

The migration's `downgrade()` function correctly reverses `upgrade()` in dependency order (drops the index before the table, drops tables before the enum type they depend on), with no orphaned objects left behind in either direction.

---

## 7. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.2–14.4) | ✅ |
| `alembic upgrade head` applies cleanly | ✅ |
| All 3 tables created with correct columns/types/defaults | ✅ |
| All constraints (PK, UNIQUE, enum) present and enforced | ✅ |
| All 7 indexes present | ✅ |
| `alembic downgrade base` cleanly reverses migration, no orphaned objects | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` table correctly tracks revision state throughout | ✅ |
| No data or schema drift between frozen design and executed migration | ✅ |

**Migration 001 is COMPLETE and VERIFIED.**

---

## 8. Environment Note (Non-Design Issue)

During verification, a local environment conflict was discovered and resolved: a native Windows-installed PostgreSQL 18 service was occupying port 5432, which silently prevented the Docker container from publishing its own port mapping (despite `docker-compose.yml` correctly specifying `127.0.0.1:5432:5432`). This was a host-machine configuration issue, not a defect in the frozen design or migration. The user stopped the native service; the Docker container then bound the port correctly. This should not require any repeated action for Migration 002 onward unless the native service is restarted.

---

## 9. Suggested Git Commit

```
Checkpoint 0.2: Migration 001 - Reference tables (licenses, topics, technologies)

Creates the three independent reference/taxonomy tables with no FK
dependency on the core entity graph, per MIGRATION_STRATEGY.md's
001_reference_tables plan:

- licenses: SPDX license taxonomy (spdx_id unique, is_osi_approved flag)
- topics: GitHub maintainer-assigned tag taxonomy (slug unique)
- technologies: curated tech taxonomy with category enum
  (language/framework/database/testing_tool/devops_tool/platform)
  and ix_technologies_category index for category-scoped queries

Verified: upgrade, all constraints (PK/UNIQUE/enum), all 7 indexes,
downgrade (clean drop, no orphaned objects), and re-upgrade.

No design deviation from QUERY_DRIVEN_SCHEMA_DESIGN.md §14.2-14.4.
```

---

**Status:** Migration 001 complete. Awaiting review before beginning Migration 002 (`002_core_entities`: `owners`, `repositories`).
