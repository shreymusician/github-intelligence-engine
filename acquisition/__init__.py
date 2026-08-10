"""Repository acquisition package — Checkpoint 1.3.

selection.py (1.3.a), storage.py (1.3.b), and pipeline.py (1.3.c) are
implemented: repository candidate selection via GitHub's Search API,
persisting RepositoryData into PostgreSQL, and orchestrating the two
into one batch run. Sub-checkpoint 1.3.d and later (see
IMPLEMENTATION_ROADMAP.md's Checkpoint 1.3 breakdown) are not yet
implemented.
"""

from acquisition.clone_workspace import RepositoryCloner
from acquisition.content_extractor import ExtractedFile, extract_content
from acquisition.exceptions import (
    AcquisitionStorageError,
    ContentAcquisitionError,
    RepositoryCloneError,
    RepositoryPersistenceError,
)
from acquisition.pipeline import AcquisitionPipeline, AcquisitionStats, PipelineResult, RepositoryFailure
from acquisition.selection import RepositorySelector, SelectionCriteria
from acquisition.storage import RepositoryWriter

__all__ = [
    "RepositorySelector",
    "SelectionCriteria",
    "RepositoryWriter",
    "AcquisitionStorageError",
    "RepositoryPersistenceError",
    "AcquisitionPipeline",
    "AcquisitionStats",
    "PipelineResult",
    "RepositoryFailure",
    "RepositoryCloner",
    "ContentAcquisitionError",
    "RepositoryCloneError",
    "ExtractedFile",
    "extract_content",
]
