# Checkpoint 1.1.g Report — GitHub Client Test Suite

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint g of h
**Status:** ✅ Complete, verified (deterministic, no live API dependency)
**Date:** 2026-08-06

---

## 1. Purpose

Provide a committed, repeatable regression suite for `github_client/` (1.1.c–1.1.f) and `config/settings.py` (1.1.a), so the live, ad hoc verification performed and documented in each prior checkpoint's report is protected against silent regression going forward. `IMPLEMENTATION_ROADMAP.md`'s Checkpoint 1.1 Definition of Done lists "Tests for API client" as its own line item, distinct from the client implementations themselves — this sub-checkpoint satisfies it.

This does not replace or repeat the live verification in `CHECKPOINT_1_1{A,C,D,E,F}_REPORT.md` — those results stand as-is. It adds a second, faster, network-independent layer on top.

---

## 2. Design Verification (Phase 1)

**Why a dedicated test suite exists:** Every prior sub-checkpoint (1.1.a–1.1.f) was verified live and by hand, then never re-run automatically. A regression introduced later — e.g. a typo in `retry.py`'s backoff formula, or a broken branch in `graphql.py`'s `NOT_FOUND` handling — would have no repeatable check to catch it.

**Why tests are their own sub-checkpoint:** Each of 1.1.c–1.1.f was deliberately scoped to one module at a time. A cross-module regression suite naturally comes after all the pieces (REST, rate limiter, retry, GraphQL) exist together, rather than being anticipated piecemeal inside each narrower sub-checkpoint.

