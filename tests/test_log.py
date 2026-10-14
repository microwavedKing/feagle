"""Tests for logging setup."""

from __future__ import annotations

import logging

from feagle import log


def test_setup_logging_configures_root():
    logger = log.setup_logging("DEBUG")
    assert logger.name == log.ROOT_LOGGER
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    assert logger.propagate is False


def test_setup_logging_is_idempotent():
    log.setup_logging("INFO")
    log.setup_logging("WARNING")
    logger = logging.getLogger(log.ROOT_LOGGER)
    assert len(logger.handlers) == 1
    assert logger.level == logging.WARNING


def test_setup_logging_reads_env(monkeypatch):
    monkeypatch.setenv("FEAGLE_LOG_LEVEL", "ERROR")
    logger = log.setup_logging()
    assert logger.level == logging.ERROR


def test_get_logger_child_name():
    assert log.get_logger("indexer.scanner").name == "feagle.indexer.scanner"
    assert log.get_logger().name == log.ROOT_LOGGER
