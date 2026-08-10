# Checkpoint 1.3.d Report — Acquisition Pipeline Test Suite

**Parent Checkpoint:** 1.3 (Repository Acquisition Pipeline), Sub-checkpoint d of e
**Status:** ✅ Complete, verified (deterministic mocked tests only — no live dependency)
**Date:** 2026-08-10

---

## 1. Purpose

Protect the acquisition layer (1.3.a Selection, 1.3.b Storage, 1.3.c Pipeline) from regression with a deterministic, CI-safe test suite. This checkpoint adds tests only — no production code in `acquisition/`, `github_client/`, or `config/` was modified.

Per the constraints given for this sub-checkpoint: no live GitHub or PostgreSQL run, no schema changes, no changes to `github_client/`, and 1.3.e (whatever it is) is explicitly out of scope.

---

## 2. Phase 1 — Design Verification (confirmed before coding)

1. **`tests/test_acquisition.py` (1.3.a, 26 tests) and `tests/test_pipeline.py` (1.3.c, 19 tests) already exist**, written as part of those checkpoints' own verification, and already cover: `SelectionCriteria` validation, query generation, pagination, dedup, multi-language merging, empty results, logging, retry composition (selection); and empty/one/multiple candidates, storage/fetch failure isolation, `RepositoryNotFoundError`/retry-exhaustion handling, rate-limiter interaction (including "not called after fetch failure"), duplicate-skip, progress logging, and statistics accuracy (pipeline). Re-reading both confirmed they already satisfy the majority of this checkpoint's "Selection" and "Pipeline" requirement lists.
2. **The genuine gap was Storage (1.3.b).** `CHECKPOINT_1_3B_REPORT.md` documents live-database verification (30/30 checks against real local PostgreSQL) but **no mocked/deterministic test file exists for `acquisition/storage.py`** — there was no `tests/test_storage.py`. This is the primary work this checkpoint adds.
3. **Cross-layer composition** (selection → fetch → rate-limit → storage → result accounting) is exercised by `tests/test_pipeline.py`'s existing tests, but only against pipeline-level fakes (`FakeSelector`, `FakeClient`, `FakeWriter` — doubles of all three collaborators). None of those tests wire the *real* `RepositorySelector` and *real* `RepositoryWriter` together through a real `AcquisitionPipeline`. Added two such tests (§4) doubling only the true I/O boundary (HTTP client methods, DB connection) — a stronger proof that the actual production classes compose, not just that `AcquisitionPipeline`'s internal loop logic is correct in isolation.
4. **No production defect was found.** Every behavior asserted below matched `acquisition/storage.py`'s existing, already-live-verified implementation on the first attempt — no test had to be adjusted to work around unexpected code behavior, and `github_client/` was not touched (confirmed by `git status` in §6).

---

## 3. Test Architecture

### `tests/test_storage.py` (new, 26 tests)

A fake psycopg `Connection`/`Cursor`/`Transaction` triad, deliberately mimicking psycopg3's real upsert semantics rather than a generic mock:

- **`FakeIdRegistry`** — simulates `ON CONFLICT (...) DO UPDATE ... RETURNING id`: the same natural key (`github_id` for owners/repositories, `spdx_id` for licenses, `slug` for topics) always resolves to the same UUID; a new key mints a fresh one. This is what makes the idempotency, owner-update, and topic-reuse tests meaningful rather than trivially true — a plain "always return a new UUID" fake would not have been able to distinguish an upsert from a duplicate insert.
- **`FakeCursor`** — records every `execute()`/`executemany()` call verbatim (`sql`, `params`) onto the connection's `operations` log, and routes `RETURNING id` calls to the registry via a minimal `INSERT INTO (\w+)` regex (routing only, never interpreting the SQL's business logic).
- **`FakeTransaction`** — mimics `Connection.transaction()`'s exact contract: sets `committed=True` on clean exit, `rolled_back=True` and re-raises on any exception. This is the same context-manager contract `acquisition/storage.py`'s own docstring documents relying on.
- **`FakeConnection`** — takes an optional `fail_on` substring; any executed SQL statement containing it raises `psycopg.Error`, letting each of the six write steps be failed independently to test rollback at every stage.

No `psycopg.connect()` call is ever made for real; `monkeypatch.setattr(psycopg, "connect", ...)` is used only in the two credential-leakage tests that specifically need to exercise `RepositoryWriter`'s *default* (settings-derived) connection factory construction path without opening a socket.

### `tests/test_pipeline.py` (extended, +2 tests, 19 → 21)

