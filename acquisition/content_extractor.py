"""README + package manifest extraction — Checkpoint 1.4.b.

Given a directory already containing a clone (produced by
acquisition.clone_workspace.RepositoryCloner, Checkpoint 1.4.a), locates
README and known package-manifest files at the repository root, reads
them as UTF-8 text, and returns structured ExtractedFile objects.

This module owns none of the clone lifecycle - it never invokes git,
never creates or deletes a directory, and never decides when a clone is
stale. It is handed a directory that already exists and simply reads from
it. Callers (1.4.d's orchestration, once it exists) are responsible for
supplying a valid clone directory and cleaning it up afterward - exactly
the contract RepositoryCloner.clone() already provides as a context
manager.

Explicitly NOT this module's job (later sub-checkpoints):
- Persisting anything to PostgreSQL (1.4.c).
- Deciding which repositories to clone or orchestrating a batch (1.4.d).
- Any Milestone 2 analysis (technology detection, documentation quality
  scoring, etc.) - this module only extracts raw text, it does not
  interpret it.

Design decisions made here, deliberately, not left implicit:
- Root-level only, not a recursive tree walk. "Important files" (the
  roadmap's own phrase, IMPLEMENTATION_ROADMAP.md Checkpoint 1.4) means
  the repository's own primary README/manifests, not vendored or nested
  copies buried in node_modules/vendor/test-fixture directories that a
  recursive walk would also match. This also avoids walking potentially
  very large cloned trees just to find a handful of well-known root-level
  filenames. A repository_content row already supports a `file_path` with
  subdirectories (Migration 010, Checkpoint 1.4.c) if nested discovery is
  ever needed later - nothing about this decision forecloses that.
- README candidates are matched case-insensitively (README.md, readme.MD,
  Readme, ReadMe.txt, ...) since GitHub itself treats README detection
  case-insensitively for its own rendering. When multiple README variants
  exist in the same repository (e.g. both README.md and README.rst),
  exactly one is selected, by a fixed priority order (.md > .rst > .txt >
  no extension) - never more than one "readme" row, avoiding an ambiguous
  "which one is THE readme" question downstream.
- Manifest filenames are matched case-SENSITIVELY, by exact string
  comparison against each directory entry's real on-disk name - not via
  `Path.is_file()`, which would silently succeed against e.g. "Package.json"
  on this project's Windows development machine (NTFS is case-insensitive
  by default) even though npm/pip/etc. themselves are case-sensitive.
  Comparing `entry.name` (the real name git wrote) against the expected
  exact filename string keeps behavior identical regardless of the host
  filesystem's own case-sensitivity.
- Symlinks are never followed. A malicious public repository could in
  principle contain a symlink named e.g. "README.md" pointing outside the
  clone directory (even at an absolute path elsewhere on the host). Every
  candidate file is required to (a) not be a symlink and (b) resolve to a
  real path still contained within the clone directory before it is ever
  opened - defense against path traversal via a crafted repository, not a
  response to an observed incident.
- A file whose size exceeds _MAX_FILE_SIZE_BYTES is skipped (logged), not
  read into memory - README/manifest files are conventionally small (KB
  range); this is a defensive bound against an unusually large file
  matching one of the known names, not a limit expected to trigger in
  ordinary use.
- A file that fails UTF-8 decoding is skipped (logged), not stored with a
  guessed encoding or as raw bytes - matches this project's established
  practice of documenting real gaps rather than fabricating data
  (CHECKPOINT_1_3B_REPORT.md Sec5's licenses.is_osi_approved precedent).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from logging_setup import get_logger

log = get_logger(__name__)

# 5 MB - generous for any real README/manifest, a defensive bound only.
_MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024

_README_STEM = "readme"
_README_EXTENSION_PRIORITY = ["md", "rst", "txt", ""]  # "" = no extension; earlier wins

# Exact, case-sensitive filenames, per the explicitly approved manifest
# list - not extended beyond what was explicitly identified.
_MANIFEST_CONTENT_TYPES: dict[str, str] = {
    "package.json": "package_json",
    "requirements.txt": "requirements_txt",
    "pyproject.toml": "pyproject_toml",
    "setup.py": "setup_py",
    "setup.cfg": "setup_cfg",
    "Pipfile": "pipfile",
    "Gemfile": "gemfile",
    "go.mod": "go_mod",
    "Cargo.toml": "cargo_toml",
    "composer.json": "composer_json",
}


@dataclass(frozen=True)
class ExtractedFile:
    """One successfully extracted file, ready for persistence (Checkpoint
    1.4.c) - field names deliberately match repository_content's planned
    columns (content_type, file_path, content, content_hash,
    content_size_bytes) for a direct, translation-free mapping.
    """

    content_type: str
    file_path: str
    content: str
    content_hash: str
    content_size_bytes: int


def extract_content(clone_dir: Path) -> list[ExtractedFile]:
    """Extract the README (at most one) and any known manifest files found
    at the root of `clone_dir`. Files that don't exist are simply absent
    from the result - this is not an error. Files that exist but cannot
    be safely read (symlink, escapes clone_dir, too large, undecodable)
    are skipped and logged, not raised as an exception - one unreadable
    file must not prevent extracting the others.
    """
    clone_root = clone_dir.resolve()
    safe_entries = [entry for entry in clone_dir.iterdir() if _is_safe_regular_file(entry, clone_root)]
    entry_names = {entry.name for entry in safe_entries}
    entries_by_name = {entry.name: entry for entry in safe_entries}

    results: list[ExtractedFile] = []

    readme_entry = _select_readme(safe_entries)
    if readme_entry is not None:
        extracted = _read_file(readme_entry, "readme", clone_root)
        if extracted is not None:
            results.append(extracted)

    for filename, content_type in _MANIFEST_CONTENT_TYPES.items():
        if filename in entry_names:
            extracted = _read_file(entries_by_name[filename], content_type, clone_root)
            if extracted is not None:
                results.append(extracted)

    log.info("content_extraction_completed clone_dir=%s files_extracted=%s", clone_root, len(results))
    return results


def _is_safe_regular_file(entry: Path, clone_root: Path) -> bool:
    """True only for a real, regular file that stays within clone_root once
    resolved - rejects symlinks and anything that would escape the clone
    directory (see module docstring)."""
    if entry.is_symlink():
        return False
    try:
        if not entry.is_file():
            return False
        resolved = entry.resolve(strict=True)
    except OSError:
        return False
    try:
        resolved.relative_to(clone_root)
    except ValueError:
        return False
    return True


def _select_readme(entries: list[Path]) -> Path | None:
    """Pick exactly one README candidate, by fixed extension priority, from
    among possibly several (README.md AND README.rst both present, etc.).
    """
    candidates: list[tuple[int, Path]] = []
    for entry in entries:
        stem, dot, ext = entry.name.partition(".")
        if stem.lower() != _README_STEM:
            continue
        ext_lower = ext.lower() if dot else ""
        if ext_lower in _README_EXTENSION_PRIORITY:
            candidates.append((_README_EXTENSION_PRIORITY.index(ext_lower), entry))

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def _read_file(path: Path, content_type: str, clone_root: Path) -> ExtractedFile | None:
    rel_path = path.relative_to(clone_root).as_posix()
    try:
        size = path.stat().st_size
    except OSError as exc:
        log.warning("content_extraction_skipped_stat_failed file_path=%s error=%s", rel_path, exc)
        return None

    if size > _MAX_FILE_SIZE_BYTES:
        log.warning(
            "content_extraction_skipped_too_large file_path=%s size_bytes=%s limit_bytes=%s",
            rel_path,
            size,
            _MAX_FILE_SIZE_BYTES,
        )
        return None

    try:
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8")
    except OSError as exc:
        log.warning("content_extraction_skipped_read_failed file_path=%s error=%s", rel_path, exc)
        return None
    except UnicodeDecodeError:
        log.warning("content_extraction_skipped_undecodable file_path=%s", rel_path)
        return None

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    log.info("content_extracted file_path=%s content_type=%s size_bytes=%s", rel_path, content_type, len(raw_bytes))
    return ExtractedFile(
        content_type=content_type,
        file_path=rel_path,
        content=text,
        content_hash=content_hash,
        content_size_bytes=len(raw_bytes),
    )
