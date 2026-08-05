"""Minimal structured console logging — Checkpoint 1.1.b.

Provides get_logger(), the single entry point future modules (starting
with Checkpoint 1.1.c's GitHub client) use to obtain a configured logger.
Level is sourced from config/settings.py's LOG_LEVEL (Checkpoint 1.1.a) -
this module does not read the environment itself.

Deliberately excludes: file logging, log rotation, JSON output, Sentry,
OpenTelemetry, metrics, tracing. Those remain deferred to Checkpoint 0.5.

Callers are responsible for never passing secret values (e.g. the raw
GITHUB_TOKEN or an Authorization header) into a log call - this module
has no way to redact content it doesn't control.
"""

from __future__ import annotations

import logging
import sys

from config.settings import get_settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

_configured = False


def _configure_root() -> None:
    """Attach a single console handler to the root logger, once per process."""
    global _configured
    if _configured:
        return

    settings = get_settings()
    root = logging.getLogger()
    root.setLevel(settings.log_level)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, configuring the root handler on first use.

    Safe to call repeatedly (including once per module, at import time) -
    the console handler is attached to the root logger exactly once per
    process, so repeated calls never produce duplicate log lines.
    """
    _configure_root()
    return logging.getLogger(name)
