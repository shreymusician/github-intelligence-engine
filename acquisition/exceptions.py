"""Typed exceptions for the repository storage layer — Checkpoint 1.3.b.

Mirrors github_client/exceptions.py's convention: a narrow, typed
vocabulary so callers (Checkpoint 1.3.c's orchestration, once it exists)
can distinguish failure modes programmatically instead of parsing
psycopg error messages themselves.
"""

from __future__ import annotations


class AcquisitionStorageError(Exception):
    """Base class for all errors raised by acquisition.storage."""


class RepositoryPersistenceError(AcquisitionStorageError):
    """Raised when persisting a RepositoryData to PostgreSQL fails.

    By the time this is raised, RepositoryWriter's transaction has
    already been rolled back — no partial writes (owner, license, topics,
    repository row, snapshot) survive a failed upsert_repository() call.
    """

    def __init__(self, full_name: str, cause: Exception) -> None:
        self.full_name = full_name
        self.cause = cause
        super().__init__(f"Failed to persist repository {full_name!r}: {cause}")
