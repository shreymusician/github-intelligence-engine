# Migration 009 Report — Repository Similarity (Precomputed Top-K)

**Checkpoint:** 0.2 (Implementation, Step 9 of 9 — FINAL migration)
**Migration ID:** `009` (`009_repository_similarity.py`)
**Status:** ✅ Applied, verified, rollback-tested, re-applied
**Date executed:** 2026-08-03

---

## 1. Purpose

Per `DATABASE_DESIGN.md` §2.12 and `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.13, this migration creates `repository_similarity`: precomputed "top-K most similar repositories" pairs, powering `SYSTEM_ARCHITECTURE.md` Module 7 (Recommendations) and `IMPLEMENTATION_ROADMAP.md` Checkpoints 3.3/5.1. It answers "given repository X, what are the N most similar repositories?" as a cheap indexed lookup, without computing that comparison at query time.

This is the ninth and **final** migration of Checkpoint 0.2.

---

## 2. Architectural Rationale

### 2.1 Why separate from `repository_embeddings` (Migration 008)

`repository_embeddings` stores the raw input — a vector per repository per model — but does not itself answer "which repositories are similar." `repository_similarity` stores the *derived output* of running a comparison (embedding cosine distance, engineered-feature distance, or a hybrid) and materializing only the top-K winners. This mirrors the established pattern in this schema of separating inputs from derived outputs (`repository_metrics` → `repository_scores` is the same shape).

### 2.2 Why Top-K, not a full N×N matrix

Per `DATABASE_DESIGN.md` §2.12: at V1 scale (100–1,000 repositories) a full similarity matrix is trivial, but `SYSTEM_ARCHITECTURE.md` §9 plans growth to 10K, 100K, and beyond. An N² relationship table becomes untenable past ~50K repositories (2.5 billion pairs). Storing only the top-K (e.g., top 20) per repository per method keeps the table's growth **linear** in repository count. `DATABASE_ARCHITECT_REVIEW.md` explicitly validates this at the review's assumed 1M-repository scale: K=20, 2 methods → 40M rows — "the single decision in the whole schema that most clearly separates a design that survives this scale from one that doesn't."

### 2.3 Why precomputed rather than computed per-search

Follows directly from the Top-K design: computing "top-20 most similar out of N repositories" at query time means an O(N) distance computation against every other repository on every search request. Precomputing on each intelligence-generation cycle converts an expensive fan-out computation into a cheap indexed lookup at read time.

### 2.4 Why last in sequence

Per `MIGRATION_STRATEGY.md`: it is the only self-referential table (both FKs point back to `repositories.id`), and per `DATABASE_DESIGN.md` §2.12 it is *derived from* embeddings and metrics rather than an independent data source — logically the last piece of the intelligence pipeline to materialize.

### 2.5 Multiple methods, no weighting breakdown column

Similarity can be computed via `embedding_cosine`, `feature_distance`, or `hybrid`, each independently queryable, per `TECHNICAL_IMPLEMENTATION_IDEAS.md` §7's ranking-signal framework. `DATABASE_ARCHITECT_REVIEW.md` notes `hybrid` is "under-specified" (no column records how a hybrid score was weighted) but explicitly logs this as "not a defect worth fixing now — no query needs the weighting breakdown," not a current gap. No such column was added.

### 2.6 Lifecycle and no partition key

Per `DATABASE_DESIGN.md` §2.12: "Recomputed on each intelligence-generation cycle; old rows for a method are replaced, not accumulated" — unlike `repository_metrics`/`repository_scores`, similarity has no historical-trend use case. This is also why `repository_similarity` is the only intelligence-output table **not** listed in `QUERY_DRIVEN_SCHEMA_DESIGN.md` §16's declared-partition-key table — it does not grow unbounded over time the way an append-only table does.

---

## 3. Dependency Analysis

### 3.1 Dependency Diagram

```
002_core_entities (owners, repositories)
        │
        ▼
009_similarity
        │
        └── repository_similarity
              ├── FK repository_id → repositories.id         (CASCADE)
              └── FK similar_repository_id → repositories.id (CASCADE)
              (self-referential: both FKs target the same table)

(No schema dependency on 001, 003, 004, 005, 006, or 007. Only a
 soft/application-level dependency on 008_embeddings - not a schema
 dependency - since similarity_method can also be 'feature_distance',
 sourced from 006_feature_store instead.)
