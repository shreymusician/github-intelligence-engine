"""Deterministic feature extraction over already-persisted repository content.

Checkpoint 2.2 (Technology Stack Detection). Consumes repository_content
rows (1.4) - never calls GitHub, never clones, never touches PostgreSQL
itself. Mirrors acquisition/ as a top-level package, per this project's
established structure (acquisition/, github_client/), not the empty
src/processing/ scaffold from the original architecture document.
"""
