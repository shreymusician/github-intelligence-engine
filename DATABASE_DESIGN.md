# Database Design — Conceptual Model

**Checkpoint:** 0.2 (Phase 1 — Conceptual Data Modeling)
**Status:** Frozen — incorporates `DATABASE_ARCHITECT_REVIEW.md` findings. See `DATABASE_DESIGN_CHANGELOG.md` for the full list of changes made in this revision.
**Scope:** Domain entities, responsibilities, relationships, lifecycle — no SQL

---

## 1. Design Philosophy

This document identifies *what exists in the domain* before deciding *how PostgreSQL should store it*. Three constraints from the frozen architecture documents shape every decision here:

1. **Intelligence-first, UI-last** (`SYSTEM_ARCHITECTURE.md`) — the schema must support feature engineering, scoring, embeddings, and search before it supports any user-facing convenience.
2. **Version 1 excludes user identity, collaborative filtering, and a graph database** (`SYSTEM_ARCHITECTURE.md` §8) — entities like User, Bookmark, and Concept/Pattern are modeled conceptually here (for future extensibility) but are **not** part of the Checkpoint 0.2 logical schema.
3. **Aggregate features over raw event logs in PostgreSQL** — commit-level, issue-level, and PR-level data are explicitly deferred to ClickHouse in Version 2+ (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §2). PostgreSQL in V1 stores repositories and *computed* metrics about them, not raw event streams.

Every entity below is classified as **Core (V1)**, **Deferred (documented, not implemented)**, or **Future (V2+, different storage engine)**.

---

## 2. Core Entities (Implemented in Checkpoint 0.2)

### 2.1 Repository

**Responsibility:** The central entity of the entire platform. Represents one GitHub repository at its current known state.

**Identity:** A repository is uniquely identified by GitHub's immutable numeric ID (survives renames/transfers) and, secondarily, by `owner/name` (human-facing, mutable).

**Lifecycle:**
- Created when first discovered by the acquisition pipeline (Milestone 1)
- Updated on every refresh cycle (weekly batch, per `SYSTEM_ARCHITECTURE.md` §3 Stage 1)
- Never hard-deleted — a repository that disappears from GitHub (deleted, made private) is marked, not removed, preserving historical intelligence and any recommendations that reference it. **This is now realized explicitly, not just promised in narrative:** `is_accessible` and `inaccessible_since` (added per `DATABASE_ARCHITECT_REVIEW.md` Finding 4) give the acquisition pipeline a distinct place to record "confirmed gone from GitHub" separately from `is_archived` (a maintainer's action on a repository that still exists and is fetchable) and separately from `last_extraction_status` (our own pipeline's transient success/failure, not GitHub-side state).
- Archived/fork status tracked as attributes, not separate entities (they are states, not kinds)

**Ownership:** Owned by exactly one `Owner` (GitHub's model — repos are never co-owned at the platform level; collaborators are a GitHub concept we don't model in V1).

**Future extensibility:** Must support many one-to-many relationships (technologies, metrics, scores, embeddings) without schema changes — achieved by keeping Repository itself narrow (identity + slowly-changing metadata) and pushing everything computed into satellite tables.

**Scope discipline (revised):** `default_branch`, `homepage_url`, `is_template`, and a duplicate `size_kb` column were removed from this entity per `DATABASE_ARCHITECT_REVIEW.md` Findings 1 and 2 — none were justified by any query in `QUERY_DRIVEN_SCHEMA_DESIGN.md`, and `size_kb` already has a correct home in Repository Snapshot (§2.9). `first_seen_at` was also removed (Finding 12) as a pure duplicate of `created_at` under this schema's existing convention that identity-table timestamps never diverge from first-discovery time.

---

### 2.2 Owner

**Responsibility:** Represents the GitHub account (User or Organization) that owns a repository.

**Why a separate entity and not a text column on Repository:** Owners recur across many repositories (an organization like `facebook` owns hundreds). Normalizing avoids redundant storage and enables owner-level queries ("show all repositories by corporate-backed organizations") without scanning all repositories.

**Lifecycle:** Created on first encounter (via any repository referencing it). Updated opportunistically when repository data is refreshed. Never deleted.

**Relationships:** One Owner → many Repositories.

