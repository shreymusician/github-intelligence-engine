# Checkpoint 1.1.f Report — GitHub GraphQL Client

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint f of h
**Status:** ✅ Complete, verified (live + deterministic tests)
**Date:** 2026-08-06

---

## 1. Purpose

Add a GraphQL client (`github_client/graphql.py`) alongside the existing REST client (1.1.c), so Checkpoint 1.1's Definition of Done item "Can query via GraphQL" is satisfied. GraphQL is introduced because GitHub exposes data REST cannot reach efficiently — e.g. fetching a repository plus its owner, license, and language in a single round trip, or (in future checkpoints) nested connections like issues/PRs/contributors that REST would need many paginated calls to assemble. REST alone is insufficient for that kind of multi-entity, nested-field fetch without either N+1 requests or over-fetching full resource payloads.

This sub-checkpoint implements **only** the client: authenticated query execution plus one convenience method (`get_repository`), mirroring `rest.py`'s single-endpoint scope from 1.1.c. No acquisition pipeline, no crawling, no database writes — those remain Checkpoint 1.3.

---

## 2. Design Verification (Phase 1)

**Why GraphQL is introduced:** GitHub's GraphQL API (v4) lets a single request specify exactly the fields needed across related entities (repository → owner → license → language) instead of REST's fixed-shape, per-resource responses. This becomes essential once Checkpoint 1.3+ needs nested collections (contributors, issues, languages breakdown) without paginating N separate REST calls.

**Why REST alone is insufficient:** REST's `GET /repos/{owner}/{repo}` (1.1.c) already covers flat repository metadata. But it cannot express "give me this repo's primary language, license, and owner type in one call with only the fields I want" — it returns a fixed, large JSON blob, and any nested/related data (e.g. multiple repos' owners) requires additional round trips.

**What GraphQL will be used for (this checkpoint):** A single convenience method, `get_repository(owner, repo)`, returning the *same* `RepositoryData`/`OwnerSummary` types `github_client.rest.GitHubRESTClient.get_repository` returns — so a caller can swap REST for GraphQL without touching downstream code. Also a generic `execute(query, variables)` method for future ad hoc queries.

**How GraphQL differs from REST on GitHub:**
| | REST | GraphQL |
|---|---|---|
| Endpoint | Resource-per-URL (`/repos/{owner}/{repo}`) | Single endpoint (`POST /graphql`) |
| Errors | HTTP status codes (404, 401, 403) | Mostly **HTTP 200** with a top-level `errors` array in the body; HTTP status is only used for auth/transport failures |
| Field selection | Fixed response shape | Caller-specified in the query |
| Rate limit | `X-RateLimit-*` response **headers** | **Point-based**, reported via a `rateLimit` field you must explicitly include in the query body — not headers |

