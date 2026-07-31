# Database Design Changelog — Architect Review Remediation

**Checkpoint:** 0.2 (final design iteration before implementation)
**Source:** `DATABASE_ARCHITECT_REVIEW.md` (13 findings across 5 lenses: Repository Intelligence, Analytics Readiness, AI/ML Readiness, Scalability, Simplicity)
**Applies to:** `DATABASE_DESIGN.md`, `ENTITY_RELATIONSHIP_MODEL.md`, `QUERY_DRIVEN_SCHEMA_DESIGN.md`, `MIGRATION_STRATEGY.md`
**Principle followed:** implement every High-severity finding; implement Medium-severity findings only where they improve correctness without adding unjustified complexity; document (not silently drop) every deferred decision; remove unjustified GitHub-mirror fields. No unrelated enhancements introduced.

---

## 1. Change Log (One Entry Per Finding)

### Finding 1 — Unjustified GitHub-mirror columns

- **Modification:** Removed `default_branch`, `homepage_url`, `is_template` from `repositories`.
- **Rationale:** None of the three appear in any of the 83 queries evaluated in `QUERY_DRIVEN_SCHEMA_DESIGN.md` Phase 2. They existed only because GitHub's API returns them — the exact mirroring anti-pattern the review's Lens 1 (Repository Intelligence) was designed to catch.
- **Affected entities:** `repositories` (schema), Repository (conceptual entity, `DATABASE_DESIGN.md` §2.1).
- **Affected queries:** None (confirmed no regression — these columns supported zero named queries).
- **Future implications:** If a future query genuinely needs one of these fields, it can be added back with a single low-cost `ALTER TABLE ADD COLUMN` — `repositories` is not a high-volume table. They must not be restored speculatively.

### Finding 2 — `size_kb` duplicated across two tables

- **Modification:** Removed `size_kb` from `repositories`; retained (and confirmed as sole authority) on `repository_snapshot`.
- **Rationale:** Two columns claiming to be authoritative for the same fact invites drift. `repository_snapshot` is the correct home — it is exactly the table designed to hold cheap, time-varying GitHub counters.
- **Affected entities:** `repositories`, `repository_snapshot` (schema); Repository, Repository Snapshot (conceptual entities, `DATABASE_DESIGN.md` §2.1, §2.9).
- **Affected queries:** None directly (no query referenced `repositories.size_kb` specifically); indirectly clarifies which column any future size-related query should target.
- **Future implications:** None — this was a pure de-duplication with no loss of capability.

### Finding 3 — `watchers_count` is a near-duplicate signal

- **Modification:** None (column retained).
- **Rationale:** GitHub's UI merged "watching" into starring years ago; the field is still API-exposed but not analytically independent from `stars_count`. Removal cost (losing a free, already-collected field) exceeds the near-zero storage cost of keeping it.
- **Affected entities:** `repository_snapshot` (schema); Repository Snapshot (conceptual entity, `DATABASE_DESIGN.md` §2.9).
- **Affected queries:** None.
- **Future implications:** Documented explicitly in both `DATABASE_DESIGN.md` and `QUERY_DRIVEN_SCHEMA_DESIGN.md` so no future scoring formula accidentally treats `watchers_count` as an independent popularity signal from `stars_count`.

### Finding 4 — No repository-lifecycle "gone from GitHub" state (**High**)

- **Modification:** Added `repositories.is_accessible` (boolean, default true) and `repositories.inaccessible_since` (nullable timestamptz).
- **Rationale:** `DATABASE_DESIGN.md` §2.1 explicitly promised that a repository "that disappears from GitHub (deleted, made private) is marked, not removed" — but no column previously existed to fulfill that promise. `is_archived` is a maintainer action on a still-existing repository; `last_extraction_status` tracks pipeline-side success/failure, not GitHub-side existence. Without this fix, a vanished repository would be silently misclassified by any query using those other two fields as proxies.
- **Affected entities:** `repositories` (schema); Repository (conceptual entity, `DATABASE_DESIGN.md` §2.1); referential integrity table in `ENTITY_RELATIONSHIP_MODEL.md` §4.
- **Affected queries:** Category F (community/maintenance health — queries 47, 51, 54: "actively maintained," "classify by maintenance status," "filter to actively maintained only") and Category I (queries 69-70: staleness/retry logic) now correctly distinguish "extraction failed, retry" from "confirmed gone, stop retrying."
- **Future implications:** Establishes the pattern that GitHub-side existence state is distinct from both maintainer-side state (`is_archived`) and pipeline-side state (`last_extraction_status`) — a future entity (e.g., a full Contributor model) should follow the same three-way distinction if it needs lifecycle tracking.

