"""Minimal configuration loader — Checkpoint 1.1.a.

Loads and validates the environment variables Checkpoint 1.1 (GitHub API
Client) needs: GITHUB_TOKEN (required) and LOG_LEVEL (optional, default
INFO, consumed by Checkpoint 1.1.b's logger).

Deliberately excludes: YAML configuration, multiple environment profiles,
secret-manager integration, and any variable not read by Checkpoint 1.1
(e.g. POSTGRES_*, REDIS_*, ENVIRONMENT). Those remain Checkpoint 0.4's
responsibility.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid.

    Never includes secret values in its message — only variable names.
    """


@dataclass(frozen=True)
class Settings:
    """Validated configuration for Checkpoint 1.1.

    `github_token` is intentionally excluded from `__repr__`/`__str__`
    so it can never appear in a log line, traceback, or REPL echo.
    """

    github_token: str
    log_level: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Settings(github_token='***redacted***', log_level={self.log_level!r})"


def _require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        raise ConfigurationError(
            f"Missing required environment variable: {name}. "
            f"Set it in your .env file (see .env.example) or the shell environment."
        )
    return value


def _validate_log_level(name: str, raw: str) -> str:
    value = raw.strip().upper()
    if value not in _VALID_LOG_LEVELS:
        allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
        raise ConfigurationError(
            f"Invalid value for {name}: {raw!r}. Must be one of: {allowed}."
        )
    return value


def load_settings() -> Settings:
    """Load and validate configuration. Raises ConfigurationError on failure.

    Not cached — callers that want a process-wide singleton should use
    `get_settings()` instead. Kept separate so tests can call this
    repeatedly with different `os.environ` states without cache staleness.
    """
    load_dotenv(dotenv_path=_ENV_FILE, override=False)

    github_token = _require("GITHUB_TOKEN")
    log_level_raw = os.environ.get("LOG_LEVEL", "INFO")
    log_level = _validate_log_level("LOG_LEVEL", log_level_raw)

    return Settings(github_token=github_token, log_level=log_level)


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton, loading it on first call."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
