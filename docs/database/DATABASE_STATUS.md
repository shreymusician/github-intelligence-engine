# Database Implementation Status

**Checkpoint:** 0.2 (Database Schema Design & Initial Migrations)
**Design status:** FROZEN — see `DATABASE_DESIGN_CHANGELOG.md` at repo root
**Last updated:** 2026-08-01
**Current Alembic revision:** `005` (`005_repository_snapshot_history`)

This document tracks *implementation* progress against the frozen design. It is not itself a design document — for the authoritative schema, see `DATABASE_DESIGN.md`, `ENTITY_RELATIONSHIP_MODEL.md`, `QUERY_DRIVEN_SCHEMA_DESIGN.md`, `MIGRATION_STRATEGY.md`, `DATABASE_ARCHITECT_REVIEW.md`, and `DATABASE_DESIGN_CHANGELOG.md` at the repository root.

---

## Migration Progress

| # | Migration | Tables Created | Status | Report |
|---|---|---|---|---|
| 001 | `001_reference_tables` | `licenses`, `topics`, `technologies` | ✅ Applied, verified, rollback-tested | `MIGRATION_001_REPORT.md` |
| 002 | `002_core_entities` | `owners`, `repositories` | ✅ Applied, verified, rollback-tested | `MIGRATION_002_REPORT.md` |
| 003 | `003_repository_taxonomy_associations` | `repository_topics`, `repository_technologies` | ✅ Applied, verified, rollback-tested | `MIGRATION_003_REPORT.md` |
| 004 | `004_repository_dependencies` | `repository_dependencies` | ✅ Applied, verified, rollback-tested | `MIGRATION_004_REPORT.md` |
| 005 | `005_repository_snapshot_history` | `repository_snapshot` | ✅ Applied, verified, rollback-tested | `MIGRATION_005_REPORT.md` |
| 006 | `006_feature_store` | `repository_metrics` | ⭕ Not started | — |
| 007 | `007_intelligence_scores` | `repository_scores` | ⭕ Not started | — |
| 008 | `008_embeddings` | `repository_embeddings` | ⭕ Not started | — |
| 009 | `009_similarity` | `repository_similarity` | ⭕ Not started | — |

**5 of 9 migrations complete.** All applied migrations have been individually verified (structural + functional), rollback-tested (`downgrade -1` / `downgrade base`), and re-applied cleanly, per the engineering workflow established starting with Migration 001.

---

## Tables Currently in the Database

| Table | Migration | Row-count driver | Notes |
|---|---|---|---|
| `licenses` | 001 | Static (~500, SPDX list) | Reference table |
| `topics` | 001 | Sub-linear w/ repo count | Reference table |
| `technologies` | 001 | Sub-linear w/ repo count | Curated taxonomy |
| `owners` | 002 | Sub-linear w/ repo count | GitHub account normalization |
| `repositories` | 002 | Linear w/ repo count | Central entity; includes `is_accessible`/`inaccessible_since` lifecycle columns (Architect Review Finding 4) |
| `repository_topics` | 003 | ~3-8 rows/repo | Pure M:N join |
| `repository_technologies` | 003 | ~5-15 rows/repo | Associative entity; includes `removed_at` (Finding 6) |
| `repository_dependencies` | 004 | ~30-100 rows/repo | Highest-volume table; first partitioning candidate (Finding 9) |
| `repository_snapshot` | 005 | Weekly cadence × repo count | `size_kb`'s sole authoritative home (Finding 2); coordinated partitioning target with `repository_metrics` (006) |

**9 tables, 21 named indexes (excluding `alembic_version`), 3 CHECK constraints, 8 foreign keys, 8 enum types**, as of revision `005`.

---

## Enum Types Currently in the Database

| Enum | Values | Introduced In |
|---|---|---|
| `technology_category` | language, framework, database, testing_tool, devops_tool, platform | 001 |
| `account_type` | user, organization | 002 |
| `last_extraction_status` | pending, success, failed | 002 |
| `technology_role` | primary, secondary, dev | 003 |
| `detected_via` | manifest, dockerfile, ci_config, linguist | 003 |
| `package_ecosystem` | npm, pypi, cargo, go, maven, nuget, rubygems | 004 |

(`repository_snapshot`, Migration 005, introduces no new enum type.)

---

## Architect Review Findings — Implementation Status