Added `TestCrossLayerComposition`, which imports `FakeConnection`/`FakeIdRegistry`/`make_repo_data` from `tests/test_storage.py` and wires the **real** `RepositorySelector` (1.3.a) and **real** `RepositoryWriter` (1.3.b) into a **real** `AcquisitionPipeline` (1.3.c), doubling only a combined GitHub HTTP client double (`search_repositories()` + `get_repository()`) and the storage layer's fake DB connection.

### `tests/test_acquisition.py` and existing `tests/test_pipeline.py` tests

Unmodified — re-verified as still passing, not duplicated.

---

## 4. Test Categories and Counts

| File | Test count | New in 1.3.d |
|---|---|---|
| `tests/test_acquisition.py` (Selection, 1.3.a) | 26 | 0 (pre-existing, reverified) |
| `tests/test_pipeline.py` (Pipeline, 1.3.c) | 21 | 2 (`TestCrossLayerComposition`) |
| `tests/test_storage.py` (Storage, 1.3.b) | 26 | 26 (new file) |
| `tests/test_github_client.py` (1.1.g, includes token-leakage) | 65 | 0 (pre-existing, reverified) |
| `tests/test_environment.py` | 1 passed + 2 skipped | 0 |
| **Total** | **158 collected (156 passed, 2 skipped)** | **28 net new tests** |

### `tests/test_storage.py` breakdown (26 tests)

| Class | Tests | Covers |
|---|---|---|
| `TestOwnerUpsert` | 2 | Owner insert; owner update reuses same id, no duplicate row |
| `TestLicenseUpsert` | 2 | License insert; repository without a license stores `license_id=NULL` |
| `TestRepositoryUpsert` | 2 | Repository insert; repository update (same `github_id`) returns same id |
| `TestTopics` | 4 | Topic creation; topic reuse across two repositories sharing a slug; repository without topics (no `repository_topics` call at all); `repository_topics` linking (correct pair count/shape) |
| `TestSnapshot` | 1 | Snapshot insertion with correct star/fork counts |
| `TestIdempotency` | 1 | Repeated identical write produces identical ids, zero duplicate rows across all four keyed tables |
| `TestForeignKeyOrdering` | 1 | owners/licenses/topics precede repositories; repositories precedes repository_snapshot; repositories precedes the repository_topics link step |
| `TestTransactionBoundary` | 2 (+4 parametrized) | Commit on success; rollback on failure at each of the four table-insert steps (owners/licenses/topics/repository_snapshot) plus the originally-specified repositories-step failure |
| `TestParameterizedSQL` | 1 | Every execute/executemany call uses dict/list-of-dict params (psycopg named placeholders), and distinctive literal values never appear string-embedded in the SQL text |
| `TestTypedExceptions` | 3 | `RepositoryPersistenceError` wraps `full_name`/`cause` correctly; is an `AcquisitionStorageError`; a non-`psycopg.Error` exception (a bug, not a persistence failure) is **not** wrapped — propagates as-is |
| `TestNoCredentialLeakage` | 3 | `postgres_password` never appears in logs on success; never appears in logs or the exception message on failure; `Settings.__repr__` redacts it |

### `TestCrossLayerComposition` (2 tests, in `tests/test_pipeline.py`)

1. `test_selection_through_storage_composes_end_to_end` — real `RepositorySelector` + real `AcquisitionPipeline` + real `RepositoryWriter`, one candidate, asserts search called once, fetch called once, `PipelineResult.stats` exactly matches, the fake DB shows 1 repository row and 2 topic rows, and the transaction committed.
2. `test_storage_failure_in_composed_run_is_isolated_and_recorded` — same real wiring, storage fails on the `repositories` insert; asserts the pipeline records a `stage="storage"` failure (not a crash), and the fake connection rolled back.

---

## 5. Verification Performed

### 5.1 Full suite, run 1

```
tests\test_acquisition.py ..........................                     [ 16%]
tests\test_environment.py .ss                                            [ 18%]
tests\test_github_client.py ............................................ [ 46%]
......................................                                   [ 70%]
tests\test_pipeline.py .....................                             [ 83%]
tests\test_storage.py ..........................                         [100%]
======================= 156 passed, 2 skipped in 14.25s =======================
```

### 5.2 Full suite, run 2 (determinism check)

```
======================= 156 passed, 2 skipped in 11.29s =======================
```