### Finding 5 — Snapshot cadence assumed daily without query justification (Medium)

- **Modification:** Changed the documented default cadence for `repository_snapshot` from "potentially daily / every API touch" to weekly, matching `repository_metrics`.
- **Rationale:** No query in the 83-query workload requires sub-weekly resolution for star velocity or growth-trend analysis at V1/V2 scale. A needlessly high cadence directly worsens the table's row-count growth (and therefore its partitioning urgency, Finding 9) with no corresponding query benefit — this is the project's own "earn every feature" discipline applied to cadence rather than columns.
- **Affected entities:** `repository_snapshot` (schema and conceptual entity, `DATABASE_DESIGN.md` §2.9).
- **Affected queries:** Category E/H (trend queries 44-45, 63-64) — unaffected in capability, since none require sub-weekly granularity; only the table's growth rate changes.
- **Future implications:** If a future capability (e.g., detecting a viral spike within days) needs finer granularity, it should be added deliberately for that stated purpose, not assumed by default. This is a documentation/policy change, not a schema change — no column was added or removed.

### Finding 6 — No removal timestamp on technology associations (Medium)

- **Modification:** Added `repository_technologies.removed_at` (nullable timestamptz), set (not deleted) when a recomputation pass no longer detects a previously-confirmed association.
- **Rationale:** `first_detected_at` alone only records when an association started, never when it ended. Without a removal marker, a technology adopted broadly and later abandoned across many repositories would show inflated, indefinitely-growing "adoption" counts in trend queries — a real correctness gap directly relevant to a named query category, not a hypothetical.
- **Affected entities:** `repository_technologies` (schema); Repository↔Technology Association (conceptual entity, `DATABASE_DESIGN.md` §2.6); cardinality rationale in `ENTITY_RELATIONSHIP_MODEL.md` §3.4.
- **Affected queries:** Category E (queries 38-40: technology adoption rate, emerging/declining technologies) — now correctly excludes abandoned associations from "currently adopted" counts via a new partial index (`WHERE removed_at IS NULL`).
- **Future implications:** The relationship remains current-state-shaped (one row per pair, never duplicated) — this is an additive correctness fix, not a shift toward full history. If true point-in-time technology reconstruction is later required (Finding tied to Phase 2 Query 68), it remains a separate, larger, and still-deferred decision.

### Finding 7 — Embedding vector dimension contradicts recommended model (**High**)

- **Modification:** Changed `repository_embeddings.embedding` from `vector(1536)` to `vector(768)`, and documented an explicit single-model V1 assumption.
- **Rationale:** `pgvector` requires a fixed vector width per column. The original 1536-dimension typing matched OpenAI's `text-embedding-ada-002`, while the project's own chosen V1 tooling (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §5-6, Hugging Face Sentence Transformers) does not produce 1536-dimensional output. As originally written, the schema would have rejected the recommended model's output on first use — a genuine defect, not a style nitpick.
- **Affected entities:** `repository_embeddings` (schema); Repository Embedding (conceptual entity, `DATABASE_DESIGN.md` §2.11); `ENTITY_RELATIONSHIP_MODEL.md` §3.7.
- **Affected queries:** Category G (similarity/recommendations, queries 55-62) and the semantic-search half of Milestone 4.2 — both depend on embeddings existing and being comparable, which required this fix to function at all with the recommended model.
- **Future implications:** A second embedding model with a *different* dimensionality (e.g., a future switch to a 1536-dim model) will require either a new table or a column-width migration — explicitly out of V1 scope and not solved speculatively now. This is a deliberate YAGNI boundary, documented so it isn't mistaken for an oversight later.

### Finding 8 — Normalized/scaled ML feature-vector storage undecided (Low)

