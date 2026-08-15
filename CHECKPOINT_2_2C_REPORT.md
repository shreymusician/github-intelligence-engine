# Checkpoint 2.2.c Report — Technology Orchestration + Live Verification

**Parent Checkpoint:** 2.2 (Technology Stack Detection), sub-checkpoint c of c
**Status:** ✅ Complete — deterministic tests, small live sample verification, and full-corpus live run all completed successfully against the real 92-repository-with-content corpus.

---

## 1. Objective

Connect the three already-independent, already-verified components from 2.2.a (`processing/manifest_parser.py`) and 2.2.b (`processing/technology_taxonomy.py`, `processing/technology_storage.py`) into one orchestrated, failure-isolated batch run over `repository_content` rows already persisted by Checkpoint 1.4 — no new parsing logic, no new taxonomy/classification logic, no new persistence SQL, no schema change, no migration, no AI/LLM/embeddings, no package-registry calls.

## 2. Architecture / Data Flow

```
repository_content (manifest rows, filtered by content_type)
    -> ManifestCandidateProvider.get_candidates()   (new: 2.2.c's only new SQL)
    -> parse_manifest()                              (2.2.a, unmodified)
    -> classify_language() / classify_package()       (2.2.b, unmodified)
    -> TechnologyWriter.upsert_technologies()          (2.2.b, unmodified)
    -> next repository
```

New module: `processing/technology_pipeline.py`, mirroring `acquisition/content_pipeline.py`'s (1.4.d) orchestration pattern exactly — dependency-injected collaborators, a candidate provider with connection-per-call semantics, a `run()`/`_process_one()` split, structured `*Stats`/`*Result` dataclasses, and per-repository failure isolation with typed-then-broad exception handling.

Neither `manifest_parser.py`, `technology_taxonomy.py`, `technology_storage.py`, nor any prior migration was modified.

## 3. Candidate Selection

`ManifestCandidateProvider.get_candidates()` runs one parameterized SELECT:

```sql
SELECT rc.repository_id, r.full_name, rc.content_type, rc.content
FROM repository_content rc
JOIN repositories r ON r.id = rc.repository_id
WHERE rc.content_type = ANY(%(content_types)s)
ORDER BY r.id, rc.id
```

filtered on `processing.manifest_parser.SUPPORTED_MANIFEST_CONTENT_TYPES` (the 10 already-approved manifest formats — no new format added), then grouped in Python into one `TechnologyCandidate(repository_id, full_name, manifests)` per repository. It does **not** exclude repositories that already have `repository_technologies` rows — `TechnologyWriter` is already the authoritative idempotency boundary, exactly mirroring `RepositoryContentCandidateProvider`'s (1.4.d) rationale.

## 4. Failure Isolation

Two failure stages are caught per repository, each recorded as a `TechnologyFailure(repository_id, full_name, stage, error_type, message, ...)` and logged, with the loop always continuing:

