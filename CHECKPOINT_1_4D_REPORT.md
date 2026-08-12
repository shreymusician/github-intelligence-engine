# Checkpoint 1.4.d Report — Content Acquisition Orchestration and Cleanup

**Parent Checkpoint:** 1.4 (Repository Content Download), sub-checkpoint d of e
**Status:** ✅ Complete, verified (deterministic mocked tests + live end-to-end run against real PostgreSQL, real Git, real repositories)
**Date:** 2026-08-12

---

## 1. Objective

Orchestrate the three already-independent, already-verified components from Checkpoints 1.4.a/1.4.b/1.4.c into one batch run over the repositories already persisted by Checkpoint 1.3:

```
repository candidate (id, full_name)
    -> RepositoryCloner.clone()        (1.4.a — temporary shallow clone)
    -> extract_content()               (1.4.b — README/manifest extraction)
    -> ContentWriter.upsert_content()  (1.4.c — durable persistence)
    -> next candidate
```

This module introduces no new git logic, no new README/manifest detection logic, no new hashing, and no new persistence/upsert SQL. Its job is orchestration: failure isolation, statistics, progress logging, and guaranteed cleanup — exactly mirroring how `acquisition/pipeline.py` (1.3.c) orchestrates 1.1/1.3.a/1.3.b without duplicating any of their logic.

---

## 2. Phase 1 — Design Verification (confirmed before coding)

Re-read `acquisition/pipeline.py` (1.3.c, the direct architectural precedent), `acquisition/selection.py` (1.3.a), `tests/test_pipeline.py`, `acquisition/clone_workspace.py`, `acquisition/content_extractor.py`, `acquisition/content_storage.py`, `acquisition/exceptions.py`, `config/settings.py`, and `logging_setup.py` directly before writing any code.

Key finding confirmed during design verification: nothing in the codebase queried the `repositories` table for a list of `(id, full_name)` pairs — `RepositorySelector` (1.3.a) only searches *GitHub* for new candidates, the wrong data source for content acquisition, which must operate over repositories **already persisted** by Checkpoint 1.3. This required one new, minimal, read-only component: `RepositoryContentCandidateProvider`.

**Explicit architectural correction from your review, applied before implementation:** `RepositoryContentCandidateProvider.get_candidates()` performs a simple, unfiltered `SELECT id, full_name FROM repositories ORDER BY id` — no `JOIN`, no `NOT EXISTS`, no `repository_content`-based exclusion. This was a deliberate design decision, not an oversight: `ContentWriter` (1.4.c) is already the authoritative database-level idempotency boundary, and automatically skipping repositories with *any* existing `repository_content` row would encode unspecified orchestration/business logic about what counts as "fully acquired" — risking, for example, silently skipping a repository whose future manifest support hasn't been backfilled yet. No `skip_existing` capability was added, per your explicit instruction not to add it speculatively.

---

## 3. Responsibility Boundary

**1.4.d owns:** obtaining repository candidates (via the new `RepositoryContentCandidateProvider`); invoking `RepositoryCloner.clone()`, `extract_content()`, `ContentWriter.upsert_content()` in sequence; per-repository failure isolation; statistics; progress logging; continuation to the next repository; a structured result.

**1.4.d does not own (and does not duplicate):** git subprocess implementation, README/manifest detection, UTF-8 decoding, hashing, SQL upsert statements, PostgreSQL transaction management, `repository_content` idempotency semantics, or any schema/migration change. Confirmed by inspection — `acquisition/content_pipeline.py` imports `RepositoryCloner`, `extract_content`, and `ContentWriter` and calls them exactly as their existing public interfaces specify; it contains no `subprocess`, no file-reading, no `hashlib`, and no `INSERT`/`UPDATE` SQL of its own (the one new `SELECT` belongs to the candidate provider, a read boundary, not a persistence one).

---

## 4. Architecture