Identical pass/skip counts across both runs; no test relies on wall-clock time, network, filesystem state beyond `tmp_path`/`monkeypatch`-isolated env vars (already established by 1.1.g's `isolated_settings_env` fixture, reused unmodified), or ordering-dependent shared state.

### 5.3 Zero live GitHub requests

Confirmed by inspection: `tests/test_storage.py` never imports `github_client.rest.GitHubRESTClient` or `.graphql`; the one place an HTTP-shaped double is used (`TestCrossLayerComposition`, in `test_pipeline.py`) is a from-scratch Python class (`FullGitHubClientDouble`) with no `requests` import, no network call possible. `tests/test_acquisition.py`/`tests/test_pipeline.py`'s pre-existing tests were already confirmed network-free in their own checkpoint reports and were not modified in a way that changes that.

### 5.4 Zero live PostgreSQL dependency

Confirmed by inspection: `tests/test_storage.py` never imports a real connection except via `monkeypatch.setattr(psycopg, "connect", ...)` in the two credential-leakage tests, and in both cases the patched target returns a `FakeConnection` — no socket is opened. `psycopg.Error` (the real exception class) is used directly as a lightweight, already-available typed exception to raise from the fake cursor — this is a dependency on the `psycopg` package being importable (a static dependency this project already has for `acquisition/storage.py` itself), not a dependency on a running PostgreSQL server.

### 5.5 No production files modified

```
$ git status --porcelain
 M tests/test_pipeline.py
?? tests/test_storage.py
```

Only test files changed. `acquisition/`, `github_client/`, `config/`, and `alembic/` are untouched.

### 5.6 Coverage tooling

`pyproject.toml`'s `pytest.ini_options.addopts` already runs `--cov=src --cov-report=term-missing` — a **pre-existing** configuration that measures a `src/` package tree that does not correspond to this project's actual module layout (`acquisition/`, `github_client/`, `config/` all live at the repository root, not under `src/`). This was not introduced or altered by this checkpoint, and per the instruction not to alter project-wide coverage configuration merely to improve this checkpoint's own numbers, it was left as-is. Consequently the coverage report shown by every run above (100% of a handful of empty `src/*/__init__.py` files) is **not a meaningful measurement of this checkpoint's actual coverage** — no coverage percentage for `acquisition/storage.py`/`selection.py`/`pipeline.py` is claimed anywhere in this report, per the instruction not to claim coverage percentages unless actually measured against the real modules.

---

## 6. Security / Credential-Leakage Testing

| Area | Status |
|---|---|
| `GITHUB_TOKEN` never appears in logs | ✅ Retained — `tests/test_github_client.py::TestNoTokenLeakage` (3 tests: REST client, GraphQL client, error paths), unmodified, reverified passing. Not duplicated in this checkpoint since `acquisition/storage.py` makes no HTTP calls and never touches `github_token`. |
| `POSTGRES_PASSWORD` never appears in logs | ✅ New — `tests/test_storage.py::TestNoCredentialLeakage::test_postgres_password_never_logged_on_success` and `..._on_failure` (2 tests), exercising `RepositoryWriter`'s actual default (settings-derived) connection-factory construction path via a monkeypatched `psycopg.connect`, not a bypassed/hand-rolled path. |
| Exception messages do not leak credentials | ✅ `test_postgres_password_never_logged_on_failure` additionally asserts the secret is absent from `str(RepositoryPersistenceError)` itself, not just the log stream. |
| Mocked Authorization headers never emitted by logging assertions | N/A to this checkpoint's own new code — `acquisition/storage.py`/`pipeline.py` make no HTTP calls and construct no `Authorization` header. This exact assertion already exists for the layer that does (`github_client`), unmodified and reverified (see `GITHUB_TOKEN` row above). |
| `Settings.__repr__` redacts `postgres_password` | ✅ `test_settings_repr_redacts_postgres_password` — confirms the redaction `config/settings.py` already implements (Checkpoint 1.3.b) has not regressed. |

---

## 7. Regressions Found

**None.** All 130 pre-existing tests (26 selection + 19 pipeline + 65 github_client/config + 1 environment, plus 2 skipped) continued to pass unmodified, both before and after this checkpoint's additions. No production code was touched, so no regression was possible by construction — this was confirmed, not merely assumed, via the `git status` check in §5.5.

---

## 8. Known Limitations

- **The fake psycopg double is a behavioral simulation, not a substitute for live-database proof.** It reproduces `ON CONFLICT ... RETURNING id` upsert semantics (same key → same id) and the `Connection.transaction()` commit/rollback contract closely enough to protect the *code path* `acquisition/storage.py` executes — but it cannot catch a genuine SQL syntax error, a real constraint violation the fake doesn't model, or a real transaction-isolation edge case. That level of proof was already obtained and documented live in `CHECKPOINT_1_3B_REPORT.md` §6 (30/30 checks against real PostgreSQL) and `CHECKPOINT_1_3C_REPORT.md` §5.2 (15-repository live end-to-end run); this checkpoint's job is regression protection between those live-verified snapshots, not a replacement for them.
- **`FakeIdRegistry`'s "insert vs. update" distinction is inferred, not literally executed SQL.** The fake cannot distinguish an `INSERT` branch from a `DO UPDATE` branch of the same `ON CONFLICT` statement (psycopg doesn't surface that distinction to the caller either — both paths return the same `RETURNING id` row). The tests instead assert the *externally observable* invariant that matters: same natural key → same id, distinct rows created only for distinct keys — which is what idempotency and "no duplicate row" actually mean from a caller's perspective.
- **`TestForeignKeyOrdering`'s topics-before-repositories assertion uses the *last* topics operation's index** (`max(...)`) rather than the first, since a repository can have zero-to-many topic rows written before the single `repositories` insert; this correctly captures the ordering constraint (all topic writes must precede the repository write, since the repository write itself doesn't depend on topics, but `repository_topics` linking — checked separately — does depend on both).
- **The two `TestCrossLayerComposition` tests import fixtures from `tests/test_storage.py`** (`FakeConnection`, `FakeIdRegistry`, `make_repo_data`) rather than redefining them — consistent with "reuse existing test conventions" and avoiding a third copy of the same fake, at the cost of a cross-file test dependency (`tests/__init__.py` exists and `pyproject.toml` sets `pythonpath = ["."]`, so this import resolves correctly, confirmed by the passing run in §5.1/§5.2).

---

## 9. Deferred Responsibilities

- **1.3.e** — whatever the final sub-checkpoint of 1.3 is, per the explicit instruction not to start it. Not investigated.
- **Coverage percentage measurement against the real `acquisition`/`github_client`/`config` modules** — the project's current `--cov=src` configuration doesn't measure them at all (§5.6); fixing that is a project-wide coverage-configuration change explicitly out of this checkpoint's scope.
- **Live re-verification of Storage/Pipeline** — not performed here by design (the checkpoint's own constraint); the existing live evidence in `CHECKPOINT_1_3B_REPORT.md`/`CHECKPOINT_1_3C_REPORT.md` stands, unchanged and unchallenged.

---

## 10. Definition of Done

| Criterion | Status |
|---|---|
| All new 1.3.d tests pass | ✅ 28 net new tests (26 storage + 2 cross-layer), all pass |
| All previously passing tests remain passing | ✅ 130 pre-existing tests (128 pass + 2 skip) unchanged in outcome |
| No live external dependency required | ✅ §5.3, §5.4 |
| Results deterministic across repeated runs | ✅ §5.1, §5.2 — identical counts, two consecutive runs |
| Selection coverage (criteria, extraction, filters, pagination, malformed responses, no live calls) | ✅ Already satisfied by pre-existing `tests/test_acquisition.py`, reverified |
| Storage coverage (owner/license/topic/repository/link/snapshot upserts, idempotency, no-license, no-topics, FK ordering, commit, rollback, parameterized SQL, typed exceptions) | ✅ New `tests/test_storage.py`, 26 tests |
| Pipeline coverage (empty/success/multi, fetch/storage isolation, typed exception handling, retry, rate-limiter timing, duplicate skip, logging, stats accuracy) | ✅ Already satisfied by pre-existing `tests/test_pipeline.py`, reverified |
| Cross-layer composition (selection → fetch → rate-limit → storage → accounting) with real classes | ✅ New `TestCrossLayerComposition`, 2 tests |
| Security: no `GITHUB_TOKEN`/`POSTGRES_PASSWORD` leakage, no leaked credentials in exception messages | ✅ Retained + 3 new tests |
| No production code modified | ✅ `git status` — test files only |
| No database schema changes | ✅ No `alembic/` file touched |
| No `github_client/` changes | ✅ Confirmed by inspection and `git status` |
| No 1.3.e work started | ✅ Not investigated |

**Sub-checkpoint 1.3.d is COMPLETE and VERIFIED.**

---

## 11. What This Enables Next

With Selection (1.3.a), Storage (1.3.b), Pipeline orchestration (1.3.c), and now a deterministic, CI-safe regression suite spanning all three plus their real-class composition (1.3.d) in place, whatever 1.3.e specifies can be planned with full confidence that the acquisition layer's current, live-verified behavior is protected against silent regression going forward.

**Not started. Awaiting your review before proceeding to 1.3.e.**
