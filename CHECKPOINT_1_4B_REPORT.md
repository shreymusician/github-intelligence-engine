# Checkpoint 1.4.b Report — README + Package Manifest Extraction

**Parent Checkpoint:** 1.4 (Repository Content Download), sub-checkpoint b of e
**Status:** ✅ Complete, verified (deterministic tests + live extraction, independently cross-checked)
**Date:** 2026-08-10

---

## 1. Objective

Given a directory already containing a shallow clone (produced by `acquisition.clone_workspace.RepositoryCloner`, Checkpoint 1.4.a), locate the repository's README and known package-manifest files at the root, read them safely as UTF-8 text, and return structured `ExtractedFile` objects — ready for persistence in 1.4.c, but not persisted here. This module owns no clone lifecycle: it never invokes git, never creates or deletes a directory, and is handed a directory that already exists.

---

## 2. Phase 1 — Design Verification (confirmed before coding)

Re-read `IMPLEMENTATION_ROADMAP.md` Checkpoint 1.4, `CHECKPOINT_1_4A_REPORT.md`, and inspected `acquisition/clone_workspace.py` and `tests/test_clone_workspace.py` directly before writing any code.

1. **1.4.a's contract confirmed:** `RepositoryCloner.clone(full_name)` is a context manager yielding a `Path` to a populated (or the operation already failed and raised) shallow clone, guaranteed removed on exit. 1.4.b's `extract_content(clone_dir: Path)` takes exactly that yielded `Path` — no new coupling, no changes needed to 1.4.a.
2. **Root-level only, not a recursive tree walk.** The roadmap's own phrase is "important files" (`IMPLEMENTATION_ROADMAP.md` Checkpoint 1.4) — the repository's own primary README/manifests, not vendored or nested copies a recursive walk would also match (e.g. inside `node_modules/`, `vendor/`, or test fixtures). This also avoids walking potentially large cloned trees just to find a handful of well-known root-level filenames. Tested explicitly (`test_nested_manifest_in_subdirectory_not_detected`) to make the boundary a verified fact, not an assumption.
3. **README matching is case-insensitive; manifest matching is case-sensitive — a deliberate distinction, not an inconsistency.** GitHub itself renders README detection case-insensitively. Manifest ecosystems (npm, pip, etc.) are case-sensitive by convention — and critically, this project's Windows development machine has a case-insensitive filesystem (NTFS), so a naive `Path.is_file()` check for `"package.json"` would silently succeed against a real on-disk file named `"Package.json"`, masking a case mismatch that would matter on GitHub's actual (Linux, case-sensitive) storage. Manifest detection therefore compares `entry.name` (the real on-disk string, as git wrote it) against the exact expected filename via Python string equality — case-sensitive regardless of host OS. Verified by `test_manifest_filename_matching_is_case_sensitive`.
4. **Ambiguous README candidates resolved by a fixed priority order** (`.md` > `.rst` > `.txt` > no extension), returning exactly one `readme` result even when multiple variants coexist in the same repository — avoids a downstream "which README is authoritative" ambiguity.
5. **Symlink/path-traversal defense — a concrete requirement, not speculative.** A malicious public repository could contain a symlink named e.g. `README.md` pointing outside the clone directory (even to an absolute path elsewhere on the host). Every candidate file must (a) not be a symlink and (b) resolve to a path still contained within the clone directory, checked before the file is ever opened.
6. **Manifest list is exactly the 10 you specified** (`package.json`, `requirements.txt`, `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `Gemfile`, `go.mod`, `Cargo.toml`, `composer.json`) — cross-checked against `TECHNICAL_IMPLEMENTATION_IDEAS.md` for any other manifest type the design already explicitly named; found none beyond what you already listed (Dockerfile is mentioned there only generically, via "etc.", not a firm identification — not added).
7. **A file-size guard** (5 MB, a defensive bound only) prevents reading an unexpectedly huge file into memory; a file that fails UTF-8 decoding is skipped and logged, not stored with a guessed encoding — matching this project's established practice of documenting real gaps (`CHECKPOINT_1_3B_REPORT.md` §5's `licenses.is_osi_approved` precedent) rather than fabricating data.

**No ambiguity required stopping for your input.** Every design point above was resolvable from the roadmap, 1.4.a's existing contract, and ordinary security engineering judgment — none of it materially affects the `repository_content` schema already approved for 1.4.c.

---

## 3. Implementation

`acquisition/content_extractor.py` — one public function, `extract_content(clone_dir: Path) -> list[ExtractedFile]`, plus the `ExtractedFile` frozen dataclass (`content_type`, `file_path`, `content`, `content_hash`, `content_size_bytes` — field names deliberately match `repository_content`'s planned columns for a direct, translation-free mapping in 1.4.c).

Algorithm: one `clone_dir.iterdir()` pass builds a filtered, safety-checked entry list (`_is_safe_regular_file`); README selection (`_select_readme`) and the 10 manifest-filename lookups both operate against that single listing (not ten separate filesystem round-trips); each matched file is read via `_read_file`, which stats it (size-limit check), reads raw bytes, decodes as UTF-8, and computes a SHA-256 hash over those same raw bytes.

---

## 4. Files Changed

| File | Change |
|---|---|
| `acquisition/content_extractor.py` | New — `extract_content()`, `ExtractedFile` |
| `acquisition/__init__.py` | Extended — exports `ExtractedFile`, `extract_content` |
| `tests/test_content_extractor.py` | New — 40 deterministic tests |

No file outside `acquisition/` and `tests/` was touched. `acquisition/clone_workspace.py` (1.4.a) is untouched — confirmed by inspection and by `git diff` before committing.

---

## 5. Deterministic Test Suite (`tests/test_content_extractor.py`, 40 tests)

All tests run against real filesystem trees built under pytest's `tmp_path` — no clone, no network, no PostgreSQL.

| Class | Tests | Covers |
|---|---|---|
| `TestReadmeDetection` | 7 | `.md` present; absent; 7 casing/extension variants (parametrized); duplicate candidates resolved by priority; no-extension lowest priority; unsupported extension (`.markdown`) ignored |
| `TestManifestDetection` | 4 (+10 parametrized) | Single manifest; multiple manifests simultaneously; all 10 supported types individually (parametrized); case-sensitive matching (`Package.json` correctly NOT detected); nested/subdirectory manifest correctly NOT detected (root-only boundary); unrecognized files (`LICENSE`, `.gitignore`, `main.py`) ignored |
| `TestContentIntegrity` | 7 | Empty file (zero size, correct empty-string hash); UTF-8 multibyte characters (Café/日本語/emoji); invalid UTF-8 skipped; hash is deterministic across repeated calls; hash changes when content changes; byte-size correctness; `file_path` uses forward slashes |
| `TestSafePathHandling` | 3 | Symlinked README pointing outside the clone dir skipped (real symlink, environment-permitting); symlinked manifest skipped (same); a monkeypatch-based test that exercises the same rejection branch **without** requiring OS symlink privileges (added specifically because this dev machine lacks them — see §6) |
| `TestFileSizeLimit` | 1 | A file exceeding the size cap is skipped |
| `TestLogging` | 2 | Extraction-completion log line with correct count; undecodable-file warning logged |

**38 pass, 2 skip.** The 2 skips are the real-symlink tests — this Windows development machine doesn't have Developer Mode/admin rights enabled, so `Path.symlink_to()` raises `OSError`, and both tests skip gracefully (`pytest.skip`) rather than failing or being silently omitted. The symlink-rejection *logic itself* is still verified deterministically via a third test that monkeypatches `Path.is_symlink` — added specifically to close this gap without depending on host permissions.

Full project regression suite: **218 passed, 4 skipped** (180 pre-existing + 38 new + the 2 pre-existing skips + these 2 new skips) — no regression to any prior checkpoint.

---

## 6. Live Verification

Ran `extract_content()` against real clones of three repositories already present in the Checkpoint 1.3 corpus (via a throwaway driver script, not delivered code, deleted after evidence capture):

| Repository | Result |
|---|---|
| `octocat/Hello-World` | `README` (no extension) detected, 14 bytes, content `"Hello World!\r\n"` |
| `public-apis/public-apis` | `README.md` detected, 232,508 bytes, including a multibyte emoji character correctly UTF-8-decoded |
| `vinta/awesome-python` | Two files detected: `README.md` (84,637 bytes) and `pyproject.toml` (1,250 bytes) — confirms multi-file extraction from one real repository |

### Independent verification (not trusting `extract_content()`'s own return values alone)

For `octocat/Hello-World` and `vinta/awesome-python`, each extracted file was **independently** re-read and re-hashed via plain `os`/`hashlib` calls (a separate code path from the extractor's own `_read_file`), and cross-checked:

```
octocat/Hello-World:
  README: hash_match=True size_match=True content_match=True (independent_size=14, reported_size=14)

vinta/awesome-python:
  README.md: hash_match=True size_match=True content_match=True (independent_size=84637, reported_size=84637)
  pyproject.toml: hash_match=True size_match=True content_match=True (independent_size=1250, reported_size=1250)

ALL INDEPENDENT CHECKS PASSED
```

Every hash, size, and decoded-content value the extractor reported was confirmed to match an independently computed value, not merely self-consistent.

---

## 7. Defects Discovered/Fixed

**None in the source module.** One test-fixture artifact was found and fixed during test development: two tests initially asserted exact string content (e.g. `"# Hello\n\nWorld."`) after writing that content with `Path.write_text(..., encoding="utf-8")` — on Windows, text-mode writing translates `\n` → `\r\n` by default, so the fixture itself wrote different bytes than the literal string implied. `extract_content()`'s own behavior was correct throughout (it reads raw bytes via `read_bytes()` and decodes them verbatim, with no newline translation of its own — confirmed by the live run above, where `octocat/Hello-World`'s real README byte-for-byte preserved its actual `\r\n` line ending). Fixed by passing `newline=""` to the two affected `write_text()` calls in the test fixtures, pinning the exact on-disk bytes — not a change to `acquisition/content_extractor.py`.

---

## 8. Security Sanity Check

- Grepped both live-run transcripts (extraction run + independent-verification run) for the real `GITHUB_TOKEN` value read from `.env` — **0 occurrences** (expected: this module performs no network I/O and never touches `Settings` at all).
- Confirmed via `ls` against the OS temp directory that **no leftover `ghia_clone_*` directories** remained after both live runs — 1.4.a's cleanup guarantee holds under 1.4.b's usage pattern too.
- Symlink/path-escape defense verified both via a real-symlink test (where environment permissions allow) and a permission-independent monkeypatch test (§5).
- No secrets, credentials, or `.env`/`PAT*.txt` content appear anywhere in `acquisition/content_extractor.py` or `tests/test_content_extractor.py`.

---

## 9. Known Limitations

- **Root-level detection only** (§2.2) — a manifest in a monorepo subdirectory (e.g. `backend/requirements.txt`) is not extracted in this sub-checkpoint's scope. The `repository_content` schema already approved for 1.4.c supports a `file_path` with subdirectories if nested discovery is needed later; nothing here forecloses that, it's simply not implemented now.
- **Exactly one README per repository, never more** — a deliberate choice (§2.4); if a future need arises for "all README variants," this priority-selection logic would need to change from "pick one" to "return all," a larger design decision left for whoever needs it.
- **Non-UTF-8 files are silently skipped, not stored in any alternate form** — a real, named gap, consistent with this project's established practice.
- **5 MB file-size cap is a fixed module constant, not configurable via `Settings`** — no concrete need for tunability has emerged yet; adding a `Settings` field for this would be speculative given the current corpus's actual file sizes (largest README encountered live: 232 KB, far under the cap).
- **Symlink handling verified via monkeypatch as well as real symlinks** — the real-symlink tests skip on this specific Windows development machine (no Developer Mode/admin rights); the monkeypatch test exists specifically to avoid the safety-logic being unverified as a result.

---

## 10. Definition of Done

| Criterion | Status |
|---|---|
| README detection (`.md`/`.rst`/`.txt`/no-extension, case-insensitive) | ✅ §5, `TestReadmeDetection` |
| Manifest detection for exactly the 10 approved file types | ✅ §5, `TestManifestDetection` (parametrized across all 10) |
| Reading text content safely, UTF-8 decoding | ✅ §5, `TestContentIntegrity` |
| Missing files handled (no error, simply absent from result) | ✅ `test_readme_absent`, and implicitly every manifest-absence case |
| Unreadable/non-UTF-8 files handled | ✅ `test_invalid_utf8_file_skipped`, live-independent-verification confirms real files decode correctly |
| `content_hash` computed (SHA-256) | ✅ Deterministic, live-independently cross-checked |
| `content_size_bytes` computed | ✅ Byte-accurate, live-independently cross-checked |
| Structured `ExtractedFile` objects returned | ✅ |
| `pathlib`/filesystem operations only, no new dependency | ✅ Confirmed by inspection — only stdlib (`hashlib`, `pathlib`, `dataclasses`) |
| No PostgreSQL persistence, no Migration 010, no `ContentWriter` | ✅ Confirmed by inspection — no `psycopg` import anywhere in this module |
| No content pipeline orchestration, no batch acquisition | ✅ Single-repository, single-call function only |
| No Milestone 2 functionality | ✅ Confirmed — no technology detection, no scoring, no interpretation of content |
| Clone lifecycle not owned by this module | ✅ Confirmed by inspection — no `subprocess`, no `tempfile`, no directory creation/deletion anywhere in `content_extractor.py` |
| Deterministic tests, no live dependency in normal execution | ✅ 38 passed, 2 environment-permission skips (covered by an equivalent deterministic test) |
| Live verification against real Checkpoint 1.3 repositories | ✅ §6, 3 repositories |
| Independent verification (not trusting extractor output alone) | ✅ §6, separate `os`/`hashlib` code path, 3 files cross-checked |
| Security sanity check | ✅ §8 |
| Full regression suite green | ✅ 218 passed, 4 skipped, 0 failed |

**Sub-checkpoint 1.4.b is COMPLETE and VERIFIED.**

---

## 11. Git

- **Commit hash:** *(recorded after commit — see final message)*
- **Commit message:** `Checkpoint 1.4.b: README and package manifest extraction`
- **Push status:** *(recorded after push — see final message)*

---

## 12. What This Enables Next

1.4.c (Content Persistence) can now be built directly against `extract_content(clone_dir) -> list[ExtractedFile]`'s contract — each `ExtractedFile`'s fields already match `repository_content`'s planned columns one-to-one, so 1.4.c's `ContentWriter` should need no translation layer beyond constructing the upsert parameters directly from each `ExtractedFile`.

**Not started. Awaiting your review before proceeding to 1.4.c.**
