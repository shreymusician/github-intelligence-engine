# Checkpoint 1.1.c Report — GitHub REST Client

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint c of h
**Status:** ✅ Complete, verified live against the real GitHub API
**Date:** 2026-08-05

---

## 1. Purpose

Implement the single REST capability `IMPLEMENTATION_ROADMAP.md` Checkpoint 1.1 requires: authenticated repository metadata fetch via `GET /repos/{owner}/{repo}`. Per `SYSTEM_ARCHITECTURE.md` Module 1, this is Data Acquisition's first concrete capability. Returns validated Python objects only — no retries, no throttling, no GraphQL, no database writes, all explicitly deferred to later sub-checkpoints or Checkpoint 1.3.

---

## 2. Files Created

| File | Purpose |
|---|---|
| `github_client/__init__.py` | Public re-exports |
| `github_client/rest.py` | `GitHubRESTClient`, `RepositoryData`, `OwnerSummary`, `RateLimitInfo` |
| `github_client/exceptions.py` | `GitHubClientError` and 4 typed subclasses |
| `requirements.txt` (amended) | Added `requests==2.32.3` — no HTTP library was previously pinned |

**Location note (consistent with 1.1.a/1.1.b):** `FOLDER_STRUCTURE.md` places this at `src/acquisition/github_api.py` + `rate_limiter.py` + `errors.py`. Placed at repo-root `github_client/` instead, per your explicit deliverable list and the same rationale as `config/` and `logging_setup.py` — `src/`'s real module structure doesn't exist until Checkpoint 0.3.

---

## 3. Endpoints Implemented

**One:** `GET /repos/{owner}/{repo}` — repository metadata. No other REST endpoint exists in this client. A separate `GET /rate_limit` call was deliberately not implemented — rate-limit state is read from the `X-RateLimit-*` headers already present on every response to the one endpoint above.

---

## 4. Authentication Strategy

- `Authorization: Bearer <token>` header, built fresh per-request from `config.settings.get_settings().github_token` (Checkpoint 1.1.a) — the token is never stored on the client instance beyond what `Settings` already holds.
- `Accept: application/vnd.github+json` and `X-GitHub-Api-Version: 2022-11-28` headers set per GitHub's current REST API versioning guidance.
- **Live-verified as actually authenticated**, not just constructed correctly: the rate-limit ceiling returned by GitHub was `limit=5000` (the authenticated tier), not `60` (the unauthenticated tier) — direct proof the token was accepted and used, not merely present.

---

## 5. Error Handling