- **Modification:** None (no schema change).
- **Rationale:** `IMPLEMENTATION_ROADMAP.md` Checkpoint 2.6 names normalized-feature storage as a deliverable, but no query in the current 83-query workload requires persisted normalized vectors over computed-on-read normalization. Adding a table now would be speculative.
- **Affected entities:** None directly; documented against Repository Metrics (`DATABASE_DESIGN.md` §2.8, new §6 in that document).
- **Affected queries:** None currently.
- **Future implications:** Explicitly left as an open decision for Checkpoint 2.6's implementer (materialized view vs. new table vs. computed-on-read) — recorded so it is a deliberate deferral, not a silently dropped requirement.

### Finding 9 — Partitioning key not committed given stated scale (**High**)

- **Modification:** Declared (not implemented) future partition keys for the four highest-volume tables: `repository_metrics` and `repository_snapshot` → `snapshot_date` (RANGE); `repository_scores` → `computed_at` (RANGE); `repository_dependencies` → `repository_id` (HASH) or `ecosystem` (LIST). Consolidated into a new `QUERY_DRIVEN_SCHEMA_DESIGN.md` §16.
- **Rationale:** At the review's assumed scale (1M repositories, 100M+ snapshot rows, billions of feature/dependency records), these four tables cross partitioning-worthy row counts well within the platform's own documented growth path — not at some indefinite future point. Retrofitting partitioning onto an already-large, unpartitioned production table is a genuinely disruptive operation; committing the key now costs nothing and preserves a non-disruptive conversion path later.
- **Affected entities:** `repository_metrics`, `repository_snapshot`, `repository_scores`, `repository_dependencies` (schema); corresponding conceptual entities in `DATABASE_DESIGN.md` (new "Partitioning posture" notes in §2.7-§2.10); migrations `004`-`007` in `MIGRATION_STRATEGY.md`.
- **Affected queries:** No query behavior changes — this is purely a storage/operational decision, invisible to the query layer.
- **Future implications:** Physical `PARTITION BY` implementation, partition-maintenance tooling (e.g., `pg_partman`), and exact boundary sizing (monthly vs. quarterly) remain explicitly deferred to a coordinated Version 2/3 migration across all four tables, triggered when any one approaches 10-50M actual rows — not the 5-year projection. This is recorded as a carried-forward action item in `MIGRATION_STRATEGY.md` §14.

### Finding 10 — `ON DELETE CASCADE` blast radius at scale (Medium)

- **Modification:** None to constraints (CASCADE retained as correct for data integrity); added explicit operational documentation.
- **Rationale:** At scale, a single hard `DELETE FROM repositories` could cascade into hundreds of millions of child-table deletes in one transaction. With Finding 4's soft-delete columns now available, hard deletion should never be a routine pipeline action.
- **Affected entities:** All tables with `ON DELETE CASCADE` referencing `repositories.id`; documented in `ENTITY_RELATIONSHIP_MODEL.md` §4 and `DATABASE_DESIGN.md` §6.
- **Affected queries:** None (this is a write-path/operational concern, not a read-query concern).
- **Future implications:** Any future admin tooling for repository deletion should batch child-table cleanup before the parent delete, or rely on `is_accessible` instead of hard deletion entirely.

### Finding 11 — Columnar-migration should be a committed milestone, not open-ended (Medium)

