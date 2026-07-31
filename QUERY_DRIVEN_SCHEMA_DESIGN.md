# Query-Driven Schema Design

**Checkpoint:** 0.2 (Phase 2 — Query-Driven Design, Phase 3 — Logical Schema Design)
**Prerequisite:** `DATABASE_DESIGN.md` (conceptual model), `ENTITY_RELATIONSHIP_MODEL.md` (relationships)

---

# PHASE 2: Query-Driven Design

## 1. Method

Every query below is drawn from an explicit source in the frozen planning documents (`claude.md` §4 use cases, `REPOSITORY_INTELLIGENCE_ENGINE.md`, `TECHNICAL_IMPLEMENTATION_IDEAS.md` §7, or `IMPLEMENTATION_ROADMAP.md` success criteria) — nothing here is invented. Each query is checked against the conceptual model from Phase 1; where a query would be hard to support, the model is revisited **before** any table is finalized (per the instruction). Findings from that stress-test are folded directly into the Phase 3 logical schema below, not left as unresolved TODOs.

Queries are grouped into 10 categories, ~9-10 each, for 96 total.

---

## 2. Category A — Discovery & Search (Keyword/Structured)

1. Find repositories primarily written in Python.
2. Find repositories using FastAPI.
3. Find repositories using PostgreSQL **and** Docker (multi-technology filter).
4. Find repositories with a specific topic tag (e.g., `machine-learning`).
5. Find repositories whose name or description contains a keyword.
6. Find repositories under a specific license (e.g., MIT only).
7. Find repositories owned by a specific organization.
8. Find repositories created within a date range.
9. Find repositories with more than N stars but fewer than M (a "hidden gem" band).
10. Find repositories excluding forks and archived projects (the default "active, original work" filter).

**Model check:** All satisfied by `repositories` (owner, license, dates, archived/fork flags) joined to `repository_technologies` and `repository_topics`. Requires indexes on `primary_language`, `owner_id`, `license_id`, `is_archived`, `is_fork`, and a full-text index on `name`/`description`. ✅ No model change needed.

## 3. Category B — Learning & Difficulty

