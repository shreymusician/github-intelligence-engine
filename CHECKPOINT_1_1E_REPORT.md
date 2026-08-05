# Checkpoint 1.1.e Report — Retry & Exponential Backoff

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint e of h
**Status:** ✅ Complete, verified (live + deterministic unit tests)
**Date:** 2026-08-06

---

## 1. Purpose

Retry transient failures — network errors, GitHub 5xx responses, secondary rate-limit 403s — that `github_client/rest.py` (1.1.c) raises as `GitHubAPIError`. Per the approved Checkpoint 1.1 plan (§8, in-conversation), these are unpredictable failures where GitHub gives no advance signal, unlike the primary rate limit (1.1.d's `RateLimiter`, which handles a known, deterministic quota with proactive waiting instead of retry). This sub-checkpoint makes no HTTP requests, no database writes, and does not modify `rest.py`.

---

## 2. Architecture

```
github_client/
├── retry.py         ← RetryPolicy, with_retry() decorator (new, this checkpoint)
├── rate_limiter.py    (unchanged - 1.1.d)
├── rest.py             (unchanged - 1.1.c/d)
├── exceptions.py        (unchanged - 1.1.c)
└── __init__.py            (re-exports updated)
```

`retry.py` depends only on `github_client.exceptions` (for classification) and `logging_setup` (for attempt/exhaustion logging) — no dependency on `rest.py` or `rate_limiter.py`, matching "no HTTP logic inside this module" and "no duplication of logic already implemented elsewhere."

**Not wired into `rest.py`.** Same pattern as 1.1.d: `get_repository()` is unmodified. A caller (Checkpoint 1.3's orchestration, or direct use) wraps it explicitly with `with_retry(policy)`. Verified in §5 by wrapping the real bound method externally rather than editing its source.

---

## 3. Retry Strategy

- **Exponential backoff:** `delay = initial_delay * multiplier ** attempt_index` (0-based attempt index).
- **Jitter — included, not omitted.** The approved plan explicitly specifies "exponential backoff *with jitter*." Implemented as equal-jitter-style: `final_delay = base + random_fn() * jitter_fraction * base`, so the random component is bounded to `[0, jitter_fraction]` of the base delay — the sequence still grows monotonically in expectation while each individual delay is non-deterministic. Default `jitter=0.5` (up to +50% randomness).
- **Configurable knobs (all four required, all implemented):** `max_attempts` (default 5), `initial_delay` (default 1.0s), `multiplier` (default 2.0), `jitter` (default 0.5). All validated eagerly at construction (`ValueError` on nonsensical values — negative delay, `multiplier < 1`, `max_attempts < 1`, `jitter` outside `[0, 1]`).
- **`sleep_fn`/`random_fn` are constructor-injectable**, so every test in §5 ran with zero real sleeping and zero real randomness — fully deterministic.
- **No max-total-wait cap implemented.** The approved plan mentioned one, but Phase 2's explicit requirement list for this sub-checkpoint named only the four knobs above — not adding an unrequested fifth knob, per "do not implement anything outside the scope of 1.1.e." Flagged as a candidate future addition, not a defect (5 attempts with the default multiplier stay naturally bounded: 1s, 2s, 4s, 8s).

---

## 4. Retryable vs. Non-Retryable Failures

| Exception | Retried? | Rationale |
|---|---|---|
| `GitHubAPIError` | **Yes** | Network failures, 5xx, unexpected statuses, secondary rate-limit 403s, and malformed-response errors all currently raise this type (see §7 ambiguity) |
| `RepositoryNotFoundError` (404) | **No** | The repo genuinely isn't there — retrying won't change that |
| `AuthenticationError` (401) | **No** | A bad token won't fix itself |
| `RateLimitExceededError` (primary exhaustion) | **No** | 1.1.d's `RateLimiter` is the correct response — a retry loop would just fail identically up to 4 more times |
| Any other exception type | **No** | Treated as an unexpected bug, not a known transient failure — propagates immediately rather than being silently retried |

---

## 5. Public API

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    initial_delay: float = 1.0
    multiplier: float = 2.0
    jitter: float = 0.5
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random

    def delay_for_attempt(self, attempt_index: int) -> float: ...
    def call(self, func: Callable[[], T]) -> T: ...

def with_retry(policy: RetryPolicy | None = None) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form, for functions taking arbitrary args/kwargs."""
```

---

## 6. Verification Performed

| # | Scenario | Result |
|---|---|---|
| 1 | 401 (`AuthenticationError`) never retried | ✅ 1 call, 0 sleeps |
| 2 | 404 (`RepositoryNotFoundError`) never retried | ✅ 1 call, 0 sleeps |
| 3 | Primary rate-limit exhaustion (`RateLimitExceededError`) never retried | ✅ 1 call, 0 sleeps |
| 4 | Retryable failure (`GitHubAPIError`), fails 3x then succeeds on the 4th attempt | ✅ Exactly 4 calls, result returned correctly |
| 4b | Exponential delay progression, `jitter=0` | ✅ `[1.0, 2.0, 4.0]` exactly, matching `initial_delay * multiplier**n` |
| 5 | `max_attempts` enforced exactly on a permanently-failing call | ✅ 3 calls, 2 sleeps (none wasted after the final failed attempt), final exception re-raised |
| 6 | Jitter upper bound (`random_fn` always returns `1.0`) | ✅ `delay_for_attempt(0) == base * 1.5` exactly (base=2.0, jitter=0.5) |
| 6b | Jitter lower bound (`random_fn` always returns `0.0`) | ✅ `delay_for_attempt(0) == base` exactly, no jitter added |
| 6c | Real randomness stays within `[base, base*1.5]` across 5 attempts | ✅ |
| 7 | Constructor validation (`max_attempts=0`, `initial_delay=-1`, `multiplier<1`, `jitter>1`) | ✅ All 4 rejected with `ValueError` at construction |
| 8 | `with_retry` decorator form, arbitrary positional args pass through | ✅ |
| 9 | No token leakage — `grep` for `token`/`Settings`/`settings.` in `retry.py` | ✅ Zero matches — module structurally cannot touch the token |
| 10 | **Live regression:** successful fetch (`octocat/Hello-World`) unaffected when wrapped with `with_retry` | ✅ 0 retries needed, identical result to 1.1.c/d's verification |
| 10b | **Live regression:** 404 still propagates immediately through the wrapper, not retried | ✅ |
| 11 | **Live end-to-end:** real network failure (unresolvable domain), wrapped with retry | ✅ Retried exactly 3 times with real exponential delays (`0.2s`, `0.4s` — matching `multiplier=2`), then raised `GitHubAPIError` |
| 11b | Token-leakage grep across the full captured log output of the real retry sequence (test 11) | ✅ Zero matches |

14 checks total (counting sub-parts), all passed. Tests 10–11 used the live GitHub API and a real unresolvable domain; the rest used deterministic injected `sleep_fn`/`random_fn`.

---

## 7. Ambiguities Encountered (Flagged in Phase 1, Resolved Without Modifying Prior Checkpoints)

1. **`GitHubAPIError` conflates network failures, malformed responses, and unexpected statuses** under one type, sometimes with `status_code=None` for both network failures and malformed-response cases. Resolved by treating *all* `GitHubAPIError` instances as uniformly retryable — a documented imprecision (a malformed-response error will be retried pointlessly up to `max_attempts` times before giving up), not fixed here since fixing it means modifying `exceptions.py`, outside this sub-checkpoint's authorized scope.
2. **`GitHubAPIError` does not expose GitHub's `Retry-After` header.** `retry.py` applies its own computed exponential+jitter delay uniformly to every retryable failure, including secondary-rate-limit 403s, rather than honoring GitHub's suggested wait time specifically — there's nothing to read that value from without modifying 1.1.c's exception shape.

Both are documented limitations, not defects introduced by this checkpoint — candidates for a future `exceptions.py` refinement (e.g., a distinct `GitHubTransientError` for network/5xx vs. a `GitHubResponseError` for malformed responses, and a `retry_after` field) if retry precision becomes load-bearing later.

---

## 8. Deferred Responsibilities

- Wiring `with_retry` into `rest.py`'s own request path or into a production call site — Checkpoint 1.3's orchestration decides when/how to apply it.
- GraphQL-specific retry semantics — 1.1.f.
- Distinguishing malformed-response from network-failure retryability, and honoring `Retry-After` — requires `exceptions.py` changes, out of scope here (see §7).
- Max-total-wait cap across all attempts — not requested in Phase 2's explicit scope for this sub-checkpoint.

---

## 9. Definition of Done

| Criterion | Status |
|---|---|
| Exponential backoff | ✅ |
| Configurable max attempts | ✅ |
| Configurable initial delay | ✅ |
| Configurable multiplier | ✅ |
| Jitter (explicitly required by the approved design, not omitted) | ✅ |
| Reusable decorator or helper function | ✅ Both: `RetryPolicy.call()` and `with_retry()` decorator |
| No HTTP logic inside this module | ✅ |
| No database code | ✅ |
| No GraphQL code | ✅ |
| Reuses `logging_setup.py` | ✅ |
| Reuses `github_client.exceptions` | ✅ |
| No duplicated logic | ✅ (imports, doesn't redefine, exception classes) |
| Retries occur only for retryable failures | ✅ |
| No retry on 401/404/primary rate-limit exhaustion | ✅ Live + deterministic verified |
| Retry count respected | ✅ |
| Exponential delay progression correct | ✅ |
| Maximum attempts enforced | ✅ |
| Logger integration | ✅ WARNING per attempt, ERROR on exhaustion |
| Deterministic tests via injected sleep/time/random functions | ✅ |
| No token leakage | ✅ |
| Regression: REST client behavior unchanged except for retry support | ✅ Live-verified, zero source changes to `rest.py` |

**Sub-checkpoint 1.1.e is COMPLETE and VERIFIED.**

---

## 10. Status Documentation

Same note as 1.1.d's report: no project-level status document tracks Checkpoint 1.1's sub-checkpoints individually — only these per-sub-checkpoint reports do. No update made; recommend a single `IMPLEMENTATION_ROADMAP.md` update once all of 1.1.a–1.1.h are complete.

---

## 11. What This Enables Next

Checkpoint 1.1.f (GraphQL Client) can reuse `RetryPolicy`/`with_retry` for GraphQL-specific transient failures once its own exception types exist, without needing new backoff logic. Checkpoint 1.3's acquisition pipeline can now compose `RateLimiter.wait_if_needed()` (1.1.d) and `with_retry()` (this checkpoint) around `GitHubRESTClient.get_repository()` (1.1.c) to build the actual production fetch loop.

**Not started. Awaiting your review before proceeding to 1.1.f.**