```
acquisition/content_pipeline.py
├── RepositoryContentCandidateProvider
│   └── get_candidates() -> list[tuple[uuid.UUID, str]]   — one unfiltered SELECT
├── ContentFailure / ProcessedRepository (dataclasses)
├── ContentAcquisitionStats / ContentAcquisitionResult (dataclasses)
└── ContentAcquisitionPipeline
    ├── __init__(candidate_provider, cloner, writer, extract_content_fn=extract_content)
    ├── run() -> ContentAcquisitionResult
    └── _process_one(repository_id, full_name) -> ProcessedRepository | ContentFailure
```

Every collaborator is dependency-injected (constructor arguments), matching `AcquisitionPipeline`'s precedent exactly — the class holds no global state and is fully testable against fakes, with the real `RepositoryCloner`/`ContentWriter`/`extract_content` used by default.

---

## 5. Failure Model

Per-repository sequence, mirroring 1.3.c's `try/except`-per-stage structure:

| Exception | Stage recorded | Behavior |
|---|---|---|
| `RepositoryCloneError` (1.4.a) | `"clone"` | Recorded, logged, loop continues |
| `ContentAcquisitionError` raised during extraction | `"extract"` | Recorded, logged, loop continues (defense-in-depth — `extract_content()` itself does not raise for per-file problems; it skips and logs internally per 1.4.b) |
| `ContentPersistenceError` (1.4.c, a `ContentAcquisitionError` subclass) | `"persist"` | Recorded, logged, loop continues; 1.4.c already guarantees zero partial rows for that repository |
| Any other exception | `"unexpected"` | Caught by a final `except Exception`, logged at ERROR with full exception info (`log.exception`), recorded with its real `error_type`/message, loop continues |
| Candidate-provider failure | — | **Not caught** — propagates to the caller; there is no partial-batch concept before any candidates exist to iterate, matching `AcquisitionPipeline`'s identical treatment of selection failures |

**Deliberate, approved divergence from `AcquisitionPipeline`:** 1.4.d's catch is broader than 1.3.c's (which only catches its own two typed-exception hierarchies) — a catch-all `except Exception` was explicitly required by your design approval, since an unexpected failure during content acquisition for one repository must never abort the batch, and must never be silently swallowed either.

**Empty extraction result is a success, not a failure** — `files_persisted=0` is recorded as a normal `ProcessedRepository`, per your explicit instruction (verified live in the earlier 1.4.c corpus data and structurally in `TestBasicRun::test_empty_extraction_is_a_success_not_a_failure`).

---

## 6. Cleanup Guarantee

No new cleanup logic was written. `RepositoryCloner.clone()`'s `@contextmanager` `finally` block (1.4.a, unmodified) unconditionally removes the clone directory regardless of how the `with` block exits. 1.4.d's `_process_one()` wraps the entire clone+extract sequence in one `with self._cloner.clone(full_name) as clone_dir:` block, so cleanup fires identically whether extraction succeeds, extraction raises, or an unexpected exception occurs inside the block. Persistence happens *after* the `with` block has already exited, so a persistence failure has nothing left to clean up. A repository that fails before cloning even starts (there is none in this design — candidates are used as-is) never opens a clone workspace in the first place. Live-verified directly (§9.2): zero leftover `ghia_clone_*` directories after both a run containing an intentional clone failure and a subsequent rerun.

---

## 7. Statistics Model

```python
ContentAcquisitionStats(candidates_selected, attempted, succeeded, failed)
```

