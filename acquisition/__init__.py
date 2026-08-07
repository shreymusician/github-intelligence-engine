"""Repository acquisition package — Checkpoint 1.3.

selection.py (1.3.a) and storage.py (1.3.b) are implemented so far:
repository candidate selection via GitHub's Search API, and persisting
RepositoryData into PostgreSQL. Orchestration (1.3.c) - tying selection,
fetch (Checkpoint 1.1), and storage into one run - remains a separate,
not-yet-implemented sub-checkpoint; see IMPLEMENTATION_ROADMAP.md's
Checkpoint 1.3 breakdown.
"""

from acquisition.exceptions import AcquisitionStorageError, RepositoryPersistenceError
from acquisition.selection import RepositorySelector, SelectionCriteria
from acquisition.storage import RepositoryWriter

__all__ = [
    "RepositorySelector",
    "SelectionCriteria",
    "RepositoryWriter",
    "AcquisitionStorageError",
    "RepositoryPersistenceError",
]
