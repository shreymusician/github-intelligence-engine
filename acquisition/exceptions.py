"""Typed exceptions for the repository acquisition layer.

Mirrors github_client/exceptions.py's convention: a narrow, typed
vocabulary so callers can distinguish failure modes programmatically
instead of parsing error messages themselves.
"""

from __future__ import annotations


class AcquisitionStorageError(Exception):
    """Base class for all errors raised by acquisition.storage — Checkpoint 1.3.b."""


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


class ContentAcquisitionError(Exception):
    """Base class for all errors raised by repository content acquisition —
    Checkpoint 1.4 (clone workspace, extraction, content persistence).
    """


class RepositoryCloneError(ContentAcquisitionError):
    """Raised when a shallow git clone of a repository fails or times out.

    Never includes GITHUB_TOKEN or any credential — clones in this project
    are always unauthenticated (public repositories only), so no secret
    exists to leak here in the first place.
    """

    def __init__(self, full_name: str, reason: str) -> None:
        self.full_name = full_name
        self.reason = reason
        super().__init__(f"Failed to clone repository {full_name!r}: {reason}")


class ContentPersistenceError(ContentAcquisitionError):
    """Raised when persisting ExtractedFile rows to repository_content fails.

    By the time this is raised, ContentWriter's transaction has already
    been rolled back — no partial rows for the batch survive a failed
    upsert_content() call.
    """

    def __init__(self, repository_id: object, cause: Exception) -> None:
        self.repository_id = repository_id
        self.cause = cause
        super().__init__(
            f"Failed to persist content for repository {repository_id!r}: {cause}"
        )