```

### 3.2 Dependencies

- **Schema (hard) dependency:** `002_core_entities` only, via two FKs to `repositories.id` — the only self-referential table in the schema.
- **No schema dependency** on 001, 003, 004, 005, 006, or 007.
- **Soft/application-level dependency** on `008_embeddings` — `similarity_method = 'embedding_cosine'` is sourced from `repository_embeddings`, but `'feature_distance'` is sourced from `repository_metrics` (006) instead, so no DB-level FK to `repository_embeddings` exists or is warranted.

### 3.3 Future migrations depending on it

None. This is the final migration of Checkpoint 0.2 — `repository_similarity` is a schema leaf, consumed only by the application layer (Search, Recommendations).

---

## 4. Design Verification (Phase 1) — No Ambiguity Found

`QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.13 fully specifies the table: 6 columns, a composite PK, one CHECK constraint, one explicit index — all mutually consistent with `MIGRATION_STRATEGY.md`, `DATABASE_DESIGN.md` §2.12, `DATABASE_ARCHITECT_REVIEW.md`, and `DATABASE_DESIGN_CHANGELOG.md`. No stop-and-report was required.

---

## 5. Objects Created

### 5.1 `repository_similarity`

| Column | Type | Constraint |
|---|---|---|
| `repository_id` | uuid | PK (part 1), NOT NULL, FK → `repositories.id` (CASCADE) |
| `similar_repository_id` | uuid | PK (part 2), NOT NULL, FK → `repositories.id` (CASCADE) |
| `similarity_method` | enum(`embedding_cosine`, `feature_distance`, `hybrid`) | PK (part 3), NOT NULL |
| `similarity_score` | numeric(5,4) | NOT NULL |
| `rank` | smallint | NOT NULL |
| `computed_at` | timestamptz | NOT NULL, default `now()` |

**PK strategy:** composite `(repository_id, similar_repository_id, similarity_method)` — a natural key, no surrogate, consistent with `repository_topics`, `repository_technologies`, and `repository_embeddings`'s treatment (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §13).

**New enum type:** `similarity_method` (`embedding_cosine`, `feature_distance`, `hybrid`) — verified via `pg_enum`, correct values and order.

---

## 6. Constraints

| Constraint | Verification method | Result |
|---|---|---|
| `pk_repository_similarity` (composite PK) | `\d repository_similarity` | ✅ Present |
| `repository_id NOT NULL` | Implicit via PK | ✅ Present |
| `similar_repository_id NOT NULL` | Implicit via PK | ✅ Present |
| `similarity_method NOT NULL` | Implicit via PK | ✅ Present |
| `similarity_score NOT NULL` | NULL insert | ✅ Rejected |
| `rank NOT NULL` | NULL insert | ✅ Rejected |
| `similarity_method` enum domain | Insert invalid value `'jaccard'` | ✅ Rejected: `invalid input value for enum similarity_method` |
| `ck_repository_similarity_no_self_similarity` (`repository_id <> similar_repository_id`) | Insert `repository_id = similar_repository_id` | ✅ Rejected: `violates check constraint "ck_repository_similarity_no_self_similarity"` |
| Duplicate `(repository_id, similar_repository_id, similarity_method)` | Insert same triple twice | ✅ Rejected via composite PK |
| Same pair, different `similarity_method` (method-scoping) | Insert same repo pair with `feature_distance` after `embedding_cosine` already exists | ✅ Succeeded — independent row per method |

---

## 7. Foreign Keys

| Constraint | Column | References | On Delete | Verified |
|---|---|---|---|---|
| `fk_repository_similarity_repository_id_repositories` | `repository_similarity.repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed; functionally tested (invalid-FK rejection + CASCADE deletion) |
| `fk_repository_similarity_similar_repository_id_repositories` | `repository_similarity.similar_repository_id` | `repositories.id` | `CASCADE` | ✅ Structurally confirmed; functionally tested (invalid-FK rejection + CASCADE deletion) |

Both FKs target the same table (`repositories`) — this is the schema's only self-referential table.

---

## 8. Indexes

| Index | Type | Purpose | Documented target |
|---|---|---|---|
| `pk_repository_similarity` | btree (composite PK) | Point lookup on `(repository_id, similar_repository_id, similarity_method)` | `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.13 |
| `ix_repository_similarity_repository_id_similarity_method_rank` | btree, `(repository_id, similarity_method, rank)` | "Top-K for repository X, method Y," ordered by `rank` — guarantees index-served ordering, not sorted at query time | §14.13; §15 Cross-Cutting Index Summary, "Similarity lookup" row |

**Empirical confirmation:** `EXPLAIN` on the Top-K query (`WHERE repository_id = ? AND similarity_method = ? ORDER BY rank LIMIT 20`) showed the planner using `Index Scan using ix_repository_similarity_repository_id_similarity_method_rank` directly.

No partition key is declared for this table (unlike `repository_metrics`, `repository_snapshot`, `repository_scores`, `repository_dependencies`) — consistent with §2.6 above.

---

## 9. Verification Performed

