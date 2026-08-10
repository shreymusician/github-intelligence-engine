"""Checkpoint 1.4.a — live clone verification driver (throwaway script)."""
from __future__ import annotations

import time
from pathlib import Path

from acquisition.clone_workspace import RepositoryCloner
from acquisition.exceptions import RepositoryCloneError

cloner = RepositoryCloner()

targets = ["octocat/Hello-World", "public-apis/public-apis", "octocat/this-repo-does-not-exist-xyz123"]

for full_name in targets:
    print(f"=== {full_name} ===")
    start = time.monotonic()
    try:
        with cloner.clone(full_name) as path:
            elapsed = time.monotonic() - start
            entries = sorted(p.name for p in path.iterdir())
            print(f"cloned in {elapsed:.2f}s -> {path}")
            print("top-level entries:", entries[:15])
            readme_candidates = [p.name for p in path.iterdir() if p.is_file() and p.name.upper().startswith("README")]
            print("README-like files found:", readme_candidates)
        print(f"directory removed after context exit: {not path.exists()}")
    except RepositoryCloneError as exc:
        elapsed = time.monotonic() - start
        print(f"FAILED as expected in {elapsed:.2f}s: {exc}")
    print()