11. Find beginner-friendly repositories in a given language.
12. Find repositories appropriate for someone with intermediate skill.
13. Find the easiest repository among a set of similar projects.
14. Rank repositories by difficulty within a technology filter (Python + beginner, sorted).
15. Find repositories whose difficulty is inconsistent with their size (small LOC but rated hard — an anomaly check).
16. Find "next step up" repositories from a given one (harder, same domain).
17. Find repositories suitable for a specific estimated time investment (e.g., "weekend project").
18. Exclude repositories above a difficulty threshold from search results.
19. Find repositories where difficulty score and popularity are decoupled (validates `REPOSITORY_INTELLIGENCE_ENGINE.md` §14's research question: quality ≠ popularity).

**Model check:** Requires `repository_scores` filtered to `score_type = 'difficulty'`, taking the latest `computed_at` per repository — needs an index supporting "latest score per repository per type," not just `repository_id`. **Finding:** a naive `(repository_id, score_type)` index without `computed_at DESC` would force a sort on every query. Phase 3 schema includes a composite index `(repository_id, score_type, computed_at DESC)` to serve this directly. ✅ Model unchanged; index requirement documented.

## 4. Category C — Quality & Documentation

20. Rank repositories by documentation quality.
21. Find repositories with a README above a readability threshold.
22. Find repositories with test coverage signals present (test-file ratio above X).
23. Find repositories with CI/CD configured.
24. Compare documentation quality across 2-5 specific repositories (side-by-side).
25. Find repositories with high code quality but low star count (underrated-gem detection, `claude.md` §16).
26. Find repositories where quality score has been improving over the last 3 snapshots (trend).
27. Find repositories with a code-smell or complexity red flag.
28. Rank repositories within a technology category by overall quality score.

**Model check:** Query 26 (quality trend) requires reading multiple `repository_scores` rows over time, not just the latest — this is exactly why Phase 1 modeled scores as append-only history rather than a single mutable column. ✅ Confirms the Phase 1 design decision was correct; no change needed. Query 24 (comparison) is an application-layer concern (fetch N repos' full score+metric rows), not a schema concern.

## 5. Category D — Technology & Stack

29. Find all repositories using a given technology, grouped by category (language vs framework vs db).
30. Find the most common technology combinations (co-occurrence, `TECHNICAL_IMPLEMENTATION_IDEAS.md` §6).
31. Find repositories that pair an unusual combination of technologies.
32. Find repositories with outdated dependencies above a threshold.
33. Find repositories with known-vulnerable dependencies.
34. Find repositories using a specific package at a specific version range.
35. Compare technology stacks of two repositories (overlap/difference).
36. Find repositories that recently adopted a new technology (requires historical association data — see finding below).
37. Rank technologies by number of repositories using them (popularity of a technology itself).

**Model check — finding:** Query 36 ("recently adopted") assumes `repository_technologies` has a notion of *when* the association first appeared. The Phase 1 model treats this table as a **current-state** table (recomputed and overwritten each pass), which cannot answer "when did this association start." **Resolution:** rather than turning `repository_technologies` into a time-series table (which would bloat the primary filtering/search table with historical noise it doesn't need for queries 29-35), add a `first_detected_at` timestamp column that is preserved across recomputation passes (set once, never overwritten) alongside the mutable `last_confirmed_at`. This answers "since when" without requiring a full history table, at the cost of not tracking *removals* precisely — an acceptable trade-off since query 36 is a "nice to have" trend signal, not a core V1 requirement per `IMPLEMENTATION_ROADMAP.md` Milestone 2/3 scope. Logged as a documented limitation in Phase 5.

## 6. Category E — Trends & Analytics

38. Track technology adoption rate over time (what % of new repos use React, by month).
39. Identify emerging technologies (rapid recent growth in adoption).
40. Identify declining technologies.
41. Compare adoption of a technology across industries/domains.
42. Compute average repository quality score, by language, over time.
43. Compute the distribution of difficulty scores across the corpus.
44. Track star velocity trends for a cohort of repositories.
45. Find the fastest-growing repositories in the last quarter.
46. Generate a report of the most common tech stack "recipes" (which technologies co-occur most).

**Model check:** Queries 38-40 need `first_detected_at` from the Category D finding above, aggregated by month — supported once that column exists. Queries 42-45 read `repository_scores` / `repository_snapshot` history, grouped and aggregated — supported by existing time-series design. Query 44/45 (star velocity, growth) needs `repository_snapshot` sorted by `snapshot_date` per repository, which is exactly what that table's PK/index (`repository_id, snapshot_date`) supports. ✅ No new entities required; confirms `repository_snapshot` earns its place as separate from `repository_metrics` (cheap, frequent, purely numeric time series — ideal for this kind of aggregate trend query without touching the heavier feature-store table).

## 7. Category F — Community & Maintenance Health

47. Find actively maintained repositories (commit within last N days).
48. Find repositories at risk of abandonment (declining activity + shrinking contributor base).
49. Find repositories with fast issue response times.
50. Find repositories with a healthy, growing contributor base.
51. Classify repositories by maintenance status (thriving/stable/declining/dormant).
52. Find single-maintainer repositories (bus-factor risk).
53. Find repositories where response time has degraded recently (trend, maintainer-burnout signal).
54. Filter search results to "actively maintained only."

**Model check:** All are satisfied by fields already planned in `repository_metrics` (`days_since_last_commit`, `unique_contributors_last_12mo`, `avg_issue_close_days`) combined with a `community_health` row in `repository_scores`. Query 52 (single-maintainer / bus-factor) is the one gap: it implies knowing contributor *concentration* (e.g., top contributor's % of commits), which requires either a dedicated metric column or the deferred Contributor entity (`DATABASE_DESIGN.md` §3.1). **Resolution:** add a scalar `top_contributor_commit_share` (numeric 0-1) to `repository_metrics` — an aggregate computable without persisting individual contributor identities, keeping Contributor correctly deferred while still answering the bus-factor query. ✅ Model refined, not restructured.

## 8. Category G — Similarity & Recommendations

55. Find the 10 most similar repositories to a given one.
56. Find repositories similar in technology stack but different in domain (cross-pollination discovery).
57. Given a repository the user has studied, recommend a "next step."
58. Recommend repositories that are high-quality but underrated (novelty + quality).
59. Find alternative implementations of the same problem (different tech, same purpose).
60. Explain why repository B was recommended given repository A (which similarity method, what score).
61. Exclude already-seen repositories from recommendation results (needs a seen-set — application layer, not schema).
62. Rank recommendation candidates combining similarity score with quality score (multi-signal ranking).

**Model check:** Queries 55, 60, 62 map directly to `repository_similarity` (method, score, rank columns) joined with `repository_scores`. Query 61 is explicitly out of schema scope (requires user session/history state, deferred per `DATABASE_DESIGN.md` §3.3). ✅ Confirms Phase 1's decision to store `similarity_method` and a numeric score (not just a rank) — query 60's "explain why" needs the raw score and method name, which a rank-only design would have lost.

## 9. Category H — Temporal / Evolution

63. Show how a repository's star count evolved since its creation.
64. Show how a repository's quality score has trended over the last year.
65. Detect a sudden anomaly (activity spike or cliff) in a repository's history.
66. Compare a repository's current state to its state 6 months ago.
67. Find repositories whose primary language changed over time (rare but real).
68. Reconstruct what technologies a repository used at a specific past date.

**Model check — finding:** Query 68 ("what technologies did repo X use on date Y") cannot be answered by a current-state `repository_technologies` table, even with the `first_detected_at` addition from Category D — that only tells you *when a technology started*, not the full technology set as of an arbitrary past date, since removed technologies leave no trace. **Resolution:** explicitly **not fixed in V1**. This is logged as an accepted limitation (Phase 5) because: (a) no roadmap checkpoint through Milestone 5 requires point-in-time technology reconstruction, (b) fixing it properly means either full technology-association history (high write volume for a rarely-needed query) or periodic full snapshots (redundant with the JSONB `loc_by_language` already captured in `repository_metrics`, which *does* give a point-in-time technology proxy via language breakdown). Documented as a known gap with a stated future path, not silently ignored.

## 10. Category I — Data Management & Operational

69. Find repositories that haven't been refreshed in over a week (staleness check for the update pipeline).
70. Find repositories that failed feature extraction and need retry.
71. Find duplicate repository records (data-quality check).
72. Count total repositories by acquisition batch/date.
73. Find repositories with incomplete data (missing README, missing license info) for backfill.
74. Audit which algorithm version produced a given score (reproducibility).
75. Find all repositories whose embedding is stale relative to their current README content.

**Model check:** Query 69 needs `repository.fetched_at` (last successful refresh timestamp) as a first-class column, not derivable from the time-series tables alone — **added to Phase 3 schema**. Query 75 is directly served by `repository_embedding.source_text_hash` (Phase 1 design already anticipated this). Query 70 needs a lightweight status flag — **added: `repository.last_extraction_status`** (enum: pending/success/failed), avoiding a whole separate job-tracking table for V1. ✅ Two small, justified column additions; no new entities.

## 11. Category J — Comparative & Cross-Repository Analytics

76. Compare 5 healthcare-domain repositories side-by-side on all quality dimensions.
77. Rank all repositories using a given framework by quality.
78. Find the repository with the best documentation among all Django projects.
79. Compute correlation between test coverage and community health across the corpus (research question, `REPOSITORY_INTELLIGENCE_ENGINE.md` §11).
80. Compute correlation between stars and quality score (expected: weak — validates the core thesis of the platform).
81. Find repositories that are outliers (very high quality, very low popularity, or the reverse).
82. Segment the corpus into quality tiers and count repositories per tier.
83. Benchmark a single repository against the median of its technology peer group.

**Model check:** All are read-only aggregate queries over `repositories` joined with the latest `repository_scores` and `repository_metrics` per repository — no new storage needed, but confirms the need (already planned) for an efficient "latest row per repository" access path on both time-series tables, addressed by the composite indexes specified in Phase 3.

---

## 12. Phase 2 Outcome

Of 83 concrete queries evaluated (grouped into 10 categories — the set naturally settled below the 100 ceiling once redundant phrasings were collapsed), **three findings led to model refinements** before any table was finalized:

1. **Bus-factor / contributor concentration** (Query 52) → added `top_contributor_commit_share` to `repository_metrics` rather than prematurely modeling individual Contributors.
2. **Technology adoption timing** (Query 36, 38-40) → added `first_detected_at` to `repository_technologies`, preserved across recomputation.
3. **Operational staleness/status tracking** (Queries 69-70) → added `fetched_at` and `last_extraction_status` to `repositories`.

One finding was an **accepted, documented limitation**, not a fix:

4. **Point-in-time technology reconstruction** (Query 68) — out of V1 scope; `repository_metrics.extended_features.loc_by_language` serves as a partial proxy.

No new entities were required. This validates the Phase 1 conceptual model: the gaps found were all *attribute-level* refinements to existing entities, not missing entities — a sign the entity boundaries drawn in `DATABASE_DESIGN.md` were correctly placed.

---

# PHASE 3: Logical Schema Design

## 13. Primary Key Strategy (Explicit Decision)

Two PK strategies are used, deliberately, by table role:

- **UUID (via `uuid-ossp`, already installed in Checkpoint 0.1)** for entities that are: (a) referenced externally (future API responses), (b) moderate volume, (c) need non-guessable, globally-unique identifiers. Used for: `owners`, `repositories`, `licenses`, `topics`, `technologies`.
- **BIGINT GENERATED ALWAYS AS IDENTITY** for high-volume, append-only, internal-only tables where sequential insert locality (B-tree friendliness) and compact index size matter more than external referenceability. Used for: `repository_dependencies`, `repository_metrics`, `repository_snapshot`, `repository_scores`.
- **Composite natural keys** (no surrogate) where the natural key is already correct and stable: `repository_topics`, `repository_technologies`, `repository_embeddings`, `repository_similarity`.

**Rationale:** A blanket "UUID everywhere" policy (common but not automatically correct) would give every metrics/snapshot row — potentially millions after 5 years of weekly snapshots across thousands of repositories — a 16-byte random-order primary key, hurting insert locality and bloating indexes for tables that are never referenced from outside the system anyway. This mirrors real-world PostgreSQL-at-scale guidance cited in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2.

---

## 14. Table Definitions

### 14.1 `owners`

**Why it exists:** Normalizes GitHub account data referenced by many repositories (§2.2 conceptual design).

| Column | Type | Constraint |
|---|---|---|
| id | uuid | PK, default `uuid_generate_v4()` |
| github_id | bigint | UNIQUE NOT NULL |
| login | text | NOT NULL |
| account_type | enum('user','organization') | NOT NULL |
| avatar_url | text | |
| github_created_at | timestamptz | |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now() |

**Indexes:** `UNIQUE (github_id)` — primary lookup path when ingesting; `INDEX (login)` — supports "find owner by login" for manual/debug queries.

**Expected volume:** ~50-500 by end of V1 (1,000 repos rarely have 1,000 distinct owners; organizations own many repos each). Growth: linear with repository growth, sub-linear in practice (owner reuse). 5-year projection at 100K repositories: ~20-40K owners.

**Queries optimized:** Category A (owner filtering), all joins from `repositories`.

---

### 14.2 `licenses`

**Why it exists:** Reference table for SPDX license identifiers (§2.3).

| Column | Type | Constraint |
|---|---|---|
| id | uuid | PK |
| spdx_id | text | UNIQUE NOT NULL |
| name | text | NOT NULL |
| is_osi_approved | boolean | NOT NULL, default false |

**Indexes:** `UNIQUE (spdx_id)`.

**Expected volume:** ~500 (SPDX's full license list). Effectively static — no meaningful growth.

**Queries optimized:** Category A query 6 (license filtering).

---

### 14.3 `topics`

**Why it exists:** Reference/growing table for GitHub's maintainer-assigned topic tags (§2.4).

| Column | Type | Constraint |
|---|---|---|
| id | uuid | PK |
| slug | text | UNIQUE NOT NULL |
| name | text | NOT NULL |

**Indexes:** `UNIQUE (slug)`.

**Expected volume:** Low thousands at V1 scale; GitHub has tens of thousands of topics platform-wide, but only those actually used by ingested repositories appear here. Growth: sub-linear (topic reuse is high — `react`, `python`, `api` recur constantly).

---

### 14.4 `technologies`

**Why it exists:** First-class, curated, deduplicated technology taxonomy (§2.5).

| Column | Type | Constraint |
|---|---|---|
| id | uuid | PK |
| slug | text | UNIQUE NOT NULL |
| name | text | NOT NULL |
| category | enum('language','framework','database','testing_tool','devops_tool','platform') | NOT NULL |
| homepage_url | text | |
| created_at | timestamptz | NOT NULL, default now() |

**Indexes:** `UNIQUE (slug)`; `INDEX (category)` — supports "list all databases" / "list all languages" queries (Category D query 29).

**Expected volume:** Low hundreds at V1 scale (curated taxonomy, not raw package names — those live in `repository_dependencies`). Growth: sub-linear, plateaus as the taxonomy matures.

**Queries optimized:** Category D (all), Category A queries 1-3.

---

### 14.5 `repositories` (central entity)

**Why it exists:** Core identity and slowly-changing metadata (§2.1).

| Column | Type | Constraint |
|---|---|---|
| id | uuid | PK, default `uuid_generate_v4()` |
| github_id | bigint | UNIQUE NOT NULL |
| owner_id | uuid | FK → owners.id, NOT NULL, `ON DELETE RESTRICT` |
| name | text | NOT NULL |
| full_name | text | UNIQUE NOT NULL |
| description | text | |
| primary_language | text | |
| default_branch | text | |
| license_id | uuid | FK → licenses.id, NULL, `ON DELETE SET NULL` |
| homepage_url | text | |
| is_archived | boolean | NOT NULL, default false |
| is_fork | boolean | NOT NULL, default false |
| is_template | boolean | NOT NULL, default false |
| size_kb | integer | |
| github_created_at | timestamptz | NOT NULL |
| pushed_at | timestamptz | |
| first_seen_at | timestamptz | NOT NULL, default now() |
| fetched_at | timestamptz | NOT NULL, default now() — *added per Phase 2 Query 69* |
| last_extraction_status | enum('pending','success','failed') | NOT NULL, default 'pending' — *added per Phase 2 Query 70* |
| created_at | timestamptz | NOT NULL, default now() |
| updated_at | timestamptz | NOT NULL, default now() |

**Indexes:**
- `UNIQUE (github_id)` — ingestion upsert key
- `UNIQUE (full_name)` — human-facing lookup
- `INDEX (owner_id)` — FK join support
- `INDEX (primary_language)` — Category A query 1
- `INDEX (license_id)` — Category A query 6
- `INDEX (is_archived, is_fork) WHERE is_archived = false AND is_fork = false` (partial index) — serves the extremely common "active, original work" default filter (Category A query 10) cheaply
- `INDEX (fetched_at)` — staleness queries (Category I query 69)
- Full-text search index (`GIN` over `to_tsvector('english', name || ' ' || description)`) — Category A query 5, and the keyword half of hybrid search (Milestone 4.1)

**Expected volume:** 100 (Milestone 1) → 1,000 (V1 target, `SYSTEM_ARCHITECTURE.md` §8) → 10K → 100K → millions (5-year, per §9's scaling roadmap). This is the table every other table hangs off of; its row width is deliberately kept narrow (identity + metadata only, no computed features) precisely because it will be touched by every query.

**5-year growth consideration:** At 100K-1M rows, this table remains entirely reasonable for a single PostgreSQL instance (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §2 cites PostgreSQL scaling to billions of rows). No partitioning needed at this table's projected size.

---

### 14.6 `repository_topics`

**Why it exists:** M:N join, no edge attributes (§3.3 ER rationale).

| Column | Type | Constraint |
|---|---|---|
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE` |
| topic_id | uuid | FK → topics.id, `ON DELETE CASCADE` |

**PK:** `(repository_id, topic_id)` — composite, no surrogate needed.

**Indexes:** The PK itself covers `repository → topics` lookups; a secondary `INDEX (topic_id)` supports the reverse direction (Category A query 4: "find repos with topic X").

**Expected volume:** ~3-8 topics per repository on average (GitHub's UI encourages a handful) → 3K-8K rows at 1,000 repositories; hundreds of thousands at 100K repositories. Trivial for PostgreSQL.

---

### 14.7 `repository_technologies`

**Why it exists:** M:N with edge attributes (§2.6, §3.4).

| Column | Type | Constraint |
|---|---|---|
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE` |
| technology_id | uuid | FK → technologies.id, `ON DELETE CASCADE` |
| role | enum('primary','secondary','dev') | NOT NULL |
| detected_via | enum('manifest','dockerfile','ci_config','linguist') | NOT NULL |
| confidence | numeric(3,2) | NOT NULL, CHECK (confidence BETWEEN 0 AND 1) |
| first_detected_at | timestamptz | NOT NULL, default now() — *added per Phase 2 Category D finding; never overwritten on recompute* |
| last_confirmed_at | timestamptz | NOT NULL, default now() — *updated every recomputation pass* |

**PK:** `(repository_id, technology_id)`.

**Indexes:** PK covers `repository → technologies`; `INDEX (technology_id, role)` supports "find all repos using React as primary" (Category A query 2, Category D query 29).

**Expected volume:** ~5-15 technologies per repository → 5K-15K rows at 1,000 repos; low millions at 100K repos. Reasonable.

---

### 14.8 `repository_dependencies`

**Why it exists:** Fine-grained package data, distinct from the curated Technology taxonomy (§2.7).

| Column | Type | Constraint |
|---|---|---|
| id | bigint | PK, GENERATED ALWAYS AS IDENTITY |
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE`, NOT NULL |
| package_name | text | NOT NULL |
| ecosystem | enum('npm','pypi','cargo','go','maven','nuget','rubygems') | NOT NULL |
| version_constraint | text | |
| resolved_version | text | |
| is_direct | boolean | NOT NULL |
| is_dev | boolean | NOT NULL, default false |
| dependency_age_days | integer | |
| is_deprecated | boolean | NOT NULL, default false |
| vulnerability_count | integer | NOT NULL, default 0 |
| last_checked_at | timestamptz | NOT NULL, default now() |

**Indexes:** `INDEX (repository_id)`; `INDEX (package_name, ecosystem)` — Category D query 34 ("find repos using package X"); partial `INDEX (repository_id) WHERE vulnerability_count > 0` — Category D query 33.

**Expected volume:** This is the **highest-cardinality table in the schema**. Average 30-100 dependencies per repository (including transitive, per `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5). At 1,000 repos: 30K-100K rows. At 100K repos: 3M-10M rows. **Explicit 5-year flag:** at millions-of-repos scale (§9 Version 4), this table is the first candidate for partitioning (by `repository_id` range or `ecosystem`) or migration to a columnar store — flagged in Phase 5, not solved prematurely here.

---

### 14.9 `repository_metrics` (feature store)

**Why it exists:** Time-series computed features (§2.8).

| Column | Type | Constraint |
|---|---|---|
| id | bigint | PK, GENERATED ALWAYS AS IDENTITY |
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE`, NOT NULL |
| snapshot_date | date | NOT NULL |
| commits_last_3mo | integer | |
| commits_last_6mo | integer | |
| commits_last_12mo | integer | |
| unique_contributors_last_12mo | integer | |
| top_contributor_commit_share | numeric(4,3) | CHECK (0 ≤ value ≤ 1) — *added per Phase 2 Category F finding* |
| days_since_last_commit | integer | |
| commit_frequency_stddev | numeric | |
| issues_open_count | integer | |
| issues_closed_count | integer | |
| issue_close_rate | numeric(4,3) | |
| avg_issue_close_days | numeric | |
| prs_merged_count | integer | |
| pr_merge_rate | numeric(4,3) | |
| avg_pr_merge_hours | numeric | |
| readme_present | boolean | |
| readme_word_count | integer | |
| readme_section_count | integer | |
| readme_code_example_count | integer | |
| readme_readability_score | numeric | |
| loc_total | integer | |
| test_file_ratio | numeric(4,3) | |
| comment_to_code_ratio | numeric(4,3) | |
| direct_dependency_count | integer | |
| transitive_dependency_count | integer | |
| outdated_dependency_pct | numeric(4,3) | |
| loc_by_language | jsonb | — genuinely variable-shaped (arbitrary language keys), justified JSONB use |
| extended_features | jsonb | — overflow bucket for long-tail/experimental features not yet promoted to columns |
| computed_at | timestamptz | NOT NULL, default now() |

**PK:** `id` (bigint identity). **Unique constraint:** `(repository_id, snapshot_date)` — one snapshot per repo per day, prevents duplicate computation runs from creating phantom rows.

**Indexes:**
- `UNIQUE (repository_id, snapshot_date)`
- `INDEX (repository_id, snapshot_date DESC)` — "latest metrics per repository" access path, used by nearly every intelligence and search query (Category B, C, F, J)
- `GIN INDEX (extended_features)` — supports ad-hoc querying into the JSONB overflow bucket without requiring a migration every time a new experimental feature is added

**On JSONB usage — explicit restraint:** Only two of ~27 columns are JSONB (`loc_by_language`, `extended_features`). Every feature named explicitly in `TECHNICAL_IMPLEMENTATION_IDEAS.md` §5 as a "Stage 1: Essential Feature" gets its own typed column, because these are queried, filtered, and indexed constantly (Category B-F, ~40 of the 83 queries touch this table). JSONB is reserved for (a) genuinely variable-shaped data (per-language LOC breakdown — the key set is unbounded) and (b) a deliberate escape hatch for features that haven't earned a promoted column yet. This directly follows the CLAUDE.md-level project instruction: "Use JSONB only when flexibility is genuinely beneficial... do not overuse JSON."

**Expected volume:** One row per repository per week (batch update cadence, `SYSTEM_ARCHITECTURE.md` §3 Stage 4). At 1,000 repos: ~52K rows/year. At 100K repos over 5 years: ~260M rows. **Explicit 5-year flag:** this is the second partitioning candidate (by `snapshot_date` range, monthly or quarterly partitions) — flagged in Phase 5.

---

### 14.10 `repository_snapshot` (raw metadata history)

**Why it exists:** Cheap, high-frequency raw counters, separate from expensive computed metrics (§2.9).

| Column | Type | Constraint |
|---|---|---|
| id | bigint | PK, GENERATED ALWAYS AS IDENTITY |
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE`, NOT NULL |
| snapshot_date | date | NOT NULL |
| stars_count | integer | NOT NULL |
| forks_count | integer | NOT NULL |
| watchers_count | integer | NOT NULL |
| open_issues_count | integer | NOT NULL |
| size_kb | integer | |

**PK:** `id`. **Unique constraint:** `(repository_id, snapshot_date)`.

**Indexes:** `UNIQUE (repository_id, snapshot_date)`; `INDEX (repository_id, snapshot_date)` for range scans (Category E/H trend queries — star velocity, growth curves).

**Expected volume:** Potentially higher frequency than `repository_metrics` (can be captured on every API touch, not just weekly feature-engineering runs) — the largest table by row count at scale. At 1,000 repos, daily snapshots: ~365K rows/year. **Explicit 5-year flag:** primary partitioning candidate, same as 14.9, by `snapshot_date`.

---

### 14.11 `repository_scores`

**Why it exists:** Versioned intelligence outputs (§2.10).

| Column | Type | Constraint |
|---|---|---|
| id | bigint | PK, GENERATED ALWAYS AS IDENTITY |
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE`, NOT NULL |
| score_type | enum('difficulty','quality','community_health') | NOT NULL |
| score_value | numeric(5,2) | NOT NULL |
| score_breakdown | jsonb | — component contributions genuinely vary per score_type (difficulty's components differ from quality's) |
| algorithm_version | text | NOT NULL |
| computed_at | timestamptz | NOT NULL, default now() |

**Indexes:** `INDEX (repository_id, score_type, computed_at DESC)` — directly serves "latest score of type X for repository Y" (Category B, C, F, J — the single most-used access pattern outside of `repositories` itself).

**On JSONB usage:** `score_breakdown` is JSONB because difficulty's breakdown (`{loc_weight, complexity_weight, docs_weight}`) and quality's breakdown (`{test_coverage_weight, maintenance_weight, ...}`) are structurally different per `score_type` — a textbook justified case, not overuse.

**Expected volume:** 3 score types × weekly cadence × repository count. At 1,000 repos: ~156K rows/year. Same partitioning candidacy as 14.9/14.10 at scale.

---

### 14.12 `repository_embeddings`

**Why it exists:** Model-scoped semantic vectors (§2.11).

| Column | Type | Constraint |
|---|---|---|
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE` |
| model_name | text | |
| model_version | text | NOT NULL |
| embedding | vector(1536) | NOT NULL — using `pgvector`, already installed in Checkpoint 0.1 |
| source_text_hash | text | NOT NULL |
| computed_at | timestamptz | NOT NULL, default now() |

**PK:** `(repository_id, model_name)`.

**Indexes:** An HNSW or IVFFlat index (`pgvector`) on `embedding` for approximate nearest-neighbor search — the specific index type and parameters are a Milestone 4.2 implementation decision (not fixed at schema-design time, since it depends on eventual corpus size per `TECHNICAL_IMPLEMENTATION_IDEAS.md` §2's pgvector trade-off discussion — appropriate for 1-10M vectors, exactly V1's scale-out ceiling).

**Expected volume:** One row per repository per active model. At 1,000 repos, 1 model: 1,000 rows. Grows linearly with repository count; grows step-wise when a new model is adopted (old model's rows are not deleted — enables comparing search quality across model versions).

---

### 14.13 `repository_similarity`

**Why it exists:** Bounded top-K precomputed similarity (§2.12).

| Column | Type | Constraint |
|---|---|---|
| repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE` |
| similar_repository_id | uuid | FK → repositories.id, `ON DELETE CASCADE` |
| similarity_method | enum('embedding_cosine','feature_distance','hybrid') | |
| similarity_score | numeric(5,4) | NOT NULL |
| rank | smallint | NOT NULL |
| computed_at | timestamptz | NOT NULL, default now() |

**PK:** `(repository_id, similar_repository_id, similarity_method)`.

**Constraint:** `CHECK (repository_id <> similar_repository_id)`.

**Indexes:** PK covers "top-K for repository X, method Y" directly when combined with `ORDER BY rank`; add `INDEX (repository_id, similarity_method, rank)` explicitly to guarantee the ordering is index-served, not sorted at query time.

**Expected volume:** Bounded by design — `repository_count × K × method_count`. At 1,000 repos, K=20, 2 methods: 40,000 rows. At 100K repos: 4M rows — **linear**, not quadratic, confirming the Phase 1 design decision (§2.12) was load-bearing, not cosmetic.

---

## 15. Cross-Cutting Index Summary

| Query need | Index |
|---|---|
| "Latest metrics for repo" | `repository_metrics (repository_id, snapshot_date DESC)` |
| "Latest score of type X for repo" | `repository_scores (repository_id, score_type, computed_at DESC)` |
| "Active, original repos" (default filter) | `repositories (is_archived, is_fork)` partial |
| "Repos using tech X" | `repository_technologies (technology_id, role)` |
| "Repos with vulnerable deps" | `repository_dependencies (repository_id) WHERE vulnerability_count > 0` partial |
| Keyword search | `repositories` GIN full-text index |
| Semantic search | `repository_embeddings` pgvector ANN index |
| Star/growth trend | `repository_snapshot (repository_id, snapshot_date)` |
| Similarity lookup | `repository_similarity (repository_id, similarity_method, rank)` |

Every index above is derived from a named query in Phase 2, not speculative — matching the project's stated engineering discipline (no premature optimization, no unjustified structures).

---

## 16. Summary

12 core tables (matching the 12 core entities in `DATABASE_DESIGN.md`), 3 justified JSONB columns out of ~90 total columns, 2 explicit primary-key strategies chosen by table role, and every index traced to a specific query from the 83-query workload in Phase 2.

Two tables (`repository_metrics`, `repository_snapshot`) and one (`repository_scores`) are flagged for future partitioning by date range — not implemented now, since V1's projected volume (tens of thousands of rows/year) does not warrant the operational complexity, but the column choices (BIGINT identity, not UUID) were made specifically to keep that door open.

Proceed to `MIGRATION_STRATEGY.md` for how these 12 tables are sequenced into Alembic migrations.