**Deferred:** Individual GitHub Users who are *not* repository owners (i.e., contributors, issue commenters) are not modeled as Owners. See §3.1 Contributor.

---

### 2.3 License

**Responsibility:** Normalizes SPDX license identifiers (MIT, Apache-2.0, GPL-3.0, etc.) so licensing can be filtered and analyzed without string-matching free text.

**Lifecycle:** Effectively a reference/lookup table. Populated once from SPDX's published list; rarely changes. A repository's license can change over time (relicensing events are real but rare) — modeled as a mutable FK on Repository, not a historical log, since license history is out of scope for V1.

**Relationships:** One License → many Repositories. A repository has zero or one license (no license present is valid and meaningful — "no license" is a data point, not missing data).

---

### 2.4 Topic

**Responsibility:** GitHub's own tagging system (repository "topics," e.g., `machine-learning`, `react`, `cli-tool`). These are user/maintainer-assigned labels, distinct from technologies we *infer*.

**Why separate from Technology:** A topic is a social/curatorial signal chosen by maintainers (marketing, categorization intent). A technology is an *engineering fact* we detect from manifests. Conflating them would lose the distinction between "what the maintainer says this is" and "what we observed this to be" — both are valuable signals for different queries (§4 Query-Driven Design).

**Lifecycle:** Reference table, grows as new topic strings are encountered. Many-to-many with Repository.

---

### 2.5 Technology

**Responsibility:** A first-class entity for any language, framework, database, testing tool, or DevOps tool a repository uses — mirrors `REPOSITORY_INTELLIGENCE_ENGINE.md` §6's "Technology as First-Class Entity."

**Why first-class (not a free-text column):** The vision documents explicitly call for technology embeddings, co-occurrence analysis, and "which technologies work well together" — none of these are possible if technology is a string on a JSON blob. A first-class entity allows technologies to eventually have their own metadata (maturity, adoption rate) and relationships to each other, without touching the Repository schema.

**Categorization:** Every technology has a *category* (language, framework, database, testing-tool, devops-tool, platform) so queries can distinguish "primary language" from "uses Docker."

**Lifecycle:** Grows as detection pipelines (Milestone 2.2) encounter new technologies. Deduplication matters here (React vs react vs ReactJS must resolve to one row) — this is a data-quality responsibility of the Feature Engineering module, not the schema, but the schema must support a canonical `slug`.

**Relationships:** Many-to-many with Repository, carrying relationship metadata (role, confidence, detection method) — this makes the join itself an entity with responsibility (see §2.6).

---

### 2.6 Repository↔Technology Association

**Responsibility:** Captures *how* a repository relates to a technology — not just "uses React" but "uses React as a primary framework, detected via package.json, high confidence."

**Why this needs to be more than a plain join table:** `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 distinguishes detection methods (package manifest, Dockerfile, CI config, linguist/LOC analysis) with varying reliability. A quality-scoring or filtering query needs to know whether "uses PostgreSQL" was inferred from a `docker-compose.yml` mention (weaker signal) versus an actual driver dependency in `requirements.txt` (stronger signal).

**Lifecycle:** Recomputed on each feature-engineering pass (Milestone 2.2). **Revised per `DATABASE_ARCHITECT_REVIEW.md` Finding 6:** associations are no longer silently overwritten when a technology is dropped — a nullable `removed_at` timestamp is set (not deleted) when a recomputation pass no longer detects a previously-confirmed association. This closes a real correctness gap: without it, a technology adopted broadly and later abandoned across many repositories would show inflated, indefinitely-growing "adoption" counts in trend queries (Category E), since `first_detected_at` alone only records when an association started, never when it ended. The row is still current-state-plus-history-markers, not a full history table — a repository cannot have two rows for the same technology, preserving the table's query performance.

---

### 2.7 Repository Dependency

**Responsibility:** Fine-grained package-level dependency data — exact package name, ecosystem (npm/PyPI/crates.io/etc.), version constraints, staleness, and vulnerability counts. This is deliberately distinct from Technology (§2.5).

**Why separate from Repository↔Technology:** Technology answers "what does this repo use, broadly" (curated, deduplicated, ~hundreds of distinct values across the whole corpus — suitable for filtering/faceting). Dependency answers "what exact packages, at what versions, how old, how risky" (raw, high-cardinality, package-registry-derived — suitable for `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5's dependency health features). A repository might have 200 npm dependencies but only 5-10 first-class Technologies worth surfacing in search filters. Merging these would either pollute the curated Technology taxonomy with noise or lose per-package granularity needed for vulnerability/staleness scoring.

