"""Repository acquisition package — Checkpoint 1.3.

selection.py (1.3.a) is the only module implemented so far: repository
candidate selection via GitHub's Search API. Storage (1.3.b) and
orchestration (1.3.c) are separate, not-yet-implemented sub-checkpoints -
see IMPLEMENTATION_ROADMAP.md's Checkpoint 1.3 breakdown.
"""

from acquisition.selection import RepositorySelector, SelectionCriteria

__all__ = [
    "RepositorySelector",
    "SelectionCriteria",
]
