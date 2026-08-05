# Checkpoint 1.1.b Report — Minimal Structured Logging

**Parent Checkpoint:** 1.1 (GitHub API Client), Sub-checkpoint b of h
**Status:** ✅ Complete, verified
**Date:** 2026-08-05

---

## 1. Purpose

Provide the single logging entry point — `get_logger(name)` — that Checkpoints 1.1.c–1.1.g (REST client, rate limiter, retry logic, GraphQL client, tests) will use to record request outcomes, throttle events, and retries. Per `SYSTEM_ARCHITECTURE.md` §7 Challenge 1, GitHub rate limiting is this project's first named engineering risk; that risk is only manageable if its behavior is observable while it happens, not reconstructed after the fact.

This is a deliberately narrow subset of the full Checkpoint 0.5 (Monitoring & Logging Infrastructure) — console output only, no file rotation, no JSON, no external systems. Full 0.5 remains deferred.

---

## 2. Files Created

| File | Purpose |
|---|---|
| `logging_setup.py` | Root-level module: `get_logger(name)` helper, console handler configuration |

**Location note:** `FOLDER_STRUCTURE.md` names this file `src/logging.py`. Two deviations were made deliberately, flagged in Phase 1 and not objected to before implementation:
1. **Filename** — `logging_setup.py`, not `logging.py`, to avoid shadowing the Python standard-library `logging` module (a module named `logging.py` importable on `sys.path` would break `import logging` for every file that imports it, including itself).
2. **Location** — repository root, sibling to `config/` (1.1.a), not under `src/`, since `src/`'s real module structure is established in Checkpoint 0.3, which has not happened yet. Mirrors 1.1.a's identical resolution for `config/`.

---

## 3. Logging Strategy

- **Single root-logger console handler**, attached exactly once per process via a module-level `_configured` guard — not one handler per call to `get_logger()`.
- **Format:** `%(asctime)s %(levelname)-8s %(name)s: %(message)s`, e.g. `2026-08-05T20:01:22+0530 INFO     module.b: from module.b` — timestamp (ISO-8601-like with timezone offset), level (left-padded to 8 chars for alignment), logger name (module-scoped, so log lines are traceable to their source), message.
- **Level sourced from `config.settings.get_settings().log_level`** (Checkpoint 1.1.a) — this module does not read `LOG_LEVEL` from the environment itself, avoiding a second source of truth for the same setting.
- **Output stream:** `sys.stderr`, not `stdout` — standard convention keeping log noise separate from any future stdout-based data output.
- **`get_logger(name)` is idempotent and safe to call at import time** in every future module (`log = get_logger(__name__)` at the top of `github_client/rest.py`, etc.) — the root handler is configured on first call and left alone on every subsequent call, regardless of how many modules call it.
- **No file logging, no rotation, no JSON, no Sentry, no OpenTelemetry, no metrics, no tracing** — explicitly out of scope per your instruction, deferred to Checkpoint 0.5.

---

## 4. Verification Performed

All checks run against the live `venv` interpreter:

| # | Scenario | Result |
|---|---|---|
| 1 | `get_logger()` initializes root logger + handler on first call | ✅ 1 handler present |
| 2 | Default `LOG_LEVEL=INFO` (from `.env`) → `DEBUG` suppressed, `INFO`/`WARNING`/`ERROR` shown | ✅ |
| 3 | `LOG_LEVEL=DEBUG` (env override) → `DEBUG` now shown | ✅ |
| 4 | 5 separate `get_logger()` calls across different module names → still exactly 1 root handler | ✅ No duplication |
| 5 | Each `get_logger(name)` call returns a distinct, correctly-named logger (`module.a`, `module.b`, ...) | ✅ Confirmed in output |
| 6 | Timestamp, level, module name, message all present and correctly ordered | ✅ Regex-verified: `2026-08-05T20:02:40+0530 INFO     format.test: hello world` |
| 7 | `grep` for `token`/`settings\.` usage inside `logging_setup.py` | ✅ Only `settings.log_level` referenced — the module never touches or logs `Settings` as a whole or `github_token` |

---

## 5. Security Considerations

- `logging_setup.py` reads exactly one field off `Settings` — `log_level` — and never constructs a log message containing `settings` itself or `github_token`. Since `Settings.__repr__` (Checkpoint 1.1.a) already redacts the token, even an accidental `logger.debug(settings)` elsewhere would print `Settings(github_token='***redacted***', ...)`, not the real value — a defense-in-depth backstop, not a excuse to rely on it.
- **This module cannot redact content it doesn't control.** If a future call site in 1.1.c–1.1.f does something like `logger.debug(f"request headers: {headers}")` where `headers` includes a raw `Authorization: Bearer <token>` string, that value **will** be logged in full — `logging_setup.py`'s formatter has no way to inspect or filter message content. This is recorded here as an explicit obligation for whoever implements 1.1.c onward: never pass raw headers, raw tokens, or the `Settings` object's non-redacted fields into a log call.
- Output goes to `stderr` only — no log file exists yet, so there is no persisted artifact to accidentally commit or leave lying around with sensitive request metadata.

---

## 6. Definition of Done

| Criterion | Status |
|---|---|
| `logging_setup.py` created | ✅ |
| Structured console logging only | ✅ |
| Configurable via `LOG_LEVEL` from `config/settings.py` (1.1.a reused, not reimplemented) | ✅ |
| Never logs secrets or tokens (within this module's own code) | ✅ |
| Timestamp, level, module name, message present | ✅ |
| `get_logger()` helper provided for future modules | ✅ |
| Intentionally minimal — no file logging, rotation, JSON, Sentry, OpenTelemetry, metrics, tracing | ✅ |
| Logger initializes correctly | ✅ |
| `LOG_LEVEL` respected at DEBUG/INFO/WARNING/ERROR | ✅ |
| Repeated `get_logger()` calls do not duplicate handlers | ✅ |
| Functional verification (not just inspection) | ✅ 7/7 checks passed against live interpreter |

**Sub-checkpoint 1.1.b is COMPLETE and VERIFIED.**

---

## 7. What This Enables Next

Checkpoint 1.1.c (REST Client) can now `from logging_setup import get_logger` and `log = get_logger(__name__)` at module level, with both configuration (1.1.a) and logging (1.1.b) available as stable foundations — no more prerequisite infrastructure needed before client code begins.

**Not started. Awaiting your review before proceeding to 1.1.c.**