**Structural (Phase 4):** `\dt`, `\d repository_similarity`, `\dT`, `alembic_version` — all 6 columns, types, defaults, nullability, composite PK, both FKs, CHECK constraint, and both indexes matched `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.13 exactly. 14 tables total (13 from Migrations 001–008 + `repository_similarity`). `similarity_method` enum confirmed with 3 correctly-ordered values.

**Functional (Phase 5):**

| # | Scenario | Result |
|---|---|---|
| 1 | Valid insert (all columns) | ✅ Inserted and retrieved intact |
| 2 | Self-similarity (`repository_id = similar_repository_id`) | ✅ Rejected via CHECK constraint |
| 3 | Invalid `repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 4 | Invalid `similar_repository_id` (non-existent UUID) | ✅ Rejected via FK constraint |
| 5 | `NULL similarity_score` | ✅ Rejected via NOT NULL |
| 6 | `NULL rank` | ✅ Rejected via NOT NULL |
| 7 | Invalid enum value (`'jaccard'`) | ✅ Rejected via enum domain |
| 8 | Duplicate `(repository_id, similar_repository_id, similarity_method)` | ✅ Rejected via composite PK |
| 9 | Same repo pair, different `similarity_method` | ✅ Succeeded — method-scoping confirmed |
| 10 | Top-K retrieval query (`WHERE repository_id, similarity_method ORDER BY rank LIMIT 20`) | ✅ Returned correct rows in rank order |
| 11 | `EXPLAIN` on Top-K query | ✅ Planner used `ix_repository_similarity_repository_id_similarity_method_rank` via Index Scan |
| 12 | Nearest-neighbor style lookup (best match across all methods for a repo, `ORDER BY similarity_score DESC LIMIT 1`) | ✅ Returned correct row |
| 13 | `ON DELETE CASCADE` via `repository_id` (delete a repo that is a similarity *source*) | ✅ 3 rows before, 0 after |
| 14 | `ON DELETE CASCADE` via `similar_repository_id` (delete a repo that is a similarity *target*) | ✅ 1 row before, 0 after |

All test data (`repository_similarity`, `repositories`, `owners`) deleted after verification; confirmed empty via count query before proceeding to rollback testing.

---

## 10. Rollback Verification

| Step | Command | Result |
|---|---|---|
| Downgrade | `alembic downgrade -1` | ✅ Clean: `Running downgrade 009 -> 008, 009_similarity` |
| Table dropped | `\dt` | ✅ `repository_similarity` removed; all 12 Migration 001–008 tables + `alembic_version` remained (13 tables total) |
| Enum dropped | `\dT` | ✅ `similarity_method` removed; all 7 prior enum types remained |
| Version reverted | `SELECT * FROM alembic_version` | ✅ `008` |
| Migration 008 object intact | `\d repository_embeddings` | ✅ Unaffected, structurally identical to pre-rollback state |
| Re-upgrade | `alembic upgrade head` | ✅ Clean: `Running upgrade 008 -> 009, 009_similarity` |
| Table recreated | `\dt` | ✅ All 14 tables present |
| Version recorded | `SELECT * FROM alembic_version` | ✅ `009` |
| Table empty after re-upgrade | `SELECT count(*) FROM repository_similarity` | ✅ `0` |
| All constraints/indexes/FKs identical after re-upgrade | `\d repository_similarity` | ✅ Identical to pre-rollback state |

No orphaned objects in either direction. `downgrade()` drops the index, the table, then the `similarity_method` enum (correct order, matching the established pattern from Migrations 004/007).

---

## 11. Performance Considerations

- The Top-K access pattern (`WHERE repository_id = ? AND similarity_method = ? ORDER BY rank LIMIT K`) is fully index-served by `ix_repository_similarity_repository_id_similarity_method_rank` — confirmed via `EXPLAIN` (Index Scan, no sort step).
- Bounded row growth by design: `repository_count × K × method_count`. At 1,000 repos, K=20, 2 methods: 40,000 rows. At 100K repos: 4M rows — linear, not quadratic (`DATABASE_ARCHITECT_REVIEW.md` Finding validated at 1M-repo scale: 40M rows).
- No partitioning required or declared, since rows are replaced (not accumulated) on each recomputation cycle — the table does not grow with time, only with repository count and method count, both of which stay well within single-table performance envelopes at any scale this schema targets.
- `ON DELETE CASCADE` on both FKs means deleting a repository silently prunes all similarity rows referencing it in either direction — consistent with `DATABASE_ARCHITECT_REVIEW.md` Finding 10's guidance that CASCADE is correct for integrity but hard-deletes should remain an administrative/exceptional operation.

---

## 12. Definition of Done