| HTTP outcome | Exception raised | Notes |
|---|---|---|
| `200` | — | Parsed into `RepositoryData` |
| `404` | `RepositoryNotFoundError(owner, repo)` | Exposes `.owner`/`.repo` fields |
| `401` | `AuthenticationError` | Token rejected |
| `403` + `X-RateLimit-Remaining: 0` | `RateLimitExceededError(reset_at)` | Classified only — no retry/wait behavior (1.1.d's job) |
| `403`, other cause | `GitHubAPIError(status_code=403)` | e.g. abuse-detection triggers, distinct from quota exhaustion |
| Any other non-200 | `GitHubAPIError(status_code=...)` | Catch-all for unexpected statuses |
| Malformed/missing expected JSON field | `GitHubAPIError` (wraps `KeyError`) | Defensive parsing in `_parse_repository` |
| Network failure (DNS, connection, timeout) | `GitHubAPIError` (wraps the underlying `requests.RequestException`) | 10-second timeout set on every request |

All five exception classes derive from `GitHubClientError`, so a caller can catch broadly or narrowly as needed.

---

## 6. Verification Performed

All checks executed against the **live** GitHub REST API (network access required explicit sandbox override for this session; see §8):

| # | Scenario | Result |
|---|---|---|
| 1 | Successful fetch (`octocat/Hello-World`) | ✅ All `RepositoryData`/`OwnerSummary` fields populated with correct types |
| 2 | Authenticated (not anonymous) | ✅ `rate_limit.limit == 5000` (proves authenticated tier, not the 60/hr anonymous tier) |
| 3 | Rate-limit header parsing, field-by-field | ✅ `limit`, `remaining`, `reset_at`, `used`, `resource` all correctly typed and populated |
| 4 | 404 handling (`octocat/this-repo-definitely-does-not-exist-12345`) | ✅ `RepositoryNotFoundError` raised, `.owner`/`.repo` correct |
| 5 | 401 handling (deliberately invalid token via injected `Settings`) | ✅ `AuthenticationError` raised |
| 6 | Network failure (unresolvable domain, deterministic) | ✅ `GitHubAPIError` raised, wraps `requests.exceptions.ConnectionError` |
| 7 | No token leakage in logs | ✅ Captured stdout+stderr across 2 real calls, grepped for the real token's distinguishing substring — zero matches |
| 8 | Returned data matches `MIGRATION_002_REPORT.md`'s `owners`/`repositories` schema | ✅ Field names/types align (`github_id`, `name`, `full_name`, `is_archived`, `is_fork`, `primary_language`, `license_spdx_id`, `github_created_at`, `pushed_at`, `owner.login`, `owner.github_id`, `owner.account_type`) — see §7 for one documented gap |

---

## 7. Security Considerations

- **Token never logged.** Confirmed by capturing full log output across multiple live calls (including a call made with a deliberately invalid token) and grepping for the token substring — zero matches in either case. The `_headers()` method builds the `Authorization` header fresh per call and it is never passed to `log.info`/`log.error`.
- **Token never appears in exception messages.** `GitHubAPIError`'s network-failure message includes the request URL and the underlying `requests` exception's string form, neither of which includes headers.
- **`Settings.__repr__`'s redaction (1.1.a) was exercised indirectly** — the client stores `self._settings` but no code path ever logs or stringifies it as a whole.
- **⚠️ Action recommended for you:** the real PAT used for this verification was pasted directly into the chat transcript per your choice. GitHub PATs pasted into any chat interface should be treated as potentially exposed — I'd recommend rotating/revoking this token at https://github.com/settings/tokens once you're done with Checkpoint 1.1 verification, and using a fresh one (dropped directly into `.env`, never pasted into chat) going forward. This is a one-time observation, not a blocker — the token remains in `.env` only, which is gitignored and was never committed.

---

## 8. Environment Note

Live verification required passing `dangerouslyDisableSandbox: true` for the network-bound test commands — this session's default tool sandbox blocks outbound network access, which is why the very first live-fetch attempt failed with a DNS resolution error (`getaddrinfo failed`) rather than a real API response. That failure was itself useful: it happened to double as an early, accidental confirmation that network failures are correctly wrapped in `GitHubAPIError` before I made it deterministic in test #6 above via an intentionally unresolvable domain.

---

## 9. Definition of Done

| Criterion | Status |
|---|---|
| `github_client/__init__.py`, `rest.py`, `exceptions.py` created | ✅ |
| Reuses `config/settings.py` (1.1.a) | ✅ |
| Reuses `logging_setup.py` (1.1.b) | ✅ |
| Authenticated REST requests | ✅ Live-verified (5000/hr tier) |
| Fetches repository metadata | ✅ `GET /repos/{owner}/{repo}` |
| Parses GitHub rate-limit headers | ✅ All 5 fields, live-verified |
| Typed exceptions | ✅ 5 classes, all live-exercised except `RateLimitExceededError` (not reachable without exhausting real quota — see §10) |
| No retries implemented | ✅ (deferred to 1.1.e) |
| No throttling implemented | ✅ (deferred to 1.1.d) |
| No GraphQL implemented | ✅ (deferred to 1.1.f) |
| No PostgreSQL writes | ✅ Returns plain dataclasses only |
| Returns validated Python objects only | ✅ `RepositoryData`/`OwnerSummary`/`RateLimitInfo`, frozen dataclasses |
| Live verification against real GitHub API | ✅ 8/8 checks passed |

**Sub-checkpoint 1.1.c is COMPLETE and VERIFIED.**

---

## 10. Known Gap (Documented, Not a Defect)

`OwnerSummary.github_created_at` does not exist as a field — GitHub's repository-metadata response embeds an abbreviated owner object that omits the owner account's creation date. Populating `owners.github_created_at` (Migration 002) would require a second call, `GET /users/{login}`, which is outside this checkpoint's single-endpoint scope. Flagged for whoever implements Checkpoint 1.3 (storage) — either accept a null `owners.github_created_at` for V1, or add the second call there.

`RateLimitExceededError` was implemented but not live-exercised — deliberately exhausting the real 5,000/hr quota to trigger a genuine 403 was judged not worth spending a large fraction of the hourly budget on for this checkpoint. Its logic (checking `remaining == 0` on a 403) was verified by code inspection and mirrors the already-live-verified header-parsing path exactly.

---

## 11. What This Enables Next

Checkpoint 1.1.d (Rate Limiter) can now wrap `GitHubRESTClient.get_repository()` calls, reading `RepositoryData.rate_limit` (or catching `RateLimitExceededError`) to decide when to throttle — without needing to touch this client's internals.

**Not started. Awaiting your review before proceeding to 1.1.d.**
