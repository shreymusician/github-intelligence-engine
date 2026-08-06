# Security History Rewrite Report

**Date:** 2026-08-06
**Performed by:** Claude Code, at explicit user request
**Status:** ✅ Complete — history purged, verified clean; two related defects found and fixed; **token rotation still outstanding (explicitly not done here, per your instruction)**

---

## 1. Purpose

A real, currently-valid GitHub Personal Access Token had been committed to git in plaintext (`.env`, `PAT.txt`, `PAT (2).txt`), flagged as Critical in `CHECKPOINT_1_1_FINAL_REPORT.md` §4/§6. This report documents the one-time cleanup that removed those files from the entire git history — not just the latest commit — so the secret no longer exists anywhere in the repository's object database, followed by full verification.

This was a repository-hygiene operation only. No project source code was modified as part of the history rewrite itself (two pre-existing defects discovered *during* verification were fixed separately — see §7 — neither is "new functionality").

---

## 2. Files Removed From History

| File | Reason |
|---|---|
| `.env` | Contained a real `GITHUB_TOKEN` (from commit `2cdd8b2` onward) plus local Postgres/Redis dev config |
| `PAT.txt` | Contained the same real GitHub PAT in plaintext |
| `PAT (2).txt` | Contained the same real GitHub PAT in plaintext (duplicate) |

Confirmed via `git log --all --name-status` (Phase 1, run before the rewrite) that all three existed **only at the repository root** — no nested copies anywhere in history.

---

## 3. Commits Rewritten / Removed

The repository had **24 commits** across all refs before the rewrite; **23 commits** after. `git-filter-repo` rewrites every commit's tree (new hashes throughout, since each commit's tree hash depends on its parent), and **drops commits that become empty** once the target paths are removed — exactly one commit, `2cdd8b2` ("Debugging"), consisted entirely of adding the three sensitive files and was therefore dropped entirely rather than kept as an empty commit.

**Commits that touched the three files (all 8, pre-rewrite hashes):**

| Old hash | Message | What happened |
|---|---|---|
| `68e5e85` | Resolved Errors | `.env` addition stripped from tree; commit kept (had other content) |
| `d594153` | Basic Build Completed | `.env` modification stripped; commit kept |
| `857aaec` | 9th Migration | `.env` modification stripped; commit kept |
| `ba890ba` | Review | `PAT.txt` addition stripped; commit kept |
| `60ce26d` | Untrack .env and PAT.txt; add PAT*.txt to .gitignore | `.env`/`PAT.txt` deletion stripped (nothing left to delete); commit kept (added `.gitignore` rules) |
| `2cdd8b2` | Debugging | **Commit dropped entirely** — 100% of its content was the three sensitive files |
| `eee970e` | Probability Model | Unaffected content kept; new hash `5c3edd0` |
| `6aeb60c` | Security Analysis | Unaffected content kept; new hash `54c9a4b` (new `main` HEAD) |

**All commit messages, authors, and timestamps were preserved exactly** — confirmed by comparing `git log --pretty=format:'%an %ad %s'` before and after; only the hashes and, for the 8 commits above, the tree contents changed. The single branch (`main`) and zero tags in the repository were both carried through (no tags existed to lose).

---

## 4. Commands Executed

### Phase 0 — Safety
```
git branch pre-history-rewrite-backup
git bundle create <scratchpad>/pre-history-rewrite-full-backup.bundle --all
git bundle verify <scratchpad>/pre-history-rewrite-full-backup.bundle
```

### Phase 1 — Verify affected history
```
git log --all --name-status -- .env
git log --all --name-status -- PAT.txt
git log --all --name-status -- "PAT (2).txt"
git log --all --name-only --pretty=format: | sort -u | grep -iE "(^|/)(\.env|pat.*\.txt)$"
```

### Phase 2 — Tooling
```
pip install git-filter-repo          # into the project venv
git filter-repo --version            # verified: 2.47.0
```

### Phase 3 — Rewrite
```
git filter-repo --invert-paths --path ".env" --path "PAT.txt" --path "PAT (2).txt" --force
git remote add origin https://github.com/shreymusician/github-intelligence-engine.git
```

### Phase 4 — Cleanup
```
git reflog expire --expire=now --all
git gc --prune=now --aggressive
git fsck --full
```

### Phase 5 — Verification
```
git log --all -- .env
git log --all -- PAT.txt
git log --all -- "PAT (2).txt"
git ls-files
git grep -n "github_pat_" $(git rev-list --all)
git grep -n "ghp_" $(git rev-list --all)
git status
git check-ignore -v ".env" "PAT.txt" "PAT (2).txt"
```

---

## 5. The `origin` Remote

