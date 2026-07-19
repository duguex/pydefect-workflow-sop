"""Tests for vasp_sop.core.logging — file handler and terminal level."""

import logging
import sys
from pathlib import Path

import pytest

from vasp_sop.core.logging import setup_file_logging


@pytest.fixture
def isolated_root_logger():
    """Restore the process-wide root logger after each logging test."""
    from vasp_sop.core.logging import teardown_file_logging

    root = logging.getLogger()
    teardown_file_logging()
    original_handlers = list(root.handlers)
    original_level = root.level
    root.setLevel(logging.INFO)

    try:
        yield root
    finally:
        teardown_file_logging()
        for handler in list(root.handlers):
            root.removeHandler(handler)
            if isinstance(handler, logging.FileHandler):
                handler.close()
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)


def test_file_handler_writes_and_terminal_warning_only(
    tmp_path: Path, isolated_root_logger: logging.Logger
):
    root = isolated_root_logger
    root.addHandler(logging.StreamHandler())  # stderr by default

    setup_file_logging(tmp_path)

    logging.info("should-only-be-in-file")
    logging.warning("should-be-in-both")

    log_file = tmp_path / "batch_run.log"
    assert log_file.is_file()
    content = log_file.read_text()
    assert "should-only-be-in-file" in content
    assert "should-be-in-both" in content

    stderr_handlers = [
        handler
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and handler.stream is sys.stderr
    ]
    assert stderr_handlers
    assert all(handler.level == logging.WARNING for handler in stderr_handlers)


def test_idempotent_calls_dont_duplicate(
    tmp_path: Path, isolated_root_logger: logging.Logger
):
    isolated_root_logger.addHandler(logging.StreamHandler())

    setup_file_logging(tmp_path)
    before = len(isolated_root_logger.handlers)
    setup_file_logging(tmp_path)

    assert len(isolated_root_logger.handlers) == before
