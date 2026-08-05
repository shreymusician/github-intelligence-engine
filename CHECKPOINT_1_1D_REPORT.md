# Checkpoint 1.1.d Report — Rate Limiter

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint d of h
**Status:** ✅ Complete, verified (live + deterministic unit tests)
**Date:** 2026-08-05

---

## 1. Purpose

Interpret the `X-RateLimit-*` headers `github_client/rest.py` (1.1.c) already parses, and decide whether a caller should proactively wait before its next request. Per `SYSTEM_ARCHITECTURE.md` §7 Challenge 1, GitHub rate limiting is this project's first-named engineering risk; the approved Checkpoint 1.1 plan commits to **proactive throttling** (act before exhaustion) rather than reactive retry-after-failure. This sub-checkpoint builds the decision logic only — it makes no HTTP requests and is not wired into `rest.py`'s request path (see §3).

---

## 2. Architecture

### 2.0 Pre-implementation refactor (approved, Option A)

`RateLimitInfo` was originally defined in `github_client/rest.py` (Checkpoint 1.1.c). Per your explicit approval, it was relocated to `github_client/rate_limiter.py` as a **behavior-preserving refactor only** — identical fields, identical `from_headers` parsing logic, identical semantics. `rest.py` now imports it from `rate_limiter.py` (one import-line change; no other line in `rest.py` was touched). Re-verified: `rest.py`'s live successful-fetch result is bit-for-bit identical to 1.1.c's original verification (`limit=5000`, same field shapes) — see §4, check 1.

### 2.1 Module layout
```
github_client/
├── rate_limiter.py   ← RateLimitInfo (relocated), RateLimiter, DEFAULT_SAFETY_MARGIN
├── rest.py            ← imports RateLimitInfo from rate_limiter.py
├── exceptions.py       (unchanged)
└── __init__.py          (re-exports updated)
```

### 2.2 Dependency direction
`rate_limiter.py` depends on nothing in this package (only `logging_setup`). `rest.py` depends on `rate_limiter.py`. One-directional — no circular import risk, matches the "isolated responsibilities" instruction.

---

## 3. Responsibilities

**In scope (this sub-checkpoint):**
- Parse `X-RateLimit-Limit` / `-Remaining` / `-Reset` / `-Used` / `-Resource` into `RateLimitInfo` (relocated, unchanged logic).
- `RateLimiter.is_exhausted(rate_limit)` — is remaining quota at or below a configurable safety margin (default 50, per the approved plan's "e.g. 50 requests")?
- `RateLimiter.seconds_until_reset(rate_limit)` — how long until GitHub's quota window resets, floored at 0.
- `RateLimiter.wait_if_needed(rate_limit)` — the one mechanism the approved design explicitly calls for: **sleep** until reset, but only when exhausted; returns whether it waited.

**Explicitly out of scope (deferred):**
- **Automatic wiring into `rest.py`'s request path.** `get_repository()` does not call `RateLimiter` at all in this sub-checkpoint — flagged in Phase 1 and not objected to. A caller (Checkpoint 1.3's orchestration loop, or 1.1.e) invokes `RateLimiter` explicitly between requests.
- **Retries** — 1.1.e.
- **HTTP requests** — this module makes none; it only interprets data `rest.py` already fetched.
- **PostgreSQL writes** — none, no import of any database layer.
- **GraphQL point-cost budgeting** — 1.1.f, a different budget mechanic entirely.
- **Multi-token pooling** — explicitly out of V1 scope per the approved plan.

---

## 4. Public API

```python
DEFAULT_SAFETY_MARGIN: int = 50

@dataclass(frozen=True)
class RateLimitInfo:
    limit: int
    remaining: int
    reset_at: int       # epoch seconds
    used: int
    resource: str

    @classmethod
    def from_headers(cls, headers) -> "RateLimitInfo | None": ...

class RateLimiter:
    def __init__(self, safety_margin: int = DEFAULT_SAFETY_MARGIN,
                 sleep_fn=time.sleep, time_fn=time.time) -> None: ...
    def is_exhausted(self, rate_limit: RateLimitInfo) -> bool: ...
    def seconds_until_reset(self, rate_limit: RateLimitInfo) -> float: ...
    def wait_if_needed(self, rate_limit: RateLimitInfo | None) -> bool: ...
```

`sleep_fn`/`time_fn` are constructor-injectable specifically so tests never actually sleep and never depend on wall-clock time — every test in §5 below ran in well under a second despite exercising multi-minute "wait" scenarios.

---

## 5. Verification Performed

