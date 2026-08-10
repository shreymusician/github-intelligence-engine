"""Temporary shallow-clone workspace — Checkpoint 1.4.a.

Provides RepositoryCloner: given a repository's `owner/repo` full name,
shallow-clones it (`git clone --depth 1`) into a fresh OS temporary
directory and yields that directory's path as a context manager. The
directory — and everything git wrote into it — is always removed on exit,
success or failure. This module has no notion of a persistent cache: per
the project's V1 lifecycle decision, PostgreSQL's `repository_content`
table (Checkpoint 1.4.c) is the durable store; the clone is scratch space
that must not outlive the single fetch-extract operation it exists for.

Explicitly NOT this module's job (later sub-checkpoints):
- Locating/reading README or manifest files inside the clone (1.4.b).
- Persisting anything to PostgreSQL (1.4.c).
- Orchestrating this across many repositories (1.4.d).

Security decisions made here, deliberately, not left implicit:
- Clones are always unauthenticated (plain `https://github.com/{full_name}.git`,
  no token). Every repository this project acquires is public (selected via
  GitHub's public Search API, Checkpoint 1.3.a) — there is no reason to
  embed GITHUB_TOKEN in a clone URL, and doing so would put it in this
  process's argv (visible to other processes on the same machine via
  process listing) and potentially in the clone's own `.git/config`. Not
  using the token at all removes that entire risk class rather than trying
  to redact it after the fact.
- `subprocess.run()` is always called with an argument list (never
  `shell=True`, never a shell-interpolated string) — no shell injection
  surface exists regardless of what `full_name` contains.
- `full_name` is validated against GitHub's own `owner/repo` shape before
  it ever reaches a subprocess argument or is interpolated into a URL —
  defense in depth against a malformed/unexpected value, even though in
  practice `full_name` always originates from `repositories.full_name`
  (itself sourced from GitHub's own API responses, Checkpoint 1.3).
- The temporary directory comes from `tempfile.mkdtemp()` (the OS temp
  directory, e.g. `%TEMP%`/`/tmp`), never a path under this repository —
  this makes "cannot accidentally be committed" a structural property, not
  a `.gitignore` hygiene concern.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from acquisition.exceptions import RepositoryCloneError
from config.settings import Settings, get_settings
from logging_setup import get_logger

log = get_logger(__name__)

# GitHub's own owner/repo naming rules (alphanumeric, hyphen, underscore,
# dot; no leading/trailing separator) - not a full spec implementation,
# just enough to reject anything that couldn't possibly be a real GitHub
# full_name before it reaches subprocess args or a URL.
_FULL_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?/[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
)

_CLONE_DIR_PREFIX = "ghia_clone_"


def _force_remove_readonly(func, path: str, exc: BaseException) -> None:
    """shutil.rmtree onexc handler: git writes some files (e.g. under
    .git/objects/pack/) read-only, which rmtree cannot delete on Windows
    without first clearing the read-only bit. Without this handler,
    rmtree(..., ignore_errors=True) would silently leave the directory
    (or part of it) behind instead of actually cleaning it up - discovered
    via this sub-checkpoint's own live verification, not assumed.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_clone_dir(clone_dir: str, full_name: str) -> None:
    try:
        shutil.rmtree(clone_dir, onexc=_force_remove_readonly)
    except OSError as exc:
        # Cleanup failure is logged, not raised - this runs in a `finally`
        # block and must never mask an original clone/extraction error.
        log.warning("clone_workspace_cleanup_failed full_name=%s path=%s error=%s", full_name, clone_dir, exc)


class RepositoryCloner:
    """Shallow-clones repositories into temporary, always-cleaned-up directories.

    Holds no state beyond its own configuration (timeout) - matches
    acquisition/selection.py's RepositorySelector precedent of holding no
    state beyond what it was constructed with.
    """

    def __init__(self, settings: Settings | None = None, timeout_seconds: int | None = None) -> None:
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else (settings or get_settings()).content_clone_timeout_seconds
        )

    @contextmanager
    def clone(self, full_name: str) -> Iterator[Path]:
        """Shallow-clone `full_name` into a fresh temporary directory, yield
        its path, and always remove the directory on exit - whether the
        clone succeeded, failed, or the caller's own code (inside the
        `with` block) raised.

        Raises RepositoryCloneError if `full_name` is malformed, the clone
        process fails (non-zero exit), or it times out.
        """
        if not _FULL_NAME_PATTERN.match(full_name):
            raise RepositoryCloneError(full_name, f"malformed full_name: {full_name!r}")

        clone_dir = tempfile.mkdtemp(prefix=_CLONE_DIR_PREFIX)
        try:
            self._run_git_clone(full_name, clone_dir)
            yield Path(clone_dir)
        finally:
            _remove_clone_dir(clone_dir, full_name)

    def _run_git_clone(self, full_name: str, dest_dir: str) -> None:
        url = f"https://github.com/{full_name}.git"
        argv = [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            "--quiet",
            url,
            dest_dir,
        ]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.error("repository_clone_timeout full_name=%s timeout_seconds=%s", full_name, self._timeout_seconds)
            raise RepositoryCloneError(full_name, f"timed out after {self._timeout_seconds}s") from exc
        except OSError as exc:
            log.error("repository_clone_failed full_name=%s error=%s", full_name, exc.__class__.__name__)
            raise RepositoryCloneError(full_name, f"failed to start git: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.strip()
            log.error(
                "repository_clone_failed full_name=%s returncode=%s stderr=%s",
                full_name,
                result.returncode,
                stderr,
            )
            raise RepositoryCloneError(full_name, f"git clone exited {result.returncode}: {stderr}")

        log.info("repository_cloned full_name=%s", full_name)
