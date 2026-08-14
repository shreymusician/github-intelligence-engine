"""Typed exceptions for the processing layer — Checkpoint 2.2.b.

Mirrors acquisition/exceptions.py's convention exactly: a narrow, typed
vocabulary so callers can distinguish failure modes programmatically
instead of parsing error messages themselves.
"""

from __future__ import annotations


class ProcessingError(Exception):
    """Base class for all errors raised by the processing layer."""


class TechnologyPersistenceError(ProcessingError):
    """Raised when persisting technology classifications to PostgreSQL fails.

    By the time this is raised, TechnologyWriter's transaction has
    already been rolled back — no partial technology set for that
    repository survives a failed upsert_technologies() call.
    """

    def __init__(self, repository_id: object, cause: Exception) -> None:
        self.repository_id = repository_id
        self.cause = cause
        super().__init__(f"Failed to persist technologies for repository {repository_id!r}: {cause}")