- **Modification:** None to schema; added implementation guidance.
- **Rationale:** `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2 already designates ClickHouse as the correct long-term engine for large-scale time-series analytics. The review's contribution is process guidance, not a schema change: the data-access layer should be built behind a repository/DAO abstraction from the start of implementation, so that migration doesn't require rewriting every call site later.
- **Affected entities:** None (schema-neutral); documented in `DATABASE_DESIGN.md` §6 and `MIGRATION_STRATEGY.md` §14 as implementation guidance for Checkpoint 0.2's coding phase.
- **Affected queries:** None.
- **Future implications:** Carried forward as an action item, not a blocking requirement — this affects how Checkpoint 0.2's implementation is coded, not what tables exist.

### Finding 12 — `first_seen_at` / `created_at` redundant (Low)

- **Modification:** Removed `first_seen_at` from `repositories`; `created_at` retains this meaning.
- **Rationale:** Both represented the same instant under this schema's existing convention (identity-table timestamps never diverge from first-discovery time, as already true for `owners`, `technologies`, etc.). No query treated them differently.
- **Affected entities:** `repositories` (schema); Repository (conceptual entity, `DATABASE_DESIGN.md` §2.1).
- **Affected queries:** None.
- **Future implications:** None — pure simplification, no capability lost.

### Finding 13 — Restates Finding 1

- **Modification:** Same as Finding 1 (both a Simplicity and an Intelligence-lens instance of the same three-column removal).
- **Rationale, entities, queries, implications:** See Finding 1.

---

## 2. Summary of Net Changes by Document

| Document | Additions | Removals | Documentation-only changes |
|---|---|---|---|
| `DATABASE_DESIGN.md` | Lifecycle notes (§2.1), removal-tracking notes (§2.6), partitioning-posture notes (§2.7-§2.10), fixed-dimension note (§2.11), new §6 (reaffirmed-without-change findings) | Narrative references to removed columns | `watchers_count` non-independence note |
| `ENTITY_RELATIONSHIP_MODEL.md` | `is_accessible`, `inaccessible_since`, `removed_at` in diagrams; partition-key annotations; new §6 revision note | `first_seen_at` from diagram; `default_branch`/`homepage_url`/`is_template`/`size_kb` (already absent from the simplified diagram) | CASCADE operational note in §4 |
| `QUERY_DRIVEN_SCHEMA_DESIGN.md` | `is_accessible`, `inaccessible_since` columns + partial index; `removed_at` column + partial index; new §16 (Partitioning Strategy); embedding column retyped | `default_branch`, `homepage_url`, `is_template`, `size_kb`, `first_seen_at` from `repositories` table definition | Cadence correction in §14.10; single-model assumption in §14.12 |
| `MIGRATION_STRATEGY.md` | Partition-key notes in migrations `004`-`007`; column-set revisions in `002`/`003`/`008`; new §13 (Resolution addendum) | — (migration *sequence* unchanged; only table contents referenced by migrations changed) | Updated §14 Overall Verdict |

**No migration was added, removed, or reordered.** The 9-migration sequence (`001` through `009`) from the original `MIGRATION_STRATEGY.md` remains structurally valid — every finding's resolution is a change to what a given migration's target table contains, not to the sequencing or dependency graph itself.

---

## 3. Final Consistency Validation

Cross-checked all four documents against each other, field by field, for the tables affected by this revision.

### 3.1 `repositories`

| Column | `DATABASE_DESIGN.md` §2.1 | `ENTITY_RELATIONSHIP_MODEL.md` diagram | `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.5 | `MIGRATION_STRATEGY.md` `002` |
|---|---|---|---|---|
| `is_accessible` | ✅ described | ✅ shown | ✅ typed, indexed | ✅ referenced |
| `inaccessible_since` | ✅ described | ✅ shown | ✅ typed | ✅ referenced |
| `default_branch` | ✅ absent (removal noted) | ✅ absent | ✅ absent | ✅ absent (removal noted) |
| `homepage_url` | ✅ absent (removal noted) | ✅ absent | ✅ absent | ✅ absent (removal noted) |
| `is_template` | ✅ absent (removal noted) | ✅ absent | ✅ absent | ✅ absent (removal noted) |
| `size_kb` | ✅ absent (removal noted) | ✅ absent (never shown here) | ✅ absent | ✅ absent (removal noted) |
| `first_seen_at` | ✅ absent (removal noted) | ✅ absent | ✅ absent | ✅ absent (removal noted) |
| `fetched_at` | ✅ present (doubles as discovery time) | ✅ shown | ✅ typed | ✅ unchanged |

**Consistent.**

### 3.2 `repository_technologies`

| Column | Design | ER | Schema | Migration |
|---|---|---|---|---|
| `removed_at` | ✅ described (§2.6) | ✅ shown | ✅ typed, partial index added | ✅ referenced (`003`) |

**Consistent.**

### 3.3 `repository_snapshot`