**Lifecycle:** Recomputed per feature-engineering pass (Milestone 2.5). High row count relative to other tables (avg. tens of dependencies per repository) — a volume/growth consideration addressed in §5 (Phase 3 logical design).

**Partitioning posture (per `DATABASE_ARCHITECT_REVIEW.md` Finding 9):** This is the schema's highest-cardinality table by repository-count growth alone (not by time), unlike the append-only time-series tables below. Its declared future partition key is therefore structural — `repository_id` (hash) or `ecosystem` (list) — not a date range. This distinction is carried into `QUERY_DRIVEN_SCHEMA_DESIGN.md` §17.

---

### 2.8 Repository Metrics (Feature Store)

**Responsibility:** The computed, engineered features described exhaustively in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 and `REPOSITORY_INTELLIGENCE_ENGINE.md` §2 — activity metrics, documentation quality, code size, dependency health rollups. This is the platform's **feature store** (§13 of the research document explicitly names this pattern).

**Why a distinct entity from Repository:** Repository is *identity and slowly-changing metadata* (name, owner, license). Metrics are *computed, frequently-refreshed, and versioned by time* (commits in the last 3 months changes weekly). Mixing them would mean re-writing repository identity rows every time a metric is recomputed — a normalization violation and a write-amplification problem.

**Temporal nature:** Metrics are captured as **snapshots** — one row per repository per computation date — not overwritten in place. This directly supports:
- `REPOSITORY_INTELLIGENCE_ENGINE.md` §10 (Time Series Analysis & Trend Discovery — "is quality improving or declining")
- `TECHNICAL_IMPLEMENTATION_IDEAS.md` §13 (Feature Store's requirement for "point-in-time correctness")

**Lifecycle:** Append-only. A new snapshot is inserted on each feature-engineering run (weekly, per the architecture's update cadence); nothing is ever updated in place. This is what enables trend queries without a separate historical table.

**Partitioning posture (per `DATABASE_ARCHITECT_REVIEW.md` Finding 9):** Declared future partition key is `snapshot_date` (RANGE, monthly or quarterly). At the review's assumed scale (1M repositories, weekly cadence), this table crosses tens of millions of rows within a single year — the partition key is fixed now in the logical schema (`QUERY_DRIVEN_SCHEMA_DESIGN.md` §17) so that physical partitioning can be introduced later without a disruptive retrofit, even though the Checkpoint 0.2 migration creates it as an ordinary (unpartitioned) table.

---

### 2.9 Repository Snapshot (Raw Metadata History)

**Responsibility:** Distinct from Metrics (§2.8) — this captures the *raw, cheap-to-fetch* GitHub counters (stars, forks, watchers, open issues) at each observation point, independent of the more expensive computed features.

**Why separate from Repository Metrics:** Cost and cadence differ. Star/fork counts come free with every API call and can be captured on every touch of a repository (even a cache refresh). Computed metrics (documentation readability, dependency health) are expensive and computed on a slower cadence (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §4's tiered analysis strategy). Conflating them would force expensive recomputation just to log a star-count delta, or would starve star-velocity tracking of the granularity it needs.

**Lifecycle:** Append-only, one row per repository per observation. This is the source of truth for "star velocity" (stars/month) and other freshness-signal calculations (`REPOSITORY_INTELLIGENCE_ENGINE.md` §2).

**Cadence (revised per `DATABASE_ARCHITECT_REVIEW.md` Finding 5):** Default cadence is **weekly**, matching Repository Metrics — not the higher (potentially daily) frequency originally speculated. No query in `QUERY_DRIVEN_SCHEMA_DESIGN.md`'s 83-query workload requires sub-weekly resolution for star velocity or growth-trend analysis at V1/V2 scale, and a higher default cadence would directly inflate this table's row count (and therefore its partitioning urgency, §Partitioning posture below) without a named analytical justification. If a future capability (e.g., detecting a viral spike within days) needs finer granularity, it should be added deliberately for that purpose, not assumed now.

**Partitioning posture (per `DATABASE_ARCHITECT_REVIEW.md` Finding 9):** Declared future partition key is `snapshot_date` (RANGE), same rationale as Repository Metrics above — this is, along with Repository Metrics, the table most exposed by the review's assumed scale (100M snapshot rows).

**Duplicate field removed:** `size_kb` was previously (incorrectly) also present on `repositories`; this table is now its sole, authoritative home (`DATABASE_ARCHITECT_REVIEW.md` Finding 2).

---

### 2.10 Repository Score (Intelligence Output)

**Responsibility:** Stores the *outputs* of intelligence generation (Milestone 3) — difficulty score, quality score, community-health classification — as described in `SYSTEM_ARCHITECTURE.md` Module 4.

**Why versioned, not a column on Repository:** Scoring algorithms evolve (`IMPLEMENTATION_ROADMAP.md` Checkpoint 3.1-3.2 explicitly plans heuristic-first, ML-later scoring). If quality score were a single column, changing the algorithm would silently rewrite history and make it impossible to answer "did this repository's quality score go up because it improved, or because we changed the formula?" Every score is stamped with an `algorithm_version`, enabling side-by-side comparison and safe rollback — matching the ADR-003 philosophy already established in Checkpoint 0.1 (versioned, auditable change).

**Lifecycle:** Append-only, like Metrics. A "current" score is simply the most recent row for a given `(repository, score_type)`.

**Partitioning posture (per `DATABASE_ARCHITECT_REVIEW.md` Finding 9):** Declared future partition key is `computed_at` (RANGE), for the same reasons as Repository Metrics and Repository Snapshot.

---

### 2.11 Repository Embedding

**Responsibility:** Stores the dense vector representation of a repository, generated from README + technology stack + metadata (`REPOSITORY_INTELLIGENCE_ENGINE.md` §3, `IMPLEMENTATION_ROADMAP.md` Checkpoint 3.4). Enables semantic search and similarity computation.

**Why model-scoped, not a single vector column:** `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 anticipates multiple embedding strategies (documentation embeddings now, code-snapshot or custom-trained embeddings later). An embedding is meaningless without knowing which model produced it — a cosine-similarity comparison between vectors from two different models is not valid. Every embedding row is scoped to `(repository, model_name)`.

**Lifecycle:** Recomputed when the source text changes meaningfully, or when a new/upgraded embedding model is adopted. A content hash of the source text prevents wasteful recomputation when nothing changed.

**Fixed-dimension constraint, made explicit (per `DATABASE_ARCHITECT_REVIEW.md` Finding 7):** `pgvector` requires a fixed vector width per column. The original schema typed this column at 1536 dimensions (OpenAI's `text-embedding-ada-002` size) while `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5-6 recommends Hugging Face Sentence Transformers for V1 — a model family that does not produce 1536-dimensional output. This was a genuine defect: the model-scoped `(repository_id, model_name)` key implies multiple models can coexist, but a single fixed-width column cannot actually hold output from models of differing dimensionality. **Resolution for V1:** one embedding model and its exact output dimensionality is committed to now — **768 dimensions** (matching a standard Sentence Transformers model such as `all-mpnet-base-v2`) — and the column is typed accordingly in `QUERY_DRIVEN_SCHEMA_DESIGN.md` §14.12. Supporting a second model with a *different* dimensionality later is explicitly out of V1 scope (would require a new table or a schema change) and is not solved speculatively here — this is a deliberate YAGNI call, made explicit rather than accidentally contradicted.

---

### 2.12 Repository Similarity

**Responsibility:** Precomputed "top-K most similar repositories" pairs, powering `SYSTEM_ARCHITECTURE.md` Module 7 (Recommendations) and `IMPLEMENTATION_ROADMAP.md` Checkpoint 3.3/5.1.

**Why precomputed top-K, not a full N×N matrix table:** At V1 scale (100-1,000 repositories) a full matrix is trivial, but the schema is designed not to paint itself into a corner — `SYSTEM_ARCHITECTURE.md` §9 explicitly plans growth to 10K, 100K, and beyond. An N² relationship table becomes untenable past ~50K repositories (2.5 billion pairs). Storing only the top-K (e.g., top 20) per repository per method keeps the table's growth linear in repository count, which is the only viable long-term shape. This is a deliberate scalability decision, not a V1 shortcut.

**Multiple methods:** Similarity can be computed via embedding cosine distance, engineered-feature distance, or a hybrid — each is a distinct, independently queryable method, per `TECHNICAL_IMPLEMENTATION_IDEAS.md` §7's ranking-signal framework.

**Lifecycle:** Recomputed on each intelligence-generation cycle; old rows for a method are replaced, not accumulated (unlike Metrics/Scores, similarity has no historical-trend use case identified in the query set — see §4).

---

## 3. Entities Modeled Conceptually, Deferred from V1 Logical Schema

These exist in the domain and are acknowledged here for future extensibility, but **Phase 3 (logical schema) intentionally excludes them** per `SYSTEM_ARCHITECTURE.md` §8's explicit V1 exclusions. Documenting them now prevents Checkpoint 0.2 from accidentally painting the schema into a corner that makes adding them later painful.

### 3.1 Contributor (Deferred)

**Conceptual responsibility:** An individual GitHub user who commits to, or opens issues/PRs against, a repository. `REPOSITORY_INTELLIGENCE_ENGINE.md` §6 lists this as a primary knowledge-graph entity with its own embeddings.

**Why deferred:** V1's activity features (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §5) need only *aggregate* contributor counts ("12 unique contributors in the last 12 months"), which are stored as scalar columns in Repository Metrics (§2.8) — not as individual Contributor rows. Modeling individual contributors is required for collaborative filtering and developer-network analysis, both explicitly excluded from V1 (`SYSTEM_ARCHITECTURE.md` §8).

**Extensibility note:** When introduced, Contributor will relate to Repository via a many-to-many "contributed_to" association (mirroring the Repository↔Technology pattern in §2.6), not as a column addition to Repository.

### 3.2 Commit, Issue, Pull Request, Release (Deferred)

**Conceptual responsibility:** Raw event-level GitHub activity.

**Why deferred:** `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2 explicitly assigns commit-history and event-stream storage to ClickHouse ("optimized for analytics, not transactions... billions of rows"), not PostgreSQL. Storing raw commits in PostgreSQL contradicts the frozen storage architecture. V1's need for commit *data* is satisfied entirely by aggregate metrics in Repository Metrics (commits/month, issue resolution rate, PR merge time) computed by the Feature Engineering module directly from the GitHub API — the raw events themselves are never persisted in this schema.

**Extensibility note:** If/when a time-series store is added (`SYSTEM_ARCHITECTURE.md` §9, Version 2), these entities will live there, referencing `repository.id` as a foreign key across storage engines (application-level join, not database FK).

### 3.3 User, Bookmark, User Preference (Deferred)

**Conceptual responsibility:** Platform end-users and their saved state — explicitly listed as "future" in `claude.md` §4's use-case catalogue and excluded from V1 in `SYSTEM_ARCHITECTURE.md` §8 ("No user authentication/identity").

**Why deferred:** No authentication system exists yet, and building user-state tables ahead of an auth strategy risks designing the wrong shape. These are genuinely out of scope, not merely lower-priority.

### 3.4 Concept/Pattern, Industry/Domain, Research Area (Deferred)

**Conceptual responsibility:** Knowledge-graph nodes from `REPOSITORY_INTELLIGENCE_ENGINE.md` §6 — design patterns a repository teaches, the industry it serves, academic research it implements.

**Why deferred:** `SYSTEM_ARCHITECTURE.md` §8 explicitly excludes the knowledge graph (Neo4j or graph-modeled relationships) from V1, and Challenge 5 in the same document recommends starting with PostgreSQL relationships only for *deterministic* associations (depends_on, uses_technology) — not inferential ones like "teaches dependency injection." Modeling Concept/Pattern now, before any classification pipeline exists to populate it, would produce an empty, speculative table.

---

## 4. Relationship Summary (Cardinalities)

| Relationship | Cardinality | Notes |
|---|---|---|
| Owner → Repository | 1 → N | One owner, many repos |
| License → Repository | 1 → 0..N | License optional on Repository |
| Repository ↔ Topic | M ↔ N | Maintainer-assigned tags |
| Repository ↔ Technology | M ↔ N (with attributes) | Role, confidence, detection method on the edge |
| Repository → Repository Dependency | 1 → N | Fine-grained package data |
| Repository → Repository Metrics | 1 → N (time series) | One row per snapshot date |
| Repository → Repository Snapshot | 1 → N (time series) | One row per observation |
| Repository → Repository Score | 1 → N (time series, per score_type) | Versioned by algorithm |
| Repository → Repository Embedding | 1 → N (per model) | One live row per model |
| Repository ↔ Repository (Similarity) | M ↔ N (self-referential, top-K only) | Not a full matrix |

---

## 5. Ownership & Data Lineage

Each entity has a clear **owning process** — the module (per `SYSTEM_ARCHITECTURE.md` §2) responsible for writing it:

| Entity | Written By | Read By |
|---|---|---|
| Owner, Repository, License, Topic | Data Acquisition (Module 1) | Everything downstream |
| Technology, Repository↔Technology, Repository Dependency | Feature Engineering (Module 3) | Search, Analytics |
| Repository Metrics | Feature Engineering (Module 3) | Intelligence Generation, Analytics |
| Repository Snapshot | Data Acquisition (Module 1) | Analytics (trend charts) |
| Repository Score | Intelligence Generation (Module 4) | Search, Recommendations |
| Repository Embedding | Feature Engineering (Module 3) | Search (semantic), Recommendations |
| Repository Similarity | Intelligence Generation (Module 4) | Recommendations |

This table matters for Phase 4 (migration planning): no migration should combine tables owned by different modules into one write path, since that would create false coupling between independently-evolving pipelines.

---

## 6. Decisions Reaffirmed Without Structural Change (Architect Review Findings 3, 8, 10, 11)

Four findings from `DATABASE_ARCHITECT_REVIEW.md` were evaluated and deliberately **not** turned into schema changes, per the instruction to implement only what improves correctness without adding unneeded complexity:

- **Finding 3 (`watchers_count` is near-redundant with stars):** Column retained (removal cost exceeds its near-zero storage cost), but documented explicitly — nowhere in this schema or its downstream scoring formulas should `watchers_count` be treated as an independent popularity signal from `stars_count`.
- **Finding 8 (normalized/scaled ML feature vectors have no defined storage location):** No table added. `IMPLEMENTATION_ROADMAP.md` Checkpoint 2.6 names this as a deliverable, but no query in the current workload requires persisted normalized vectors (versus computing normalization at read-time). Explicitly deferred to that checkpoint's implementer, who should choose between a materialized view, a new table, or computed-on-read — not decided speculatively here.
- **Finding 10 (`ON DELETE CASCADE` blast radius at scale):** No constraint change — CASCADE remains correct for data integrity. Documented as an **operational** rule, not a schema rule: with `is_accessible`/`inaccessible_since` (§2.1) now available, hard-deleting a repository row should be a rare, administrative action, never a routine pipeline operation, given the potential cascade into hundreds of millions of child rows at scale.
- **Finding 11 (eventual migration of high-volume tables to a columnar store):** No schema change — this is implementation guidance (build the data-access layer behind a repository/DAO abstraction from the start) carried into `MIGRATION_STRATEGY.md`, not a database-design decision.

## 7. Summary

**12 core entities** carry Checkpoint 0.2 through the roadmap's Milestones 1-5 (acquisition through recommendations) without requiring schema changes. **6 entities** are deliberately deferred with documented extensibility paths, preventing both under-design (schema that can't grow) and over-design (empty speculative tables for capabilities with no consuming pipeline yet).

This revision incorporates every High-severity finding and the Medium/Low findings that improve correctness without adding unjustified complexity, per `DATABASE_DESIGN_CHANGELOG.md`. No entity or relationship was added or removed in this revision — only attribute-level corrections were made, confirming the original Phase 1 entity boundaries were sound.

Proceed to `QUERY_DRIVEN_SCHEMA_DESIGN.md` to validate this model against real query patterns before finalizing the logical schema.