**Invariant:** `attempted == succeeded + failed`, and `candidates_selected == attempted` (no `skipped` category exists — every candidate returned by the provider is attempted; there is no duplicate-producing mechanism in the new unfiltered `SELECT`, unlike 1.3.c's GitHub-Search-derived candidates, so no `skipped_duplicates` field was added). Verified deterministically in `TestStatisticsInvariant` and live in §9.1/§9.4.

---

## 8. Files Changed

| File | Change |
|---|---|
| `acquisition/content_pipeline.py` | New — `RepositoryContentCandidateProvider`, `ContentAcquisitionPipeline`, `ContentFailure`, `ProcessedRepository`, `ContentAcquisitionStats`, `ContentAcquisitionResult` |
| `acquisition/__init__.py` | Extended — exports only |
| `tests/test_content_pipeline.py` | New — 20 deterministic tests |
| `CHECKPOINT_1_4D_REPORT.md` | New — this report |

**Confirmed untouched** (by `git diff` inspection before this commit): `acquisition/clone_workspace.py`, `acquisition/content_extractor.py`, `acquisition/content_storage.py`, `acquisition/exceptions.py`, `alembic/versions/010_repository_content.py`, `github_client/*`.

---

## 9. Deterministic Test Suite (`tests/test_content_pipeline.py`, 20 tests)

Composes the **real** `ContentAcquisitionPipeline` with fake `RepositoryContentCandidateProvider`/`RepositoryCloner`/`extract_content`/`ContentWriter` collaborators (dict/list-driven, call-recording fakes mirroring `tests/test_pipeline.py`'s established style) — no real network, git, or PostgreSQL. `RepositoryContentCandidateProvider`'s own SQL is tested separately against a minimal fake `psycopg` connection (mirroring `tests/test_content_storage.py`'s `FakeConnection` pattern), since it is genuinely new read-only SQL, not orchestration logic.

| Class | Tests | Covers |
|---|---|---|
| `TestBasicRun` | 4 | Empty candidate set; one success; multiple successes; empty extraction is a success (`files_persisted=0`) |
| `TestFailureIsolation` | 9 | Clone failure isolated + batch continues; extraction failure isolated; persistence failure isolated; unexpected exception (from clone/extract stage and from the writer stage) isolated and logged; all repositories failing; mixed success/failure order preserved; no downstream calls after a clone failure; candidate-provider failure propagates uncaught |
| `TestStatisticsInvariant` | 1 | `attempted == succeeded + failed` |
| `TestLogging` | 2 | Start/success/completion events logged; repository content itself is never logged |
| `TestCandidateProvider` | 4 | Returns `(id, full_name)` pairs; query is unfiltered (`repository_content` absent from SQL, no `JOIN`, no `NOT EXISTS`); connection always closed; empty table handled |

**All 20 pass.**

---

## 10. Live Verification

Ran the **real** `ContentAcquisitionPipeline` composed with the **real** `RepositoryCloner` (1.4.a), `extract_content` (1.4.b), and `ContentWriter` (1.4.c) — no fakes — against a small, hand-picked 3-candidate batch: two real Checkpoint 1.3 corpus repositories (`vinta/awesome-python`, `public-apis/public-apis`) and one deliberately nonexistent `full_name` (`octocat/this-repo-does-not-exist-xyz123`, paired with a real, valid `repository_id` borrowed from another corpus entry solely to exercise failure reporting — no dummy database row was created), placed in the **middle** of the batch to prove continuation. Driver script was throwaway (not delivered code, deleted after evidence capture, matching 1.3.b/1.4.a/1.4.b/1.4.c precedent).

### 10.1 End-to-end run with an intentional mid-batch failure

```
stats: ContentAcquisitionStats(candidates_selected=3, attempted=3, succeeded=2, failed=1)
processed: [('vinta/awesome-python', 2), ('public-apis/public-apis', 1)]
failures: [('octocat/this-repo-does-not-exist-xyz123', 'clone', 'RepositoryCloneError')]
ASSERTIONS PASSED: failure isolated, batch continued, both successes recorded
```

The real `git clone` failure (`fatal: repository ... not found`, exit code 128) was correctly wrapped as `RepositoryCloneError`, recorded with `stage="clone"`, and — critically — **the third candidate (`public-apis/public-apis`), positioned after the failure, was still processed successfully**, directly proving one repository's failure does not abort the batch.

### 10.2 Cleanup verification

```
temp ghia_clone_* dirs before run: []
temp ghia_clone_* dirs after run: []
ASSERTIONS PASSED: no leftover clone directories
```

Checked via direct OS temp-directory listing, not the driver script's own claim.

### 10.3 Independent SQL verification

```
vinta/awesome-python: 2 rows
  pyproject.toml: hash_ok=True size_ok=True
  README.md: hash_ok=True size_ok=True
public-apis/public-apis: 1 rows
  README.md: hash_ok=True size_ok=True
ALL INDEPENDENT CHECKS PASSED: True
```

Every hash and byte size was recomputed independently (`hashlib.sha256` over the persisted `content` column, read directly via `psycopg`) and cross-checked against the stored `content_hash`/`content_size_bytes` — not trusting `ContentWriter`'s or the pipeline's own return values.

### 10.4 Rerun — idempotency through `ContentWriter`

```
rerun stats: ContentAcquisitionStats(candidates_selected=2, attempted=2, succeeded=2, failed=0)
  pyproject.toml: id_unchanged=True fetched_at_moved=True updated_at_unchanged=True
  README.md: id_unchanged=True fetched_at_moved=True updated_at_unchanged=True
  README.md: id_unchanged=True fetched_at_moved=True updated_at_unchanged=True
IDEMPOTENCY CHECKS PASSED: True
temp ghia_clone_* dirs after rerun: []
```

Re-running the pipeline against the same two successful candidates (a fresh `ContentAcquisitionPipeline` instance, real clone → extract → persist again) confirmed: same `repository_content.id` per file (no duplicate rows), `fetched_at` moved forward, `updated_at` preserved — exactly 1.4.c's already-verified idempotency behavior, now demonstrated end-to-end through the orchestrator rather than by calling `ContentWriter` directly. No leftover clone directories after the rerun either.

### 10.5 Real `RepositoryContentCandidateProvider` against the full corpus

```
candidate_provider_completed candidates=100
total candidates: 100
sample: [(UUID('01e3d0bb-...'), 'langflow-ai/langflow'), ...]
```

Confirmed the real provider returns all 100 Checkpoint 1.3 corpus repositories, unfiltered — matching the approved design exactly (this call only *listed* candidates; it did not run the full batch, consistent with your instruction that 1.4.d is not the full-corpus run).

---

## 11. Environmental Note

Unlike Checkpoint 1.4.c's live verification (where Docker Desktop had just cold-started and individual `psycopg.connect()` calls took minutes), Docker had already been running for several hours before this checkpoint's live verification began, and a direct connection-latency check confirmed a stable ~5-second connection time throughout — consistent with ordinary localhost TCP/IPv6 dual-stack resolution overhead on this development machine, not a recurrence of the prior cold-start condition. No anomaly was observed or needed diagnosis during this checkpoint's live run.

---

## 12. Defects Discovered/Fixed

**None.** No defect was found in `acquisition/content_pipeline.py`, `RepositoryContentCandidateProvider`, or any prior-checkpoint component during design, testing, or live verification.

---

## 13. Security Verification

- Grepped `acquisition/content_pipeline.py` and `tests/test_content_pipeline.py` for `GITHUB_TOKEN`, `github_pat_`, `ghp_`, `POSTGRES_PASSWORD`, and `repo_password` — zero matches.
- `RepositoryContentCandidateProvider`'s SQL uses no parameters at all (a fixed, literal `SELECT` with no user input interpolated), and `ContentAcquisitionPipeline` passes no new SQL of its own — all persistence SQL remains exclusively inside `ContentWriter` (1.4.c), unmodified.
- No repository content, tokens, passwords, or large payloads are logged — confirmed by inspection and by `TestLogging::test_repository_content_never_logged`.
- The live-verification driver script lived only in the session's scratchpad directory (outside the git repository) and was never staged or committed.
- No leftover clone directories remained after either live run.
- `git diff`/`git status` confirmed no scratch files, unrelated changes, or credentials were staged.

---

## 14. Known Limitations

- **No batch-size/pagination control** — `RepositoryContentCandidateProvider` returns every row in `repositories` in one call; at V1's 100-repository scale this is appropriate and matches the roadmap's stated scope, but a much larger corpus would need this reconsidered (not this checkpoint's job).
- **No orchestration-level "already processed" tracking** — a deliberate design decision (§2), not a gap: resumability is achieved by `ContentWriter`'s own idempotency, and re-running the full batch is the resume mechanism, exactly mirroring 1.3.c's established precedent.
- **`RepositoryContentCandidateProvider` opens its own connection per call**, matching `RepositoryWriter`/`ContentWriter`'s established one-connection-per-call precedent.

---

## 15. Definition of Done

| Criterion | Status |
|---|---|
| Orchestrates 1.4.a → 1.4.b → 1.4.c without duplicating their logic | ✅ §3, confirmed by inspection |
| `RepositoryContentCandidateProvider` — unfiltered `SELECT id, full_name FROM repositories ORDER BY id`, no `skip_existing` | ✅ §2, §9 (`TestCandidateProvider`), §10.5 (live, 100/100 corpus rows) |
| Per-repository failure isolation (clone/extract/persist/unexpected) | ✅ §5, §9 (`TestFailureIsolation`), §10.1 (live) |
| Unexpected exceptions never silently swallowed | ✅ §5, `log.exception`, `TestFailureIsolation::test_unexpected_exception_recorded_and_batch_continues` |
| Empty extraction is a success, not a failure | ✅ §5, `TestBasicRun::test_empty_extraction_is_a_success_not_a_failure` |
| Cleanup guaranteed via `RepositoryCloner`'s existing contract, no new cleanup logic | ✅ §6, §10.2 (live, zero leftover dirs) |
| No new/persistent Git cache introduced | ✅ Confirmed by inspection — `RepositoryCloner` used exactly as 1.4.a established it |
| Statistics invariant (`attempted == succeeded + failed`) | ✅ §7, `TestStatisticsInvariant` |
| Deterministic tests, no live dependency in normal execution | ✅ §9, 20/20 pass |
| Live end-to-end run with real 1.4.a/1.4.b/1.4.c components | ✅ §10.1–§10.4 |
| Independent SQL verification | ✅ §10.3 |
| Rerun idempotency demonstrated through the orchestrator | ✅ §10.4 |
| No schema/migration change | ✅ Confirmed — no `alembic/versions/` file added or modified |
| 1.4.a/1.4.b/1.4.c untouched | ✅ §8, confirmed by `git diff` |
| Security verification | ✅ §13 |
| Full regression suite green | ✅ 258 passed, 4 skipped, 0 failed |
| No progression to 1.4.e | ✅ Confirmed — no full-corpus run, no new orchestration beyond this checkpoint's scope |

**Sub-checkpoint 1.4.d is COMPLETE and VERIFIED.**

---

## 16. Git

**Note on commit history:** an automatic snapshot-commit mechanism operating on this repository committed and pushed this checkpoint's code changes (`acquisition/content_pipeline.py`, `acquisition/__init__.py`, `tests/test_content_pipeline.py`) under the generic message `"Acquisition Pipeline"` (commit `300897a`) before this checkpoint's intentional milestone commit could be made — consistent with your standing instruction that automatic snapshot commits must not substitute for an explicit milestone commit. The code changes in `300897a` were independently verified (via `git diff bc88288..300897a --stat`) to contain **only** the three intended 1.4.d files, with no scratch files or unrelated changes. This checkpoint's own intentional commit therefore contains this report (`CHECKPOINT_1_4D_REPORT.md`) as the remaining uncommitted change at the time of finalization.

- **Commit hash:** *(recorded after commit — see final message)*
- **Commit message:** `Checkpoint 1.4.d: Content acquisition orchestration and cleanup`
- **Push status:** *(recorded after push — see final message)*

---

## 17. What This Enables Next

1.4.e (the full 100-repository corpus run) can now be executed directly against `ContentAcquisitionPipeline` composed with the real `RepositoryContentCandidateProvider`, `RepositoryCloner`, and `ContentWriter` — no further orchestration code is needed.

**Not started. Awaiting review before proceeding to 1.4.e.**