| # | Scenario | Result |
|---|---|---|
| 1 | **Regression:** re-ran 1.1.c's exact live successful-fetch scenario after the refactor | ✅ Identical result (`limit=5000`), confirming behavior-preserving |
| 2 | Full header parsing (all 5 fields) | ✅ Correct values, correct types |
| 3 | Missing headers → `None` | ✅ Unchanged from 1.1.c |
| 4 | `X-RateLimit-Resource` absent → defaults to `"core"` | ✅ |
| 5 | Exhaustion boundary: `remaining=51` (above margin), `remaining=50` (at margin), `remaining=0` (fully exhausted) | ✅ `is_exhausted` correctly `False`, `True`, `True` — margin is inclusive |
| 6 | `seconds_until_reset` with injected deterministic clock | ✅ `reset_at=1090, now=1000` → `90.0`; past `reset_at` → floored at `0.0`, never negative |
| 7 | `wait_if_needed`, exhausted quota | ✅ Called injected `sleep_fn` with exactly `75.0`, returned `True` |
| 8 | `wait_if_needed`, healthy quota | ✅ No sleep call, returned `False` |
| 9 | `wait_if_needed(None)` | ✅ No crash, no sleep, returns `False` |
| 10 | `wait_if_needed`, exhausted but `reset_at` already in the past | ✅ Returns `True` (correctly reports "was exhausted") without an actual pointless `sleep(0)` call |
| 11 | Malformed numeric header (e.g. `X-RateLimit-Limit: "not-a-number"`) | ✅ Documented as unchanged pre-existing behavior: raises `ValueError` — see §7 |
| 12 | Logger integration | ✅ `WARNING`-level log line emitted on every `wait_if_needed` call that found exhaustion, with `remaining`/`safety_margin`/`wait_seconds`/`reset_at` fields |
| 13 | No token leakage | ✅ `grep` for `token`/`settings`/`Settings` in `rate_limiter.py` → zero matches; this module never imports `config.settings`, structurally cannot touch the token |
| 14 | End-to-end integration | ✅ A real `RateLimitInfo` from a live `rest.py` call fed into `RateLimiter.wait_if_needed()` — correctly reported `False` (healthy quota, ~4998/5000 remaining) |

14/14 checks passed. Checks 1, 14 used live GitHub responses; checks 2–13 used deterministic, injected inputs (safer and faster than waiting on real quota exhaustion or real wall-clock time).

---

## 6. Edge Cases Tested

- Safety-margin boundary inclusivity (`remaining == safety_margin` counts as exhausted).
- `reset_at` in the past (already-elapsed reset window) — handled without a meaningless `sleep(0)` call or negative sleep duration.
- `rate_limit is None` (e.g. a response that didn't carry rate-limit headers at all) — handled without raising.
- Constructor guard: `RateLimiter(safety_margin=-1)` raises `ValueError` at construction (not exercised in the table above but present in the code — negative margins are nonsensical and rejected eagerly rather than producing confusing `is_exhausted` results later).

---

## 7. Deferred Responsibilities (Restated)

1. **Wiring into `rest.py`'s request flow.** `get_repository()` still makes exactly one request per call, unmodified from 1.1.c, with no automatic waiting. This is intentional — see §3.
2. **Malformed-header robustness in `from_headers`.** A non-numeric header value raises `ValueError`, uncaught. This is **pre-existing behavior from 1.1.c, deliberately left unchanged** per your explicit instruction ("Do not change... parsing logic"). Flagging as a candidate fix for whoever next touches `from_headers` — likely worth catching `ValueError` there and returning `None` (treating malformed headers the same as missing ones), but that's a parsing-logic change outside this sub-checkpoint's authorized scope.
3. **Retry-after-wait.** `wait_if_needed()` blocks the calling thread but does not retry the original request — that remains the caller's job (1.1.e or Checkpoint 1.3's orchestration).

---

## 8. Definition of Done

| Criterion | Status |
|---|---|
| Parses all GitHub rate-limit headers | ✅ |
| Exposes strongly-typed `RateLimitInfo` | ✅ (relocated here, Option A) |
| Determines whether requests may continue (`is_exhausted`) | ✅ |
| Determines remaining quota | ✅ (`RateLimitInfo.remaining`, unchanged) |
| Determines reset time (`seconds_until_reset`) | ✅ |
| Exposes helper methods the REST client can call | ✅ `is_exhausted`, `seconds_until_reset`, `wait_if_needed` |
| Implements only the mechanism required for proactive waiting (sleep, no retry) | ✅ |
| No automatic sleep beyond what the design requires | ✅ Sleep only occurs inside `wait_if_needed`, only when exhausted, only when explicitly called |
| No retries implemented | ✅ |
| No HTTP requests performed | ✅ |
| No PostgreSQL writes | ✅ |
| REST client behavior unmodified beyond the approved import-path refactor | ✅ Regression-verified live (check 1) |
| Thorough verification, not assumed correctness | ✅ 14/14 checks, live + deterministic |

**Sub-checkpoint 1.1.d is COMPLETE and VERIFIED.**

---

## 9. Status Documentation Updated

None of the project-level status documents (`DATABASE_STATUS.md`, `IMPLEMENTATION_ROADMAP.md`'s own checkbox tracking) reference Checkpoint 1.1's sub-checkpoints individually — that tracking granularity exists only in this checkpoint's own reports (`CHECKPOINT_1_1A/B/C/D_REPORT.md`). No update made; flagging that `IMPLEMENTATION_ROADMAP.md`'s Checkpoint 1.1 row still shows "⭕ Not Started / 0%" despite a/b/c/d now complete — consistent with the same stale-tracking pattern noted in `MILESTONE_0_REVIEW.md` §14 for Checkpoints 0.1/0.2. Recommend a single roadmap-table update once all of 1.1.a–1.1.h are done, rather than four incremental edits.

---

## 10. What This Enables Next

Checkpoint 1.1.e (Retry) can now distinguish "this is a quota-exhaustion 403, hand it to `RateLimiter`, don't retry" from "this is a transient 5xx/timeout, retry with backoff" using `RateLimitExceededError` (1.1.c) and `RateLimiter.is_exhausted()` (this checkpoint) together.

**Not started. Awaiting your review before proceeding to 1.1.e.**