| Criterion | Status |
|---|---|
| Migration file matches frozen design exactly, no ambiguity encountered | ✅ |
| `alembic upgrade head` applies cleanly from `008` | ✅ |
| Table created with correct columns/types/defaults (6 columns) | ✅ |
| Composite PK `(repository_id, similar_repository_id, similarity_method)` per §14.13's PK strategy | ✅ |
| Both FKs present, self-referential, `ON DELETE CASCADE`, functionally tested in both directions | ✅ |
| CHECK constraint preventing self-similarity, functionally tested | ✅ |
| `NOT NULL` tested on `similarity_score` and `rank` | ✅ |
| `similarity_method` enum: correct 3 values, invalid-value rejection tested | ✅ |
| Method-scoping behavior functionally proven (same pair, multiple methods) | ✅ |
| Top-K retrieval query functionally correct and `EXPLAIN`-confirmed index usage | ✅ |
| `alembic downgrade -1` cleanly reverses migration; Migrations 001–008 fully intact | ✅ |
| Re-running `alembic upgrade head` after rollback succeeds cleanly | ✅ |
| `alembic_version` correctly tracks `008` after rollback and `009` after re-upgrade | ✅ |
| No previous migration modified | ✅ |
| No undocumented constraints introduced | ✅ |

**Migration 009 is COMPLETE and VERIFIED.**

---

## 13. Observations (Frozen Design Not Modified)

1. **No design ambiguity encountered** — `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.13 is fully and unambiguously specified.
2. **No design deviation.** Every column, constraint, and index in this migration is a direct, literal implementation of the frozen design. The deliberate absence of a hybrid-weighting JSONB column and the deliberate absence of a partition key are both confirmed as intentional, not oversights.
3. **First and only self-referential table** in the schema — both FKs correctly named and independently verified (`fk_repository_similarity_repository_id_repositories`, `fk_repository_similarity_similar_repository_id_repositories`), disambiguating the two relationships to the same target table.
4. **This is the final migration of Checkpoint 0.2.** No further migrations are planned in this checkpoint.

---

## 14. Suggested Git Commit

```
Checkpoint 0.2: Migration 009 - Repository similarity (precomputed top-K)

Creates repository_similarity: precomputed "top-K most similar
repositories" pairs, powering Recommendations (SYSTEM_ARCHITECTURE.md
Module 7), per DATABASE_DESIGN.md Sec2.12 and
QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.13. Final migration of
Checkpoint 0.2.

Top-K, not a full N x N matrix: an N^2 similarity table becomes
untenable past ~50K repositories (2.5B pairs), while SYSTEM_ARCHITECTURE.md
Sec9 plans growth well beyond that. Storing only the top-K per
repository per method keeps growth linear in repository count -
validated at 1M repos, K=20, 2 methods -> 40M rows by
DATABASE_ARCHITECT_REVIEW.md as "the single decision in the whole
schema that most clearly separates a design that survives this scale
from one that doesn't."

Last in migration sequence because it is the only self-referential
table (repository_id, similar_repository_id both -> repositories.id)
and is derived from embeddings/metrics rather than an independent data
source - logically the last piece of the intelligence pipeline to
materialize.

Composite PK (repository_id, similar_repository_id, similarity_method):
method-scoped, since embedding_cosine / feature_distance / hybrid are
independently queryable strategies. No weighting-breakdown column for
'hybrid' - DATABASE_ARCHITECT_REVIEW.md explicitly logs this as
under-specified but not a current defect (no query needs it).

CHECK (repository_id <> similar_repository_id) prevents self-similarity
rows. Old rows per method are replaced (not accumulated) on each
recomputation cycle - no historical-trend use case, and therefore (unlike
repository_metrics/snapshot/scores/dependencies) no declared partition
key for this table.

Only FK dependency is repositories.id (Migration 002), via two FKs to
the same table. Only a soft/application-level dependency on
008_embeddings - no DB-level FK, since similarity_method can also be
'feature_distance', sourced from repository_metrics (006) instead.

Single explicit index INDEX(repository_id, similarity_method, rank)
guarantees the Top-K "for repo X, method Y, ordered by rank" access
pattern is index-served, not sorted at query time - EXPLAIN-confirmed.

Verified: upgrade, composite-PK duplicate rejection, method-scoping
(multiple methods per pair), self-similarity CHECK rejection, both FKs
(rejection + functional CASCADE deletion in both directions), NOT NULL
on similarity_score/rank, enum domain rejection, Top-K retrieval query
correctness, EXPLAIN-validated index usage, downgrade (clean drop incl.
enum teardown, Migrations 001-008 untouched), and re-upgrade.

No design deviation - direct implementation of DATABASE_DESIGN.md
Sec2.12 and QUERY_DRIVEN_SCHEMA_DESIGN.md Sec14.13. This completes all
9 migrations of Checkpoint 0.2.
```

---

**Status:** Migration 009 complete. This is the final migration of Checkpoint 0.2. Awaiting review before compiling the Checkpoint 0.2 Final Report.
