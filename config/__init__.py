"""Minimal configuration package for Checkpoint 1.1 (GitHub API Client).

Scope is intentionally narrow: only the environment variables Checkpoint 1.1
actually reads (GITHUB_TOKEN, LOG_LEVEL). YAML configs, multi-environment
profiles, and secret-manager integration are deferred to Checkpoint 0.4.
"""

from config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
