# Migration Strategy

**Checkpoint:** 0.2 (Phase 4 — Migration Planning, Phase 5 — Review)
**Prerequisite:** `QUERY_DRIVEN_SCHEMA_DESIGN.md` (logical schema)
**Note:** This document plans migration structure and ordering only. **No SQL or Alembic migration files are created here** — that is Checkpoint 0.2's implementation phase, which follows review and approval of this design.

---

# PHASE 4: Migration Planning

## 1. Sequencing Principles

Migrations are ordered by two constraints, in priority order:

1. **Foreign-key dependency order** — a table cannot be created before the tables it references.
2. **Module ownership boundaries** (`DATABASE_DESIGN.md` §5) — migrations should not force unrelated modules (Acquisition vs. Feature Engineering vs. Intelligence Generation) to ship schema changes together. Each migration corresponds to a bounded unit of the domain that one module owns, so future migrations authored by different roadmap checkpoints don't collide.

Every migration is additive-only (`ADR-003`'s "no ad-hoc schema changes... every change is reviewable" principle) — no migration in this plan drops or destructively alters a prior migration's tables.

---

## 2. Migration Plan

### `001_reference_tables`

**Creates:** `licenses`, `topics`, `technologies`

**Rationale for grouping:** These three are independent reference/lookup tables with no foreign keys pointing *into* the core entity graph yet — they can exist before `repositories` or `owners` do. Grouping them avoids three near-empty migrations for what is conceptually one step: "seed the taxonomies the rest of the schema will reference."

**Owning module:** Data Acquisition (licenses, topics populated during ingestion) / Feature Engineering (technologies populated during detection) — grouped here as pure schema, not data seeding; actual population happens in application code, not migration data.

**Dependencies:** None (first migration after Checkpoint 0.1's empty database).

---

### `002_core_entities`

**Creates:** `owners`, `repositories`

**Rationale:** The central entity and its required parent. Kept as its own migration — not merged with `001` — because `repositories` is the highest-traffic table in the schema and its own evolution (column additions like `fetched_at`, `last_extraction_status` discovered in Phase 2) will likely be revisited independently of the static reference tables in `001`.

**Foreign keys introduced:** `repositories.owner_id → owners.id`, `repositories.license_id → licenses.id` (requires `001` to have run first).

**Owning module:** Data Acquisition (Milestone 1).

**Dependencies:** `001_reference_tables` (for `license_id` FK).

---

### `003_repository_taxonomy_associations`

**Creates:** `repository_topics`, `repository_technologies`

**Rationale:** Both are M:N join tables connecting `repositories` to `001`'s reference tables. Grouped together because they share the same shape (associative tables between a core entity and a taxonomy table) and the same owning concern — "what is this repository tagged/built with" — even though `repository_topics` is populated by Acquisition and `repository_technologies` by Feature Engineering. Splitting them into two migrations would add process overhead without a corresponding independence benefit, since both depend on the exact same prerequisite state (`002` + `001`).

**Dependencies:** `001_reference_tables`, `002_core_entities`.

---

### `004_repository_dependencies`

**Creates:** `repository_dependencies`

**Rationale:** Kept separate from `003` despite superficial similarity (also a repository-scoped association) because it is **not** a join to a curated reference table — it's a high-cardinality, independently-owned table (§14.8 flags it as the highest-volume table in the schema and a future partitioning candidate). Isolating it means a future migration that partitions or restructures this table doesn't need to touch the taxonomy association tables in `003`.

**Owning module:** Feature Engineering (Milestone 2.5, dependency analysis).

**Dependencies:** `002_core_entities`.

---

### `005_repository_snapshot_history`

**Creates:** `repository_snapshot`

**Rationale:** Own migration because it is owned by a different module (Data Acquisition, not Feature Engineering) and has a different write cadence than the metrics/scores tables that follow. Per `DATABASE_DESIGN.md` §2.9, deliberately kept independent of `repository_metrics` to avoid coupling cheap/frequent raw-counter capture to expensive/infrequent feature computation.

**Owning module:** Data Acquisition.

**Dependencies:** `002_core_entities`.

---

### `006_feature_store`

**Creates:** `repository_metrics`

**Rationale:** This is the single largest table definition (27 columns, §14.9) and the one most likely to accumulate new columns as Milestone 2's checkpoints (2.1 Activity, 2.2 Technology, 2.3 Documentation, 2.4 Code Complexity, 2.5 Dependency) each contribute their own feature groups over time. Isolating it as its own migration means future column-addition migrations (e.g., `009_add_security_features`) have a clean, singular target.

**Owning module:** Feature Engineering (Milestone 2, all checkpoints).

**Dependencies:** `002_core_entities`.

---

### `007_intelligence_scores`

**Creates:** `repository_scores`

**Rationale:** Distinct owning module (Intelligence Generation, Milestone 3) and a distinct consumer pattern (read by Search and Recommendations, not by Feature Engineering). Depends on `006` only in the sense that scoring algorithms *use* metrics as input at the application layer — there is no database-level foreign key from `repository_scores` to `repository_metrics` (scores reference `repositories` directly), so this migration's only hard dependency is `002`.

**Owning module:** Intelligence Generation (Milestone 3.1, 3.2, 3.5).

**Dependencies:** `002_core_entities`. (Soft/application-level dependency on `006_feature_store` — not a schema dependency.)

---

### `008_embeddings`

**Creates:** `repository_embeddings`

**Rationale:** Requires the `vector` extension, already installed during Checkpoint 0.1's infrastructure setup (`init-postgres.sql`) — this migration only creates the table and column using that pre-existing extension, it does not install the extension itself (infrastructure vs. schema separation, per `ADR-003`, holds even within Checkpoint 0.2's own migrations). Isolated because it's the first table with a genuinely different storage/index type (pgvector ANN index) — keeping it separate means an eventual embedding-model migration or index-tuning migration has an isolated blast radius.

**Owning module:** Feature Engineering (Milestone 3.4).

**Dependencies:** `002_core_entities`. Assumes `vector` extension present (Checkpoint 0.1 infrastructure, not this migration's responsibility).

---

### `009_similarity`

**Creates:** `repository_similarity`

**Rationale:** Last in sequence because it is the only self-referential table and, per `DATABASE_DESIGN.md` §2.12, is *derived from* embeddings and metrics rather than an independent data source — logically the "last" piece of the intelligence pipeline to materialize. Two foreign keys both pointing back to `repositories.id` (`repository_id`, `similar_repository_id`) plus the `CHECK` constraint preventing self-similarity.

**Owning module:** Intelligence Generation (Milestone 3.3, 5.1).

**Dependencies:** `002_core_entities`. (Soft/application-level dependency on `008_embeddings` — not a schema dependency, since `similarity_method` can also be `feature_distance`, sourced from `006` instead.)

---

## 3. Sequencing Diagram

```
001_reference_tables
        │
        ▼
002_core_entities ──────────────┬──────────────┬──────────────┐
        │                       │              │              │
        ▼                       ▼              ▼              ▼
003_repository_taxonomy   004_dependencies  005_snapshot   006_feature_store
        │                                                       │
        │                                                       ▼
        │                                                007_intelligence_scores
        │                                                       │
        ▼                                                       ▼
   (no further deps)                                    008_embeddings
                                                                 │
                                                                 ▼
                                                          009_similarity
```

*(Arrows show schema-level FK dependency; `006→007` and `008→009` are drawn for logical/application-layer reading order even though no DB-level FK enforces them.)*

---

## 4. What Each Migration Does NOT Do

Per the instruction, this is a planning document — none of the migrations above are generated as executable Alembic scripts in this checkpoint. When implementation begins, each migration will additionally need (decided at implementation time, not here):

- Explicit `upgrade()` and `downgrade()` functions (Alembic requirement, already scaffolded in Checkpoint 0.1's `alembic/env.py`)
- Enum type creation statements (PostgreSQL requires `CREATE TYPE` before a column can use it — an implementation detail of migrations `002`, `004`, `007`, `009`)
- Seed-data decisions for `001` (do we ship a canonical SPDX license list in the migration, or load it via a separate script? — a Checkpoint 0.2 implementation question, not a design question)

---

# PHASE 5: Principal Architect Review

## 5. Normalization

**Assessment: Correct, not over-normalized.**

- Reference tables (`licenses`, `topics`, `technologies`) are properly extracted to avoid repeated text storage and enable filtering — textbook 3NF.
- `repository_technologies` and `repository_dependencies` are deliberately **not** merged (`DATABASE_DESIGN.md` §2.7) despite superficial overlap — this is correct normalization by *purpose*, not just by structure: they answer different questions at different cardinalities.
- JSONB is used in exactly 3 of ~90 columns, each individually justified (§14.9, §14.11) against the project's explicit anti-JSONB-overuse instruction. This was checked, not assumed.
- **Self-critique:** `repository_metrics.loc_by_language` (JSONB) could theoretically be normalized into a `repository_language_breakdown` table (`repository_id, language, loc_count, percentage`). This was considered and rejected: the query workload (Phase 2) never filters or joins on individual language breakdown — it's read as a whole for display/analytics, never queried by "find repos where Go is >20% of LOC." If that query pattern emerges later, this JSONB column is the correct place to split out a new table — a low-cost future migration, not a current mistake.

## 6. Future Scalability

**Assessment: Sound for the documented 5-year growth path, with flagged pressure points.**

- The similarity table's top-K design (§14.13) is the schema's single most important scalability decision — it converts an O(n²) problem into O(n·k), which is the difference between a schema that works at 100K repositories and one that catastrophically fails.
- `repository_dependencies`, `repository_metrics`, `repository_snapshot`, and `repository_scores` are correctly identified (§14.8-14.11) as partitioning candidates at scale. **This review's addition:** these four tables should be watched together, because they'll cross partitioning-worthy row counts (roughly 10-50M rows, per general PostgreSQL partitioning guidance) at approximately the same point in the corpus's growth curve — around the "100K repositories" mark per `SYSTEM_ARCHITECTURE.md` §9's Version 3. This means a **single coordinated partitioning migration** (by `snapshot_date`/`computed_at` range, monthly or quarterly) across all four tables should be planned as one Version 2/3 initiative, not four uncoordinated ones — flagged here explicitly so it isn't rediscovered painfully later.
- BIGINT identity PKs on these four tables (§13) were chosen specifically to keep this option open — a UUID PK would have made range-based partitioning by insertion order awkward.

## 7. Indexing

**Assessment: Every index is query-justified (§15); no speculative indexes present.**

- **Self-critique:** the plan does not yet specify `fillfactor` tuning or `autovacuum` parameters for the append-only time-series tables (14.9-14.11), which under high write volume benefit from more aggressive autovacuum settings (frequent INSERT-only tables still need vacuum for visibility-map maintenance, not bloat). This is correctly deferred to implementation/operations tuning (Checkpoint 0.2 implementation or later operational hardening), not a schema-design gap — PostgreSQL configuration is an infrastructure concern (`ADR-003`'s boundary), not a schema concern.

## 8. Maintainability

**Assessment: Strong, directly traceable to source requirements.**

- Every table's existence is justified against a specific document/section (`DATABASE_DESIGN.md` traces each entity to `REPOSITORY_INTELLIGENCE_ENGINE.md`, `TECHNICAL_IMPLEMENTATION_IDEAS.md`, or `SYSTEM_ARCHITECTURE.md`), and every index is justified against a specific numbered query in `QUERY_DRIVEN_SCHEMA_DESIGN.md`. A future engineer questioning "why does this column exist" has a direct citation, not folklore.
- The 9-migration plan maps cleanly onto module ownership (§1), meaning a developer working on Milestone 3 (Intelligence Generation) touches migrations `007`/`009` without needing to understand or risk `004`/`005` (owned by Acquisition/Feature Engineering).

## 9. Analytics Readiness

**Assessment: Good for V1/V2 scale; explicitly not the final answer at V3+.**

- Time-series tables (metrics, snapshot, scores) support the trend/analytics queries in Phase 2 Category E/H directly via PostgreSQL aggregate queries.
- **Honest limitation, stated rather than hidden:** `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2 explicitly designates ClickHouse as the correct engine for large-scale time-series analytics ("billions of rows... sub-second latency"). This schema's time-series tables in PostgreSQL are the right choice for V1-V2 (tens of thousands to low millions of rows) but are consciously **not** designed to be the platform's permanent analytics engine — that migration (PostgreSQL → PostgreSQL + ClickHouse) is anticipated in `SYSTEM_ARCHITECTURE.md` §9 Version 2 and does not require redesigning this schema, only adding a parallel store fed from the same `repositories.id` keys.

## 10. AI / ML Readiness

**Assessment: Strong.**

- `repository_metrics` provides a clean, typed feature vector directly exportable for ML training (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §6's practical ML pipeline expects exactly this shape).
- `repository_scores.score_breakdown` (JSONB) preserves component-level explainability, supporting the interpretability goals in `REPOSITORY_INTELLIGENCE_ENGINE.md` §14.
- `algorithm_version` on every score row directly enables the "compare heuristic baseline vs. trained model" workflow described in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §6 without any schema change when that transition happens.

## 11. Vector Search Readiness

**Assessment: Correctly scoped for V1, with an explicit upgrade path.**

- `pgvector`, installed in Checkpoint 0.1, is used exactly as `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2 recommends for the 1-10M embedding range.
- Model-scoping the embedding table (§14.12) means migrating to a specialized vector database (Pinecone/Weaviate, per the same document's trade-off table) later requires no schema redesign — only a new `model_name` value and a data-migration job, since the embedding table's *shape* already anticipates multiple models coexisting.

## 12. Historical Data Support

**Assessment: Strong by design, with one explicitly accepted gap.**

- Append-only time-series tables (`repository_metrics`, `repository_snapshot`, `repository_scores`) give the platform genuine historical trend capability from day one — not bolted on later.
- **Accepted gap (restated from Phase 2 Category H, Query 68):** point-in-time reconstruction of a repository's *full* technology set as of an arbitrary past date is not supported — `repository_technologies` is current-state-with-first-seen-date, not a full history. This was a deliberate cost/benefit call: full technology-association history would roughly double the write volume of the highest-join-frequency association table for a query need that appears once in 83 evaluated queries and is not required by any roadmap checkpoint through Milestone 5. If a future roadmap checkpoint requires it, the fix is additive (introduce `technology_association_history` as a new table populated going forward) — not a breaking schema change.

## 13. Overall Verdict

The schema is **approved to proceed to implementation** with the following carried-forward action items, none of which block Checkpoint 0.2:

1. When corpus approaches ~100K repositories (Version 3 per `SYSTEM_ARCHITECTURE.md` §9), execute a coordinated partitioning migration across `repository_dependencies`, `repository_metrics`, `repository_snapshot`, `repository_scores` (§6).
2. Autovacuum/fillfactor tuning for high-write time-series tables is an operations task, not a schema task (§7).
3. Point-in-time technology history is a known, accepted, and documented V1 gap (§12) — not to be "discovered" as a surprise later.
4. `repository_metrics.loc_by_language` is a normalization candidate *if and only if* a future query needs to filter/join on individual language breakdown (§5) — do not split it preemptively.

No entity, relationship, or table in this design was found to require restructuring during this review. The three refinements made during Phase 2 (§12 of `QUERY_DRIVEN_SCHEMA_DESIGN.md`) were sufficient — a sign the conceptual model in `DATABASE_DESIGN.md` was sound before logical design began.
