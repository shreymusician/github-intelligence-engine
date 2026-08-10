# Checkpoint 1.4.a Report — Shallow Clone + Temporary Content Workspace

**Parent Checkpoint:** 1.4 (Repository Content Download), sub-checkpoint a of e
**Status:** ✅ Complete, verified (deterministic mocked tests + live GitHub clones)
**Date:** 2026-08-10

---

## 1. Purpose

Provide a temporary, always-cleaned-up shallow-clone workspace for a single repository, given its `owner/repo` full name — the first step in Checkpoint 1.4's content-acquisition pipeline (`GitHub → temporary clone → extract → persist to repository_content → delete clone`, per the approved V1 lifecycle). This sub-checkpoint does **not** extract README/manifest content, does **not** touch PostgreSQL, and does **not** orchestrate multiple repositories — those are 1.4.b, 1.4.c, and 1.4.d respectively.

---

## 2. Scope

**In scope:** `git clone --depth 1` into an OS temporary directory, cleanup of that directory (guaranteed, success or failure), typed failure handling, no persistent cache (per the explicit V1 storage clarification: PostgreSQL's future `repository_content` table is the durable store, not the filesystem).

**Explicitly out of scope (deferred to later sub-checkpoints):** README/manifest file discovery and reading (1.4.b), `repository_content` schema/persistence (1.4.c), multi-repository orchestration and disk-space policy across a batch (1.4.d), any Milestone 2 functionality (technology detection, documentation scoring, etc. — not touched).

---

## 3. Phase 1 — Design Verification (confirmed before coding)

1. **No persistent cache.** Per your explicit instruction, the clone directory is scratch space only — it does not survive past the single fetch operation. This ruled out the originally-sketched `data/repo_cache/{owner}__{repo}/` design from the pre-approval planning turn; the clone lives in the OS temp directory (`tempfile.mkdtemp()`) instead, which also makes "cannot accidentally be committed" a structural property (the temp dir is categorically outside the git repository), not a `.gitignore`-hygiene concern.
2. **Authentication decision (not left implicit):** all repositories in this project's V1 corpus are public (selected via GitHub's public Search API, Checkpoint 1.3.a). Clones are therefore always **unauthenticated** (`https://github.com/{full_name}.git`, no token). This was a deliberate choice, not an oversight: embedding `GITHUB_TOKEN` in a clone URL would expose it via process-listing (subprocess argv is visible to other processes on the same machine) and potentially write it into the clone's own `.git/config` — a real leakage vector this design avoids entirely by never using the token for cloning in the first place, rather than trying to redact it after the fact.
3. **Subprocess safety:** `subprocess.run()` is always called with an explicit argument list (`["git", "clone", "--depth", "1", ...]`) and `shell=False` — no shell string interpolation exists anywhere in this module, so there is no shell-injection surface regardless of what `full_name` contains.
4. **`full_name` validation:** even though `full_name` in production always originates from `repositories.full_name` (itself sourced from GitHub's own API responses via Checkpoint 1.3, not arbitrary user input), a regex matching GitHub's actual owner/repo naming rules is applied before `full_name` ever reaches a subprocess argument or gets interpolated into a URL — defense in depth, not a response to any observed attack.
5. **No `GitPython` dependency added.** `subprocess` + the system `git` CLI (confirmed installed, 2.55.0) fully covers this sub-checkpoint's scope (invoke one command, check exit code, capture stderr) — no concrete requirement emerged that `subprocess` couldn't reasonably handle.

**No ambiguity required stopping for your input** — every open question from the pre-approval planning turn (persistent cache vs. not, authentication strategy) was already resolved by your explicit clarifications before this sub-checkpoint began.

---

## 4. Architecture

```
acquisition/clone_workspace.py
├── RepositoryCloner
│   ├── __init__(settings=None, timeout_seconds=None)
│   └── clone(full_name) -> contextmanager yielding Path
│         1. validate full_name against GitHub's owner/repo shape
│         2. tempfile.mkdtemp() — fresh OS temp directory
│         3. subprocess.run(["git","clone","--depth","1","--single-branch",
│                             "--no-tags","--quiet", url, dest_dir],
│                            shell=False, capture_output=True, timeout=N)
│         4. yield Path(dest_dir)
│         5. finally: shutil.rmtree(dest_dir, onexc=<clear-readonly-and-retry>)
└── (module-level) _force_remove_readonly, _remove_clone_dir

acquisition/exceptions.py (extended)
├── ContentAcquisitionError (new base, Checkpoint 1.4's error hierarchy)
└── RepositoryCloneError (new, extends ContentAcquisitionError)

config/settings.py (extended)
└── Settings.content_clone_timeout_seconds: int = 60
    (env var CONTENT_CLONE_TIMEOUT_SECONDS, not fail-fast — same additive
    pattern 1.3.b used for postgres_*)
```

`RepositoryCloner` holds no state beyond its own timeout configuration — matches `acquisition/selection.py`'s `RepositorySelector` precedent (`CHECKPOINT_1_3A_REPORT.md` §4) of holding no state beyond what it was constructed with.

---

## 5. A Real Defect Found and Fixed During This Sub-Checkpoint's Own Live Verification

**Not a pre-existing/frozen-checkpoint defect — found and fixed within 1.4.a's own implementation, before sign-off**, so per this project's own established practice (distinguishing "reopening an already-verified checkpoint" from "fixing a bug in code not yet signed off"), this was fixed directly rather than reported as a blocker.

- **Symptom:** the first live-verification run showed cloned directories were **not** actually removed after the `with` block exited — `directory removed after context exit: False` for both successful test clones, and the leftover directories were independently confirmed still present on disk via `ls`.
- **Root cause:** the initial implementation used `shutil.rmtree(clone_dir, ignore_errors=True)`. On Windows, `git clone` writes some files under `.git/objects/pack/` as read-only; `shutil.rmtree` cannot delete a read-only file without first clearing that attribute, and `ignore_errors=True` **silently swallows** that failure instead of surfacing it — so cleanup appeared to run without error but left the directory (or parts of it) behind.
- **Fix:** replaced `ignore_errors=True` with an `onexc` handler (`_force_remove_readonly`) that clears the read-only bit (`os.chmod(path, stat.S_IWRITE)`) and retries the delete — the standard, well-documented pattern for cleaning up git clones on Windows. Genuine cleanup failures (e.g. a file locked by another process) are now caught and logged as a warning rather than silently ignored or allowed to mask whatever exception the `finally` block might otherwise be propagating.
- **Re-verified live** after the fix: both real clones (`octocat/Hello-World`, `public-apis/public-apis`) now correctly report `directory removed after context exit: True`, independently confirmed absent from the OS temp directory via a direct filesystem check.

---

## 6. Files Changed

| File | Change |
|---|---|
| `acquisition/clone_workspace.py` | New — `RepositoryCloner` |
| `acquisition/exceptions.py` | Extended — `ContentAcquisitionError`, `RepositoryCloneError` |
| `acquisition/__init__.py` | Extended — exports the three new symbols |
| `config/settings.py` | Extended — `content_clone_timeout_seconds` field, env var, `__repr__` |
| `tests/test_clone_workspace.py` | New — 24 deterministic tests |

No file outside `acquisition/`, `config/settings.py`, and `tests/` was touched. No `alembic/` migration in this sub-checkpoint (Migration 010 is 1.4.c's job).

---

## 7. Deterministic Test Suite (`tests/test_clone_workspace.py`, 24 tests)

No live git clone, no network call, no live PostgreSQL — `subprocess.run` is monkeypatched throughout.

| Class | Tests | Covers |
|---|---|---|
| `TestFullNameValidation` | 3 | Malformed names rejected before `subprocess.run` is ever called (path traversal attempts, missing slash, extra segments, embedded spaces); valid name accepted |
| `TestSubprocessSafety` | 3 | `git` invoked with an argument list (not a shell string), `shell=False` explicitly; correct `--depth 1`/`--single-branch`/`--no-tags` flags; clone URL is unauthenticated (`https://github.com/...`, no `@credentials`); configured timeout passed through to `subprocess.run` |
| `TestCloneFailureHandling` | 3 | Non-zero exit code → `RepositoryCloneError`; `subprocess.TimeoutExpired` → `RepositoryCloneError`; git executable missing (`FileNotFoundError`/`OSError`) → `RepositoryCloneError` |
| `TestWorkspaceCleanup` | 4 | Directory removed after success; removed after clone failure; removed even when the **caller's** code inside the `with` block raises; the temp directory is confirmed outside the project repository's own path |
| `TestNoTokenLeakage` | 2 | `GITHUB_TOKEN` never appears in `subprocess` argv or in logs, on both the success and failure paths |
| `TestSettingsIntegration` | 3 | Default timeout sourced from `Settings`; explicit `timeout_seconds` overrides `Settings`; `repr(settings)` shows the new field and still redacts the token |

**All 24 pass.** Full project regression suite: **180 passed, 2 skipped** (156 pre-existing + 24 new), run after the read-only-cleanup fix — no regression to any prior checkpoint.

---

## 8. Live Verification

Ran `RepositoryCloner` exactly as a real caller would (`RepositoryCloner()`, default `Settings`) against real GitHub repositories, via a throwaway driver script (not delivered code, deleted after evidence capture — matching 1.3.b/1.3.c/1.3.e's established precedent):

| Target | Result |
|---|---|
| `octocat/Hello-World` | Cloned in 1.37s; top-level entries `['.git', 'README']`; README-like file found: `README`; directory confirmed removed after the `with` block exited |
| `public-apis/public-apis` | Cloned in 1.45s; `README.md` correctly found among 8 top-level entries; directory confirmed removed |
| `octocat/this-repo-does-not-exist-xyz123` (deliberately nonexistent) | Correctly raised `RepositoryCloneError` after 1.13s, wrapping git's real `returncode=128`/`"repository not found"` stderr; temp directory never left behind |

**Independent filesystem verification** (not trusting the script's own printed claim): `ls` against the OS temp directory after the run found **zero** `ghia_clone_*` directories remaining — confirmed via a separate command, not the driver script's own output.

**Security check on the live run's output:** grepped the full live-run transcript for the real `GITHUB_TOKEN` value read from `.env` — **0 occurrences** (expected — this module never uses the token at all, by design).

---

## 9. Known Limitations

- **No retry on transient clone failure.** A one-off network blip during `git clone` surfaces immediately as `RepositoryCloneError`, with no automatic retry inside this module. This mirrors `acquisition/selection.py`'s own precedent (`CHECKPOINT_1_3A_REPORT.md` §7: "no retry is automatically applied... a caller-composed `RetryPolicy` works correctly around it") — retry composition, if wanted, belongs to 1.4.d's orchestration layer, not this module.
- **No disk-space ceiling enforced.** A single shallow clone could still be large for an unusually big repository; at 100-repository V1 scale with immediate cleanup after each clone, this hasn't been a demonstrated problem. Flagged, not solved speculatively.
- **Anonymous GitHub clone rate limits** are not proactively tracked (unlike the REST API's `RateLimiter`) — GitHub does not publish a strict quota for anonymous git-protocol clones the way it does for REST, and 100 sequential shallow clones is well within normal CI-scale usage; not expected to be an issue, not verified against an explicit limit.
- **Windows-specific read-only-file cleanup fix** (§5) was discovered and fixed on this development environment (Windows); the `onexc` handler is a no-op on platforms where git doesn't mark pack files read-only, so it should be safe cross-platform, but this hasn't been independently verified on Linux/macOS.

---

## 10. Definition of Done

| Criterion | Status |
|---|---|
| `git clone --depth 1` implemented via `subprocess`, no `GitPython` | ✅ |
| Subprocess safety (argument list, `shell=False`) | ✅ §7, `TestSubprocessSafety` |
| Clone timeout configurable and enforced | ✅ §7, `TestSubprocessSafety::test_timeout_passed_to_subprocess`; live-confirmed default |
| Repository name/path sanitization | ✅ §7, `TestFullNameValidation` (7 malformed-input cases) |
| Cache/workspace cleanup, guaranteed | ✅ §5 (defect found and fixed), §7 `TestWorkspaceCleanup`, §8 live-independently-verified |
| Clone failure handling (typed exception) | ✅ §7 `TestCloneFailureHandling`, §8 live-verified against a real nonexistent repository |
| GitHub authentication strategy decided and documented | ✅ §3.2 — unauthenticated, deliberately |
| No token leakage in git command arguments/logs | ✅ §7 `TestNoTokenLeakage`, §8 live-grepped, 0 occurrences |
| Deterministic tests using `tmp_path`/mocked `subprocess.run` | ✅ 24 tests, no live dependency in normal execution |
| Temporary clone directories cannot accidentally be committed | ✅ Structural — OS temp directory, outside the repo; verified via `TestWorkspaceCleanup::test_temp_directory_is_outside_the_project_repository` |
| No persistent filesystem cache introduced | ✅ Confirmed by inspection — no cache-directory config, no freshness/TTL logic, matches the approved V1 lifecycle exactly |
| README extraction, manifest extraction, PostgreSQL persistence, orchestration | ✅ Correctly NOT implemented — deferred to 1.4.b/c/d |
| No Milestone 2 functionality touched | ✅ Confirmed by inspection |
| Full regression suite green | ✅ 180 passed, 2 skipped, 0 failed |

**Sub-checkpoint 1.4.a is COMPLETE and VERIFIED.**

---

## 11. What This Enables Next

1.4.b (README + Manifest Extraction) can now be built directly against `RepositoryCloner.clone(full_name)`'s contract: a context manager yielding a `Path` to a real, populated (or the operation already failed) shallow clone, guaranteed to be cleaned up afterward regardless of what the extraction step does with it.

**Not started. Awaiting your review before proceeding to 1.4.b.**