**Behaviours protected (regression-critical, now covered):**
- `config.settings`: required/whitespace-only token validation, log-level validation and normalization, singleton caching, `repr()` redaction.
- `github_client.exceptions`: class hierarchy, message content, exposed fields (`owner`/`repo`, `reset_at`, `status_code`).
- `github_client.rate_limiter`: header parsing (all fields, missing-header, default resource), exhaustion boundary (inclusive at the safety margin), wait/no-wait/`None`-input behavior, constructor validation.
- `github_client.retry`: exponential-backoff formula (with and without jitter), retryable-vs-non-retryable classification for all four named exception types plus an unexpected `ValueError`, max-attempts enforcement, `with_retry` argument pass-through, constructor validation.
- `github_client.rest.GitHubRESTClient`: success parsing, all five exception-classification branches (404/401/403-exhausted/403-other/other-status), malformed/missing-field responses, network failures, request headers (Bearer token, `Accept`, `X-GitHub-Api-Version`), absent rate-limit headers.
- `github_client.graphql.GitHubGraphQLClient`: success parsing, point-based rate-limit parsing (`resource="graphql"`, `used = limit - remaining`), the `NOT_FOUND`-vs-other-GraphQL-error split (1.1.f §4's design decision), 401/other-status/network-failure/malformed-JSON/missing-`data`-and-`errors` handling, request shape (endpoint, variables, headers).
- Cross-cutting: `with_retry` composed around both clients (recovers from a transient failure, does not retry a 404), a `RateLimitInfo` from either client accepted by the same `RateLimiter`.
- No token leakage across success paths, error paths, and captured log output for both clients.

**Mocked vs. live:** Every test in this suite uses one of two hand-written doubles — `FakeResponse` (fixed `status_code`/`json()`/`headers`) and `FakeSession` (records calls, returns a fixed response or raises a fixed exception) — standing in for `requests.Session`. No test constructs a real `requests.Session()` or reaches the network. This mirrors the fake-session style already used in the 1.1.e/1.1.f ad hoc verification scripts, just made permanent and deterministic (`sleep_fn`/`random_fn`/`time_fn` are injected everywhere timing or randomness would otherwise appear).

Live verification (real GitHub API, real token, real rate-limit headers/body) remains exactly what 1.1.c/1.1.d/1.1.e/1.1.f's reports already recorded — not re-run here, and not required to be, since this suite's job is regression protection, not initial proof of connectivity.

**What remains deferred:** CI wiring (explicitly out of scope per your instructions — Checkpoint 0.7 owns that), integration/e2e tests against the live API, any database-backed testing (no DB code exists in `github_client` yet), and Checkpoint 1.3's acquisition-pipeline tests.

No ambiguity encountered; proceeded directly to implementation.

---

## 3. Files Created

| File | Purpose |
|---|---|
| `tests/test_github_client.py` | The full suite — 67 tests across 9 test classes |

No production code was modified — no defect was discovered during implementation or verification (see §6).

---

## 4. Testing Strategy

- **Framework:** `pytest`, using the project's existing `pyproject.toml` configuration (`testpaths = ["tests"]`) — no new pytest plugins, no new dependencies. `pytest`/`pytest-cov` were already pinned in `requirements.txt` from Checkpoint 0.1.
- **Isolation from the real environment:** `config.settings` tests use a fixture (`isolated_settings_env`) that repoints `config.settings._ENV_FILE` at a nonexistent path and clears `GITHUB_TOKEN`/`LOG_LEVEL` from `os.environ` via `monkeypatch`, so they never read the project's real `.env`. Every `github_client` test constructs `Settings` directly with a fixed dummy token (`make_settings()`) and passes a fake session — none of them call `get_settings()` or touch the real `.env` either.
- **Determinism:** every `RetryPolicy`/`RateLimiter` instantiated in a test injects `sleep_fn`, `random_fn`, and/or `time_fn` — no test sleeps for real or depends on wall-clock time or `random`'s default seed.
- **No live network:** `FakeSession`/`FakeResponse` (defined at the top of the file) stand in for `requests.Session`/`requests.Response` everywhere. Confirmed by inspection — the string `requests.Session(` does not appear anywhere in the test file outside of the `requests.ConnectionError` exception class used to simulate network failure.

---

## 5. Test Coverage

| Area | Test class | # tests |
|---|---|---|
| `config.settings` (1.1.a) | `TestSettings` | 7 |
| `github_client.exceptions` | `TestExceptions` | 5 |
| `github_client.rate_limiter` (1.1.d) | `TestRateLimitInfo`, `TestRateLimiter` | 11 |
| `github_client.retry` (1.1.e) | `TestRetryPolicy` | 12 |
| `github_client.rest` (1.1.c) | `TestGitHubRESTClient` | 10 |
| `github_client.graphql` (1.1.f) | `TestGitHubGraphQLClient` | 12 |
| Cross-cutting (retry + rate limiter composed around both clients) | `TestClientRetryComposition` | 4 |
| No token leakage | `TestNoTokenLeakage` | 3 |
| Environment smoke tests (pre-existing, Checkpoint 0.1) | `test_environment.py` | 3 (unchanged) |

**67 new tests, all passing.** Both success and failure paths are covered for each client: for REST, all five HTTP-status branches plus malformed-field and network-failure paths; for GraphQL, the `NOT_FOUND` split, non-`NOT_FOUND` GraphQL errors, 401/other-status/network-failure/malformed-JSON/missing-`data` paths, and point-based rate-limit math.

---

## 6. Verification Performed

| # | Check | Result |
|---|---|---|
| 1 | `pytest tests/test_github_client.py -v` | ✅ 67/67 passed |
| 2 | `pytest tests/` (full suite, including pre-existing `test_environment.py`) | ✅ 68 passed, 2 skipped (Postgres/Redis connectivity — expected, unchanged pre-existing skip behavior, no Docker services running in this session) |
| 3 | Ran the new suite twice in succession | ✅ Identical result both times (67 passed, no flakiness) |
| 4 | Grep for `requests.Session(` (a real, network-capable session) anywhere in the test file | ✅ Zero matches — only `FakeSession`/`FakeResponse` construct request/response doubles |
| 5 | `config.settings` tests run with `GITHUB_TOKEN`/`LOG_LEVEL` cleared and `_ENV_FILE` repointed at a nonexistent path | ✅ Fully isolated from the real `.env`; no test in the suite depends on a real `GITHUB_TOKEN` being set |
| 6 | Token-leakage checks (`TestNoTokenLeakage`, 3 tests) — captured `caplog` output across REST/GraphQL success and error paths | ✅ Dummy token value and `"Bearer <token>"` string absent from all captured log text |
| 7 | Regression check against 1.1.c–1.1.f's documented behavior (parsing, exception classification, rate-limit math, retry formula) | ✅ All matched exactly — no discrepancy found |

No production defect was discovered. `github_client/`, `config/settings.py`, and `logging_setup.py` were not modified.

---

## 7. Known Limitations

1. **`--cov=src` in `pyproject.toml`'s `addopts`** measures coverage against `src/`, which doesn't contain `github_client/` or `config/` (those live at repo root, per 1.1.a/1.1.c's documented location deviations). Coverage percentage reported by a default `pytest` run will not reflect this suite's actual coverage of `github_client/`. Not fixed here — changing `addopts` is a project-wide pytest-configuration change outside this sub-checkpoint's scope (and outside "do not build CI" / "do not modify production code" instructions); flagged for whoever next touches `pyproject.toml` or does Checkpoint 0.3's real `src/` restructuring.
2. **Live connectivity is not re-verified by this suite.** By design (see §2) — this suite protects already-verified behavior from regressing, it does not re-prove GitHub API connectivity. If GitHub's API shape changes, this suite would not catch that; only the live verification from 1.1.c–1.1.f (and any future live smoke test) would.
3. **`logging_setup.py`'s own behavior (handler configuration, format string) is not directly tested here** — it was already fully verified in `CHECKPOINT_1_1B_REPORT.md`. This suite only exercises it indirectly, through `caplog`, to confirm the *content* of what `github_client` logs (no token) — not the logger's own formatting/handler mechanics.

---

## 8. Deferred Responsibilities

- CI wiring (Checkpoint 0.7) — explicitly excluded per your instructions.
- Integration/e2e tests hitting the live GitHub API — not built; `tests/e2e/` and `tests/integration/` remain empty placeholders from Checkpoint 0.1.
- `pyproject.toml` `addopts` correction (`--cov=src` → include `github_client`/`config`) — flagged in §7, not made.
- Checkpoint 1.3's acquisition-pipeline tests (batching, storage, orchestration-level retry/rate-limit composition) — out of scope until that pipeline exists.

---

## 9. Definition of Done

| Criterion | Status |
|---|---|
| `tests/test_github_client.py` created | ✅ |
| Covers REST client | ✅ 10 tests, all status/error branches |
| Covers GraphQL client | ✅ 12 tests, all status/error branches including the `NOT_FOUND` split |
| Covers retry | ✅ 12 tests, formula + classification + constructor validation |
| Covers rate limiter | ✅ 11 tests, header parsing + exhaustion + wait logic |
| Covers configuration | ✅ 7 tests, validation + caching + redaction |
| Covers exception mapping | ✅ 5 tests, hierarchy + fields + messages |
| Mocked HTTP responses used throughout | ✅ `FakeResponse`/`FakeSession`, no real `requests.Session` |
| No CI built | ✅ |
| No integration pipelines built | ✅ |
| No production code modified (no defect found) | ✅ |
| All tests pass | ✅ 67/67 |
| Deterministic execution | ✅ Verified via repeated runs; all timing/randomness injected |
| No live API dependency for normal execution | ✅ Verified by inspection (no real `requests.Session`) and by isolating `config.settings` from the real `.env` |
| Existing live verification remains valid | ✅ Not modified or contradicted; this suite is additive |
| Adequate success/failure path coverage | ✅ Both paths covered for every classified outcome in every module |
| No token leakage | ✅ 3 dedicated tests + inspection |

**Sub-checkpoint 1.1.g is COMPLETE and VERIFIED.**

---

## 10. Status Documentation

Same note as 1.1.d/1.1.e/1.1.f's reports: no project-level status document tracks Checkpoint 1.1's sub-checkpoints individually. No update made; still recommend a single `IMPLEMENTATION_ROADMAP.md` table update once 1.1.h is complete and the full sub-checkpoint chain (a–h) is done.

---

## 11. What This Enables Next

Checkpoint 1.1.h can proceed with a committed regression suite already in place — any change to `github_client/` or `config/settings.py` from this point forward can be checked against `pytest tests/test_github_client.py` before being considered safe.

**Not started. Awaiting your review before proceeding to 1.1.h.**
