"""Logging setup.

feagle logs to stderr with a plain line format. Under systemd user units
journald captures stderr directly, so no python-systemd dependency is needed.
"""

from __future__ import annotations

import logging
import os
import sys

ROOT_LOGGER = "feagle"

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int | str | None = None) -> logging.Logger:
    """Configure the ``feagle`` logger tree and return its root logger.

    The level may be passed explicitly or taken from ``FEAGLE_LOG_LEVEL``.
    Calling this more than once is safe: handlers are replaced, not appended.
    """
    if level is None:
        level = os.environ.get("FEAGLE_LOG_LEVEL", "INFO")

    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.handlers[:] = [handler]
    logger.propagate = False
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger, e.g. ``get_logger("indexer.scanner")``."""
    if not name:
        return logging.getLogger(ROOT_LOGGER)
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")