| Property | Design | ER | Schema | Migration |
|---|---|---|---|---|
| `size_kb` sole authority | ✅ stated | ✅ (implicit, not previously conflicting) | ✅ retained | ✅ referenced (`005`) |
| Weekly default cadence | ✅ stated | ✅ noted in §3.6 | ✅ stated in §14.10 | ✅ referenced (`005`) |
| Declared partition key `snapshot_date` | ✅ stated | ✅ noted | ✅ stated, in §16 table | ✅ referenced (`005`) |

**Consistent.**

### 3.4 `repository_metrics` / `repository_scores` partition keys

| Table | Key | Design | ER | Schema §16 | Migration |
|---|---|---|---|---|---|
| `repository_metrics` | `snapshot_date` | ✅ | ✅ | ✅ | ✅ (`006`) |
| `repository_scores` | `computed_at` | ✅ | ✅ | ✅ | ✅ (`007`) |
| `repository_dependencies` | `repository_id`/`ecosystem` | ✅ | — (not diagrammed in ER, correctly out of visual scope) | ✅ | ✅ (`004`) |

**Consistent.** (`repository_dependencies`'s partition key is not shown in the ER diagram because the ER diagram does not visualize storage/partitioning properties for any table — this is intentional and matches the ER document's own stated scope in its §5, "What This Model Deliberately Does Not Decide Yet.")

### 3.5 `repository_embeddings`

| Property | Design | ER | Schema | Migration |
|---|---|---|---|---|
| `vector(768)`, not `vector(1536)` | ✅ stated with rationale | ✅ shown as `vector_768` | ✅ typed `vector(768)` | ✅ referenced (`008`) |
| Single-model V1 assumption | ✅ stated | ✅ noted in §3.7 | ✅ stated | ✅ referenced |

**Consistent.**

### 3.6 Cross-cutting checks

- **Entity count:** All four documents still describe exactly **12 core entities / tables** and **6 deferred entities**. No document drifted from this count during revision.
- **Migration count:** All four documents still describe exactly **9 migrations** (`001`-`009`), in the same order, with the same dependency graph. No renumbering occurred.
- **JSONB column count:** `repository_metrics.loc_by_language`, `repository_metrics.extended_features`, `repository_scores.score_breakdown` — still exactly **3 justified JSONB columns**, unchanged by this revision (no finding touched JSONB usage).
- **PK strategy:** Still exactly **2 strategies** (UUID for `owners`/`repositories`/`licenses`/`topics`/`technologies`; BIGINT identity for the four high-volume tables) plus composite natural keys for join tables — unchanged by this revision.
- **Finding traceability:** Every one of the 13 findings in `DATABASE_ARCHITECT_REVIEW.md` §6 (consolidated findings table) has a corresponding entry in this changelog's §1, and every changelog entry is reflected in all four design documents where relevant (verified above).

**No inconsistencies found.**

---

## 4. Design Freeze Declaration

All required High-severity findings (4, 7, 9) are resolved. All Medium-severity findings judged to improve correctness (5, 6) are resolved; those judged to be operational/implementation guidance rather than schema concerns (10, 11) are documented without adding schema complexity, per instruction. All Low-severity findings (1, 2, 12, 13) are resolved by removing unjustified GitHub-mirror and duplicate fields. Findings 3 and 8 are deliberately retained/deferred with explicit documentation, not silently dropped.

The four design documents — `DATABASE_DESIGN.md`, `ENTITY_RELATIONSHIP_MODEL.md`, `QUERY_DRIVEN_SCHEMA_DESIGN.md`, `MIGRATION_STRATEGY.md` — are verified consistent with each other at the entity, relationship, column, index, and migration level, per the validation in §3 above.

**The database design for Checkpoint 0.2 is hereby declared FROZEN.**

This frozen design — 12 core entities, 9 migrations, 3 justified JSONB columns, 2 PK strategies, declared (not yet implemented) partitioning keys on the four highest-volume tables, and a repository-lifecycle model that correctly distinguishes GitHub-side existence from maintainer-side and pipeline-side state — becomes the source of truth for Alembic migration implementation in the next checkpoint.

No further design changes should be made without a new review cycle. Implementation (Alembic migrations `001` through `009`, per `MIGRATION_STRATEGY.md`) may now begin.