**How GraphQL rate limits differ:** REST rate-limiting is already handled by `RateLimitInfo.from_headers()` (1.1.d), parsing response headers. GraphQL has a *separate* 5,000-point-per-hour budget (bucket name `"graphql"`, distinct from REST's `"core"`), and each query costs a variable number of points depending on requested fields/connections — GitHub reports this via a `rateLimit { limit cost remaining resetAt }` field that must be included in every query. `graphql.py` appends this field to every query it sends (`_RATE_LIMIT_FIELD`) and builds a `RateLimitInfo` directly from the response body rather than headers.

**How it reuses existing modules, unmodified:**
- `config.settings.Settings`/`get_settings()` — same token/log-level config as REST.
- `logging_setup.get_logger()` — same structured console logging.
- `github_client.rate_limiter.RateLimitInfo` — the *same dataclass* is constructed directly (it's a plain frozen dataclass with public fields, no REST-specific coupling), not subclassed or duplicated. `RateLimiter.is_exhausted()`/`wait_if_needed()` work unmodified against GraphQL-sourced instances (verified in §5).
- `github_client.retry.RetryPolicy`/`with_retry()` — retries `GitHubAPIError` raised by the GraphQL client exactly as it retries REST's (verified in §5); no GraphQL-specific retry logic needed since both clients raise the same exception types.
- `github_client.exceptions` — `AuthenticationError`, `GitHubAPIError`, `RepositoryNotFoundError` are reused as-is; no new exception types introduced.
- `github_client.rest.RepositoryData`/`OwnerSummary` — reused directly as the return type of `get_repository()`, not duplicated, so REST and GraphQL results are interchangeable to callers.

**What remains deferred:** acquisition/orchestration logic, multi-repo batching, database writes, repository crawling (all Checkpoint 1.3); a formal pytest suite (Checkpoint 1.1.g, explicitly not started per instructions).

No ambiguities blocked implementation; the one design decision worth flagging (resolved, not deferred) is in §4.

---

## 3. Architecture

```
github_client/
├── graphql.py         ← GitHubGraphQLClient, execute(), get_repository() (new, this checkpoint)
├── rest.py              (unchanged - 1.1.c; RepositoryData/OwnerSummary imported, not duplicated)
├── rate_limiter.py       (unchanged - 1.1.d; RateLimitInfo constructed directly, no new classmethod added)
├── retry.py               (unchanged - 1.1.e; composed externally by callers, same as with rest.py)
├── exceptions.py            (unchanged - 1.1.c)
└── __init__.py                (updated: exports GitHubGraphQLClient)
```

`graphql.py` depends on `config.settings`, `github_client.exceptions`, `github_client.rate_limiter`, `github_client.rest` (types only), and `logging_setup` — no new dependencies, no modifications to any prior-checkpoint file.

**Not wired with automatic retry/rate-limit waiting**, matching 1.1.d/1.1.e's established pattern: `GitHubGraphQLClient` methods raise; a caller composes `with_retry()`/`RateLimiter.wait_if_needed()` externally (verified in §5, wrapping `get_repository` the same way 1.1.e wrapped REST's).

---

## 4. GraphQL Strategy — Error Handling Design Decision

GitHub's GraphQL API can return a `repository: null` field accompanied by a `NOT_FOUND`-typed error in the top-level `errors` array — this is GitHub's normal partial-response pattern for a missing nested field, not a hard failure.

Two ways to handle this were possible:
1. Have the generic `execute()` method parse `errors`, detect `NOT_FOUND`, and raise `RepositoryNotFoundError` itself (guessing owner/repo from the GraphQL error `path`).
2. Have `execute()` treat `NOT_FOUND` errors as non-fatal (let the null field pass through in `data`), and let `get_repository()` — which already knows the exact owner/repo it queried — raise `RepositoryNotFoundError(owner, repo)` when it sees `data["repository"] is None`.

Option 2 was chosen: `execute()` stays generic and query-agnostic (it has no business inferring resource types from an error path), while `get_repository()` has full context and raises a correctly-populated exception without guesswork. Any *other* GraphQL error type (`FORBIDDEN`, `RATE_LIMITED`, syntax errors, etc.) is still raised immediately and generically as `GitHubAPIError` from `execute()`, since those aren't specific to a known-missing field.

---

## 5. Public API

```python
class GitHubGraphQLClient:
    def __init__(self, settings: Settings | None = None, session: requests.Session | None = None) -> None: ...

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute a raw GraphQL query. Returns the response's `data` object."""

    def get_repository(self, owner: str, repo: str) -> RepositoryData:
        """Fetch repository metadata via GraphQL, returning the same
        RepositoryData/OwnerSummary types as GitHubRESTClient.get_repository."""
```

Raises: `AuthenticationError` (HTTP 401), `GitHubAPIError` (other HTTP failures, network failures, malformed JSON, missing `data`, non-`NOT_FOUND` GraphQL errors), `RepositoryNotFoundError` (repository null/NOT_FOUND, from `get_repository()` only).

---

## 6. Verification Performed

All 20 checks below ran via an ad hoc script (not a committed pytest file — 1.1.g is deferred), combining live GitHub API calls with deterministic fake-session tests, following the same style as `CHECKPOINT_1_1E_REPORT.md` §6.

| # | Scenario | Result |
|---|---|---|
| 1 | Authenticated GraphQL request succeeds (`get_repository("octocat", "Hello-World")`) | ✅ Correct `full_name` |
| 2 | Owner parsed correctly from GraphQL's `... on User { databaseId avatarUrl }` fragment | ✅ |
| 3 | Rate limit parsed from response body's `rateLimit` field, `resource="graphql"` | ✅ |
| 4 | Parsed `remaining <= limit` (sanity) | ✅ |
| 5 | Generic `execute()` works for an arbitrary query (`viewer { login }`) | ✅ |
| 6 | Nonexistent repository raises `RepositoryNotFoundError` with correct owner | ✅ |
| 7 | Invalid token raises `AuthenticationError` (HTTP 401) | ✅ |
| 8 | Malformed (non-JSON) response raises `GitHubAPIError` | ✅ |
| 9 | Response missing both `data` and `errors` raises `GitHubAPIError` | ✅ |
| 10 | Non-`NOT_FOUND` GraphQL error (e.g. `FORBIDDEN`) raises `GitHubAPIError` | ✅ |
| 11 | Network failure (simulated `ConnectionError`) raises `GitHubAPIError` | ✅ |
| 12 | `RateLimiter.is_exhausted()` (1.1.d) works unmodified against a GraphQL-sourced `RateLimitInfo` | ✅ |
| 13 | `RetryPolicy.call()` (1.1.e) retries `GitHubAPIError` raised by the GraphQL client, succeeds on 3rd attempt | ✅ Exactly 3 calls |
| 14 | Retry never retries `RepositoryNotFoundError` from the GraphQL client | ✅ Exactly 1 call |
| 15 | **Live regression:** `get_repository` wrapped with `with_retry()` still succeeds | ✅ |
| 16 | Logger integration — `github_graphql_response` lines present in captured log output | ✅ |
| 17 | No token leakage — raw token value absent from all captured log output | ✅ |
| 18 | No token leakage — literal `"Bearer "` prefix absent from captured log output | ✅ |
| 19 | Static source check — the only `token` reference in `graphql.py` is `self._settings.github_token` building the `Authorization` header (never logged) | ✅ |
| 20 | **REST regression:** `GitHubRESTClient.get_repository` still works unaffected, live | ✅ |

**20/20 checks passed.** Tests 1–7, 15, 20 hit the live GitHub API; the rest used fake `requests.Session`-like objects for deterministic error-path coverage.

---

## 7. Deferred Responsibilities

- Wiring `with_retry`/`RateLimiter` into `graphql.py`'s own request path — same externally-composed pattern as `rest.py`; a caller (Checkpoint 1.3) applies both explicitly.
- Repository acquisition pipeline, multi-repo batching, database writes, crawling — Checkpoint 1.3.
- Additional GraphQL queries beyond `get_repository` (issues, PRs, contributors, languages breakdown) — added as needed by future checkpoints that actually consume them.
- Formal pytest test suite — Checkpoint 1.1.g, not started per instructions.

---

## 8. Definition of Done

| Criterion | Status |
|---|---|
| Authenticated GraphQL requests | ✅ |
| Reusable query execution method (`execute`) | ✅ |
| GraphQL-specific rate-limit parsing (point-based, from response body) | ✅ |
| Reuses `config.settings` | ✅ |
| Reuses `logging_setup` | ✅ |
| Reuses `github_client.retry` (composed externally, not duplicated) | ✅ |
| Reuses `github_client.rate_limiter` (`RateLimitInfo` constructed directly, no duplication) | ✅ |
| Reuses `github_client.exceptions` | ✅ |
| No duplicated REST logic | ✅ (`RepositoryData`/`OwnerSummary` imported from `rest.py`) |
| No database writes | ✅ |
| No acquisition pipeline / repository crawling | ✅ |
| Malformed response handling | ✅ Live + deterministic verified |
| Error handling (401, network failure, GraphQL error body, NOT_FOUND) | ✅ |
| No token leakage | ✅ |
| REST regression — `rest.py` unaffected | ✅ Live-verified, zero source changes to `rest.py` |

**Sub-checkpoint 1.1.f is COMPLETE and VERIFIED.**

---

## 9. What This Enables Next

Checkpoint 1.1.g (Test Suite) can formalize this verification script into pytest coverage for both `rest.py` and `graphql.py`. Checkpoint 1.3's acquisition pipeline can choose REST or GraphQL per call site — both return identical `RepositoryData`/`OwnerSummary` types and compose identically with `RateLimiter`/`with_retry` — without any adapter code.

**Not started. Awaiting your review before proceeding to 1.1.g.**