- **`persist`** — `TechnologyPersistenceError` from `TechnologyWriter.upsert_technologies()` (2.2.b's own transaction-rollback-then-raise contract).
- **`unexpected`** — any other exception during parsing, classification, or persistence, logged via `log.exception(...)` before being recorded.

A per-manifest `parse_error` (2.2.a's "report failure as data" contract — `parse_manifest()` never raises) is **not** a repository-level failure: it is counted in `parse_errors` and processing continues with the repository's remaining manifests and whatever dependencies were already parsed.

## 5. Empty / Unclassified Behavior

Verified by both deterministic tests and the live run:

- Repository with no manifest → never a candidate (filtered at the SQL level); not an error.
- Repository with manifest but zero parsed dependencies → `ProcessedRepository` with `dependencies_parsed=0`, `technologies_persisted=0`; not an error.
- Malformed manifest → `parse_error` recorded, `parse_errors` incremented, remaining manifests for that repository still processed (live-confirmed: `odysseus-dev/odysseus`'s `setup_py` — no literal `install_requires` inside its `setup()` call — and one other repository in the full run, 2 total).
- Dependencies with no taxonomy match → contribute nothing; not an error (e.g. `numpy`, `pandas` parse successfully but classify to `None`, per 2.2.b's explicit conservative policy).
- Persistence failure → that repository's transaction rolls back (2.2.b's existing guarantee), recorded as a `persist`-stage failure, batch continues.
- Unexpected exception → recorded as `unexpected`, logged with a full traceback, batch continues.

**Deliberate, documented limitation:** a manifest that parses to zero dependencies contributes **no** language classification either, even though 2.2.b's `classify_language()` docstring notes a manifest's mere existence structurally implies its ecosystem's language. Deriving an ecosystem from `content_type` alone (independent of any actually-parsed `ParsedDependency`) would require a new `content_type -> ecosystem` mapping that neither 2.2.a nor 2.2.b expose as reusable, tested logic — building one here would be a new classification mechanism, explicitly out of scope. `classify_language()` is called only once per distinct `ParsedDependency.ecosystem` actually observed among a repository's parsed dependencies. This is the conservative reading of the brief's "do not manufacture a technology just because a manifest exists," applied to the language case too.

## 6. Idempotency

No new deduplication mechanism was built — re-running is safe purely because `TechnologyWriter.upsert_technologies()` (2.2.b) already guarantees idempotent persistence via `ON CONFLICT`. Verified live (§9): re-running the same 5-repository sample produced the same 13 `repository_technologies` rows (no duplicates), with `last_confirmed_at` advancing and `first_detected_at` unchanged, matching 2.2.b's documented semantics exactly.

## 7. Statistics — What Each Field Measures

`TechnologyAcquisitionStats`: `candidates_selected`, `attempted`, `succeeded`, `failed`, `manifests_processed`, `dependencies_parsed`, `parse_errors`, `technologies_persisted`.

`technologies_persisted` counts **relationship-write operations** (the length of `upsert_technologies()`'s return list, summed across repositories) — **not** unique technology identities and **not** the final distinct-row count in `repository_technologies`. If the same package appears in two different manifest files for one repository (e.g. `flask` in both `requirements.txt` and `pyproject.toml`), it is classified and upserted twice within that repository's single transaction (harmless — `ON CONFLICT` collapses it to one row), and both upserts are counted here. This is why the full-corpus run's `technologies_persisted=124` is larger than the final distinct `repository_technologies` row count (117, confirmed by direct SQL count, §9) — the gap is fully explained by this within-repository, cross-manifest overlap, not a bug.

## 8. Deterministic Tests

`tests/test_technology_pipeline.py` — **21 new tests**, zero DB/network dependency (fakes for the candidate provider, parser, both classifiers, and the writer), mirroring `tests/test_content_pipeline.py`'s dict/list-driven, call-recording fake style:

- `TestBasicRun` (8): empty candidate set; one successful repository; manifest with zero dependencies is not an error; malformed manifest records a parse error but is not a failure; unrecognized dependencies are not an error; mixed recognized/unrecognized persists only the recognized ones; language classified once across multiple same-ecosystem manifests (not once per manifest); a manifest with zero dependencies does **not** manufacture a language classification (§5).
- `TestFailureIsolation` (6): persistence failure recorded, batch continues; unexpected exception during parsing recorded, batch continues; unexpected exception from the writer recorded; all-repositories-failing; mixed success/failure preserves order; candidate-provider failure propagates uncaught (no partial-batch concept before candidates exist).
- `TestStatisticsInvariant` (1): `attempted == succeeded + failed`.
- `TestLogging` (1): start/completion progress logging present.
- `TestCandidateProvider` (5): manifest rows grouped correctly by repository; multiple repositories kept separate; query filters by supported content types with no `repository_technologies` exclusion; connection always closed; empty result set.

**All 21 pass.** Full regression suite (`pytest -q`, entire `tests/` directory): **365 passed, 4 skipped** (the 4 skips are pre-existing and unrelated to this change — confirmed by running the suite before this checkpoint's changes were added).

## 9. Live Verification

Docker Desktop and the `repo_intelligence_postgres` container (previously stopped) were started for this checkpoint's live verification; the corpus state was otherwise untouched from prior checkpoints (`technologies`/`repository_technologies` were empty going in — 0 rows each, confirmed by SQL before any run).

**Pre-run corpus state** (measured, not assumed):

| Metric | Value |
|---|---|
| Repositories total | 100 |
| Repositories with any `repository_content` row | 92 |
| **Repositories with manifest-typed content (2.2.c-eligible)** | **66** |
| Manifest rows total | 114 |

### 9.1 Small representative sample (5 repositories)

Ran `TechnologyPipeline` against the first 5 of the 66 eligible repositories (`ZhuLinsen/daily_stock_analysis`, `scikit-learn/scikit-learn`, `odysseus-dev/odysseus`, `yt-dlp/yt-dlp`, `pathwaycom/pathway`):

`TechnologyAcquisitionStats(candidates_selected=5, attempted=5, succeeded=5, failed=0, manifests_processed=12, dependencies_parsed=328, parse_errors=1, technologies_persisted=13)`

**Independent SQL verification** (not reusing pipeline output):
- `repository_technologies` row count: 13, exactly matching the run's persisted-relationship count for a first run (no pre-existing overlap).
- 6 distinct technology identities produced: `python`, `javascript`, `rust` (languages); `fastapi`, `pytest` (framework/testing_tool); `aws-sdk-python` (platform) — every one traceable to an actual dependency in the sampled repositories' manifests (e.g. `pathwaycom/pathway`'s `Cargo.toml` → `rust`).
- Zero duplicate `(repository_id, technology_id)` pairs.
- Zero rows with `confidence != 1.0`.
- `detected_via` is `'manifest'` for all 13 rows (only value present).
- Zero rows with a `repository_id` not present in `repositories` (join returned 0 orphans).

**Idempotency check:** re-ran the identical 5-repository sample. `repository_technologies` row count unchanged (13, no duplicates), `last_confirmed_at` advanced on every row (e.g. `pathwaycom/pathway`/`python`: `08:42:34` → `08:43:37`), `first_detected_at` unchanged — matching 2.2.b's documented upsert contract exactly.

Sample verification was clean, so the full-corpus run proceeded per the brief's instruction ("first run a small representative sample ... then perform the broader corpus run only if the small live verification is clean").

### 9.2 Full corpus run (66 eligible repositories)

`TechnologyAcquisitionStats(candidates_selected=66, attempted=66, succeeded=66, failed=0, manifests_processed=114, dependencies_parsed=3223, parse_errors=2, technologies_persisted=124)`

**Independent SQL verification:**

| Check | Result |
|---|---|
| `technologies` row count | 8 |
| `repository_technologies` row count | 117 |
| Duplicate `(repository_id, technology_id)` pairs | 0 |
| Rows with `confidence != 1.0` | 0 |
| Distinct `detected_via` values | 1 (`'manifest'`) |
| Rows with an invalid/orphaned `repository_id` | 0 |

**8 technology identities actually observed and persisted** (of the taxonomy's 11 curated identities — `go`, `ruby`, `php` were not observed because no `go.mod`/`Gemfile`/`composer.json` exists anywhere in the current 66-repository manifest corpus):

| Slug | Category | Repositories |
|---|---|---|
| python | language | 58 |
| pytest | testing_tool | 20 |
| fastapi | framework | 16 |
| aws-sdk-python | platform | 13 |
| javascript | language | 6 |
| flask | framework | 2 |
| rust | language | 1 |
| svelte | framework | 1 |

The 124-vs-117 gap between the run's reported `technologies_persisted` and the final distinct row count is fully explained by within-repository, cross-manifest overlap (§7) — not data loss or a bug.

**No coverage/completion conflation:** the taxonomy having 11 curated identities is a statement about the vocabulary's size, not about how many technologies exist in the corpus. The measured result is 8 distinct identities actually classified and persisted across 117 repository↔technology relationships, from 3,223 raw parsed dependencies (most of which — e.g. `numpy`, `pandas`, `requests` — correctly classify to `None` per 2.2.b's conservative policy and contribute nothing).

## 10. Files Changed

| File | Change |
|---|---|
| `processing/technology_pipeline.py` | New — `ManifestCandidateProvider`, `TechnologyPipeline`, supporting dataclasses |
| `tests/test_technology_pipeline.py` | New — 21 tests |
| `CHECKPOINT_2_2C_REPORT.md` | New — this report |

No existing file modified. **No migration. No schema change. No taxonomy expansion.**

## 11. Known Limitations

- Manifests parsing to zero dependencies contribute no language classification (§5) — a deliberate, conservative scope decision, not an oversight; means a repository with e.g. only an empty `package.json` will show no `javascript` technology.
- `technologies_persisted` in the stats is a relationship-write count, not a unique-identity count or final-row count (§7) — documented to avoid misreading the number.
- No stale-technology reconciliation exists (unchanged from 2.2.b) — `repository_technologies` can only grow, never shrink, across re-runs. Still explicitly deferred, not built here.
- `go`, `ruby`, `php` remain unobserved in the live corpus (§9.2) purely because no manifest of those ecosystems exists in the current 66 repositories — not a defect in the taxonomy or orchestrator.

## 12. Commits

| Commit | Message |
|---|---|
| `4e3084d` | Checkpoint 2.2.c: Technology orchestration implementation + tests |
| *(this report)* | Checkpoint 2.2.c: Live verification report |

Both pushed to `origin/main` individually; `git rev-parse HEAD` verified equal to `git rev-parse origin/main` after the implementation commit.

## 13. Push Verification

```
4e3084de38e8b5db982c94d945c1cae1f243bee7  (local HEAD)
4e3084de38e8b5db982c94d945c1cae1f243bee7  (origin/main)
```

Confirmed equal after `git push origin main`.

## 14. Stop Condition

Per the checkpoint brief: **2.2.c is complete. Stopping here.** No work has begun on 2.3, 2.4, taxonomy expansion, or AI/embeddings/search/scoring. Awaiting review.
