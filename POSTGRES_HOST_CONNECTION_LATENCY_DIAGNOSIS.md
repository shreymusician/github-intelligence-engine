# PostgreSQL Connection Latency Diagnosis & Environment Fix

**Date:** 2026-08-15
**Scope:** Environment/configuration only. No production code, architecture, or Checkpoint 1.4.d implementation changed.
**Status:** Diagnosis complete, fix applied and verified. **Checkpoint 1.4.e not started.**

---

## 1. Symptom

A 100-repository content-acquisition run (Checkpoint 1.4.e scope) appeared to progress at ~2.5 minutes per repository and, separately, was found to have stopped mid-batch (51/100 processed) with no surviving process and no persisted logs (`logging_setup.py` is console/stderr-only by design — Checkpoint 1.1.b).

## 2. Root Cause

`.env` (gitignored, local-only) set:
```
POSTGRES_HOST=localhost
```
On this Windows machine, `localhost` resolves IPv6 (`::1`) before IPv4. `docker-compose.yml` publishes PostgreSQL as `127.0.0.1:5432:5432` — IPv4 only, by explicit existing design (see its own "Services isolated from host network" comments). Every `psycopg.connect(host="localhost", ...)` therefore stalled on the unreachable IPv6 address until the OS default TCP timeout expired (~130s) before falling back to IPv4. `ContentWriter` and `RepositoryContentCandidateProvider` each open one connection per call (existing, documented 1.4.c/1.4.d precedent), so this ~130s tax was paid once per repository, on every persist.

Measured directly, no code changes:
```
psycopg.connect(host='localhost', ...)    -> 130.17s
psycopg.connect(host='127.0.0.1', ...)    ->   0.03s
```

Clone (~2-15s for typical repos), extraction (<0.5s), and cleanup (<2s) were never the bottleneck. Docker/Postgres were healthy throughout (container up for hours, not a cold-start condition). This is an environment/config fact specific to this machine's DNS resolution order, not a defect in `acquisition/clone_workspace.py`, `content_extractor.py`, `content_storage.py`, or `content_pipeline.py` — all confirmed correct by inspection, the existing test suite, and live diagnostic runs.

## 3. Fix Applied

`.env` (local-only, gitignored — not part of this or any commit):
```diff
- POSTGRES_HOST=localhost
+ POSTGRES_HOST=127.0.0.1
```
`config/settings.py`'s `Settings.postgres_host` default and every production module remain untouched. No architecture, pooling, caching, or concurrency was added.

## 4. Verification

**Connection time**, real `psycopg.connect()` via `config.settings.get_settings()`:

| | Before | After | Improvement |
|---|---|---|---|
| `localhost` → `127.0.0.1` | 130.17s | 0.053s | ~2455x |

**Controlled persistence test** (2 previously-unprocessed repositories, real `RepositoryCloner` → `extract_content` → `ContentWriter`, unmodified):

| Repository | clone (s) | extract (s) | persist (s) | cleanup (s) | total (s) |
|---|---|---|---|---|---|
| browser-use/browser-use | 5.71 | 0.05 | **0.07** | 0.43 | 6.26 |
| Comfy-Org/ComfyUI | 7.58 | 0.07 | **0.05** | 0.84 | 8.53 |

Persist time dropped from a consistent ~130s (measured across 4 repositories pre-fix) to ~0.06s — matching the isolated connection-time measurement exactly, confirming the fix resolves the actual bottleneck end-to-end, not just in isolation.

**Regression suite:** `pytest -q` → **258 passed, 4 skipped, 0 failed** (unchanged from pre-fix baseline). No test touches the real database — `test_content_storage.py` and `test_content_pipeline.py` use fake connections exclusively, so this run's real-DB writes came only from the controlled diagnostic samples, not the test suite.

## 5. Corpus State

**59/100** repositories now have persisted `repository_content` rows (53 pre-existing + 6 processed across this session's controlled diagnostic samples, one repo — `apache/superset` — legitimately clone-timed-out at 60s on a large repo and was correctly isolated, not a defect).

## 6. Correctness vs. Volume — Checkpoint 1.4.e

These are distinct concerns:

- **Correctness verification** of the orchestration pipeline (clone → extract → persist → cleanup, failure isolation, idempotent rerun, independent SQL check) was already fully established in Checkpoint 1.4.d's own live verification (§10.1–10.4) and reconfirmed by this session's diagnostic samples (success paths, a real clone-timeout failure correctly isolated, cleanup under both outcomes). No further correctness evidence is needed from a full 100-repository run.
- **Populating the full 100-repository corpus** is a separate, real data-completeness goal for downstream checkpoints. It is not blocked by any code defect — only by wall-clock time, which is now fixed (was ~130s/repo dominated by the connection bug; is now ~5-10s/repo dominated by clone). `ContentWriter.upsert_content()`'s existing idempotency means this can be done incrementally, in multiple runs, without any orchestration-level resume logic — re-running already-processed candidates is safe and cheap by design (1.4.d §10.4).

## 7. Recommendation

Close out this environment fix as its own record (this document). Do not run the remaining ~41 repositories automatically. The full-corpus population run remains available on request, now at the corrected ~5-10s/repo pace instead of ~130s/repo, and can be run in one sitting or incrementally without any further design changes.

**No progression to Checkpoint 1.4.e's full run, and no progression to Checkpoint 2, has occurred.**