As anticipated in the pre-execution plan, `git-filter-repo` **automatically removed the `origin` remote** as its default safety behavior (it assumes a rewritten history should not be pushed casually against a remote that still holds the old, secret-containing commits). This is documented, expected `git-filter-repo` behavior, not an error. It was restored immediately after the rewrite: `git remote add origin https://github.com/shreymusician/github-intelligence-engine.git`. Confirmed no push occurred — `origin/main`'s remote-tracking ref was empty until the remote was re-added; nothing was pushed during this cleanup (see §9 for what pushing would require).

---

## 6. Unexpected Side Effect Found and Corrected

**`git-filter-repo` updates the working tree to match the rewritten HEAD, not just the git object database.** Since `.env`, `PAT.txt`, and `PAT (2).txt` were tracked at the pre-rewrite HEAD and are untracked in the rewritten history, the rewrite **deleted all three files from disk** — a direct conflict with your explicit instruction not to change local `.env` contents.

**Corrected immediately:** cloned the Phase-0 bundle backup into a temporary directory, checked out the three files' exact content from the original (pre-rewrite) commit `6aeb60c`, and copied them back into the working directory — restoring disk state without re-adding them to git tracking. Verified:
- Byte-for-byte content match against the pre-rewrite committed blobs (confirmed for `.env`'s `GITHUB_TOKEN` line and both `PAT*.txt` files; an initial `diff` "mismatch" for the PAT files turned out to be a trailing-newline artifact of the comparison method, not a real content difference — confirmed via `cat -A`).
- The temporary restore clone was deleted after use; nothing from it remains on disk.

**Lesson for future history rewrites:** `git-filter-repo` (and `filter-branch`) are not purely "history" operations — they check out the new HEAD into the working tree. Any currently-tracked file being removed will disappear from disk, not just from git. This should be anticipated and backed up explicitly next time, not assumed to be scoped to `.git/` alone.

---

## 7. Second Defect Found: `.gitignore` Never Actually Worked

While verifying "`.gitignore` still ignores them" (a Phase 5 requirement), `git check-ignore -v ".env"` returned **no match** — meaning `.gitignore` was not actually ignoring `.env` (or `PAT*.txt`, `*.pem`, `*.key`, `secrets.json`, `.env.local`, `.env.*.local`) at all, despite every prior checkpoint report (`CHECKPOINT_1_1A_REPORT.md` §6, `CHECKPOINT_1_1_FINAL_REPORT.md` §6) stating these were "confirmed gitignored."

**Root cause:** gitignore syntax does not support inline trailing comments. A line like:
```
.env                      # Actual environment variables (local only)
```
is not `.env` with a comment — the `#` is only a comment character when it is the **first character of the line**. Everywhere else, it's literal. That entire line was being parsed as one literal (and unmatchable) pattern: a filename consisting of `.env`, many spaces, `#`, and the comment text. This bug existed in `.gitignore` from whenever those lines were first written (predates this cleanup) and had never been substantively tested with `git check-ignore` — every prior "confirmed gitignored" claim was based on the files simply not being re-added, not on the ignore rule actually functioning.

**Fixed:** moved each affected comment onto its own line above the pattern it describes, for exactly the security-relevant block (`.env`, `.env.local`, `.env.*.local`, `*.pem`, `*.key`, `secrets.json`, `PAT*.txt`) — no other part of `.gitignore` was touched, since other inline-comment lines elsewhere in the file (e.g. line 17's `*.py[cod]` pattern) are outside this task's security scope and are flagged here for separate follow-up rather than fixed opportunistically.

**Verified fixed:**
```
$ git check-ignore -v ".env"
.gitignore:14:.env	.env
$ git check-ignore -v "PAT.txt"
.gitignore:25:PAT*.txt	PAT.txt
$ git check-ignore -v "PAT (2).txt"
.gitignore:25:PAT*.txt	PAT (2).txt
```

---

## 8. Verification Evidence (Phase 5, final run)

| Check | Result |
|---|---|
| `git log --all -- .env` | Empty — **PASS**, no commit anywhere references `.env` |
| `git log --all -- PAT.txt` | Empty — **PASS** |
| `git log --all -- "PAT (2).txt"` | Empty — **PASS** |
| `git ls-files` (grepped for the three names) | No matches — **PASS**, none are tracked |
| `git grep -n "github_pat_"` across every commit on every ref | No matches — **PASS** |
| `git grep -n "ghp_"` across every commit on every ref | One match: `tests/test_github_client.py:35: TEST_TOKEN = "ghp_test_token_never_logged_1234567890"` — **expected and benign**, a fake constant written for the 1.1.g test suite, not a real credential (its own name says so) |
| `git status` | Clean — only the `.gitignore` fix is an unstaged modification |
| `git check-ignore -v` on all three files | All three now genuinely match `.gitignore` rules — **PASS** (this failed before the §7 fix) |
| `.env`/`PAT.txt`/`PAT (2).txt` present on disk with correct content | **PASS** — restored in §6, confirmed byte-identical to pre-rewrite committed content |
| Full test suite (`pytest tests/test_github_client.py`) | 67/67 passed — project unaffected functionally |

All items from your requested outcome list are satisfied:
- ✅ None of the three files exist anywhere in git history
- ✅ None of them are tracked
- ✅ `.gitignore` permanently ignores them (fixed during this cleanup — it did not, before)
- ✅ Only the intentional fake test token remains

---

## 9. Repository Health

```
.git size: 936K → 498K
git count-objects -v:
  count: 0
  in-pack: 193
  packs: 1
  size-pack: 374
  prune-packable: 0
  garbage: 0
  size-garbage: 0
git fsck --full: exit code 0, no output (no corruption, no dangling objects)
```

Repository is healthy: single clean pack, zero garbage, zero dangling objects, `fsck` reports no integrity issues.

---

## 10. Backups Created

| Backup | Location | Status |
|---|---|---|
| `pre-history-rewrite-backup` branch | Local, in-repo | **Not a true rollback point** — `git-filter-repo` rewrites all refs by default, so this branch was rewritten identically to `main` (same new HEAD, `54c9a4b`). Created per your explicit Phase 0 instruction; kept for reference but does not preserve pre-rewrite state. |
| `pre-history-rewrite-full-backup.bundle` | External, outside `.git` (session scratchpad) | **The real rollback point.** Contains the complete pre-rewrite repository — all 4 refs (`main`, `pre-history-rewrite-backup`, `origin/main`, `HEAD`), all 24 original commits, secrets included. To restore: `git clone <bundle-path> <new-dir>`, or `git fetch <bundle-path> main:some-branch` into a fresh clone. This file itself contains the secret — treat it as sensitive, delete it once you're confident it's no longer needed, and never publish it anywhere. |

This tension (an in-repo backup branch necessarily gets cleaned by the same operation it's meant to protect against) was flagged before executing; see the pre-execution discussion.

---

## 11. Final Security Status

| Item | Status |
|---|---|
| Secret removed from all git history | ✅ Done, verified |
| Secret removed from git tracking (index) | ✅ Done, verified |
| `.gitignore` actually prevents re-tracking | ✅ Fixed and verified (was broken before) |
| Local `.env`/`PAT*.txt` content preserved on disk | ✅ Restored after an unintended deletion (§6) |
| Token rotated | ❌ **Not done** — explicitly out of scope per your instruction ("Do not modify my GitHub Personal Access Token. Do not create a new token.") |
| Pushed to remote | ❌ Not done — see §12 |

**The token itself is still the same, still valid, and was in git history (even if only locally) at some point — I continue to recommend rotating it** at https://github.com/settings/tokens once you're ready, independent of this cleanup. This cleanup removed it from the repository; it does not un-expose a credential that may have already been read elsewhere (e.g. the chat transcript this project's early checkpoints flagged).

---

## 12. Push Consequences (Not Executed — Informational Only, Per Your Original Instruction §10)

`origin/main` currently points to `857aaec` ("9th Migration") in the **old** (pre-rewrite) commit graph — it was already 7 commits behind local `main` before this cleanup even started (confirmed in the pre-execution investigation), so it never had the secret-containing commits in the first place.

If you ever want to sync the rewritten local `main` to `origin`, the required command would be:
```
git push --force-with-lease origin main
```
**Consequences if you do this:**
- Every commit hash on `origin/main` from `857aaec` onward would change (rewritten history is not a superset of the old history — it's a parallel, incompatible history from that point forward).
- `--force-with-lease` (safer than `--force`) refuses the push if `origin/main` has moved since you last fetched, protecting against clobbering someone else's work — but since nothing has changed remotely since your last fetch in this session, it would succeed.
- Anyone who has already cloned or fetched this repository from GitHub would need to re-clone or hard-reset their local copy (`git fetch && git reset --hard origin/main`) — their existing local history would diverge and normal `git pull` would fail/conflict.
- Since `origin/main` never received the secret-containing commits, this push is a normal (if unusual) history change from GitHub's perspective — not a "remove a leaked secret from a public history" emergency. Low urgency, but still worth being deliberate about.

**Not executed. Your explicit confirmation would be required before any push.**

---

## 13. Checkpoint Status

Per your instructions, this was a standalone security cleanup — **Checkpoint 1.2 has not been started.** Awaiting your review of this report before any further work.