All findings from `DATABASE_ARCHITECT_REVIEW.md` were resolved at the **design** level before implementation began (see `DATABASE_DESIGN_CHANGELOG.md`). The table below tracks which findings have now been *physically realized* in the database via the migrations applied so far:

| Finding | Severity | Resolution | Realized In |
|---|---|---|---|
| 1. Unjustified GitHub-mirror columns | Low | Removed from `repositories` | ✅ Migration 002 (confirmed absent) |
| 2. `size_kb` duplicated | Low | Sole home: `repository_snapshot` | ✅ Migration 005 |
| 3. `watchers_count` near-duplicate | Info | Retained, documented | ✅ Migration 005 |
| 4. No repository-lifecycle "gone from GitHub" state | **High** | `is_accessible`, `inaccessible_since` added | ✅ Migration 002 |
| 5. Snapshot cadence assumed daily | Medium | Defaulted to weekly | ✅ Migration 005 |
| 6. No removal timestamp on technology associations | Medium | `removed_at` added | ✅ Migration 003 |
| 7. Embedding vector dimension mismatch | **High** | `vector(768)` | ⭕ Pending Migration 008 |
| 8. Normalized ML feature-vector storage undecided | Low | Deferred to Checkpoint 2.6 | N/A (no schema object yet) |
| 9. Partitioning key not committed | **High** | Declared (not implemented) for 4 tables | ✅ Documented in Migrations 004-005; pending 006-007 |
| 10. CASCADE delete blast radius | Medium | Operational guidance only | ✅ Documented in Migration 002+ reports |
| 11. Columnar-migration commitment | Medium | DAO/repository abstraction guidance | Carried forward, not yet applicable (no ORM layer built yet) |
| 12. `first_seen_at`/`created_at` redundant | Low | `first_seen_at` removed | ✅ Migration 002 (confirmed absent) |
| 13. (Restates Finding 1) | Low | Same as Finding 1 | ✅ Migration 002 |

**All 3 High-severity findings applicable so far (4, 9) are realized in the database; Finding 7 awaits Migration 008.**

---

## Verification Methodology (Applied Uniformly, Migrations 001-005)

Every migration listed above followed the same 7-phase workflow:

1. **Design Verification** — re-read the frozen design section, explain rationale, dependencies, and index justification before writing code
2. **Implementation** — Alembic migration file, following naming conventions established in Migration 001 (`pk_`, `uq_`, `ix_`, `fk_<table>_<col>_<reftable>`, `ck_<table>_<description>`)
3. **Execution** — `alembic upgrade head` against the Docker PostgreSQL instance
4. **Structural Verification** — `\d`/`\dt`/`\dT` inspection confirming every column, default, constraint, and index matches the frozen schema exactly
5. **Functional Verification** — real inserts/deletes proving constraints are actually enforced (not just structurally present): valid data, duplicate rejection, invalid-FK rejection, invalid-enum rejection, CHECK-boundary testing, and functional `ON DELETE` behavior
6. **Rollback Verification** — `alembic downgrade -1`, confirming clean removal with no orphaned objects and no impact on prior migrations, then `alembic upgrade head` to confirm clean re-creation
7. **Documentation** — a `MIGRATION_NNN_REPORT.md` per migration, capturing purpose, dependency diagram, objects, constraints, verification evidence, and observations

One genuine design ambiguity was discovered during this process (Migration 005, §3 of `MIGRATION_005_REPORT.md`) and resolved via explicit user confirmation rather than assumption, consistent with the standing instruction to stop and report rather than guess.

---

## Known Gaps / Deferred Items (Not Yet Due)

These are documented in the frozen design as intentionally deferred, not implementation oversights:

- **Point-in-time technology reconstruction** (Category H, Query 68) — accepted V1 gap, narrowed by `removed_at` (Migration 003) but not fully solved.
- **Normalized/scaled ML feature vectors** (Finding 8) — no table exists yet; deferred to Checkpoint 2.6's implementer.
- **Physical partitioning** — declared partition keys exist in documentation (Migrations 004-005; pending 006-007) but no `PARTITION BY` DDL has been executed anywhere, per the frozen design's explicit YAGNI stance. Trigger condition: any of the four flagged tables approaching 10-50M actual rows.

---

## Next Step

**Migration 006** (`006_feature_store`: `repository_metrics`) — the largest single table definition in the schema (~27 typed columns + 2 JSONB columns), coordinated with Migration 005 for future partitioning on `snapshot_date`. Awaiting user review of Migration 005 before proceeding.
