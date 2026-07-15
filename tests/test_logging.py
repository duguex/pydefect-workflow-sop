"""Tests for vasp_sop.core.logging — file handler and terminal level."""

import logging
from pathlib import Path

import pytest

from vasp_sop.core.logging import setup_file_logging


@pytest.fixture
def isolated_root_logger():
    """Restore the process-wide root logger after each logging test."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    attr_name = "_vasp_sop_stderr_handler"
    had_stderr_handler = hasattr(root, attr_name)
    original_stderr_handler = getattr(root, attr_name, None)

    for handler in original_handlers:
        root.removeHandler(handler)
    if had_stderr_handler:
        delattr(root, attr_name)
    root.setLevel(logging.INFO)

    try:
        yield root
    finally:
        configured_handlers = list(root.handlers)
        for handler in configured_handlers:
            root.removeHandler(handler)
            if isinstance(handler, logging.FileHandler):
                handler.close()
        root.setLevel(original_level)
        for handler in original_handlers:
            root.addHandler(handler)
        if had_stderr_handler:
            setattr(root, attr_name, original_stderr_handler)


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

    console_handlers = [
        handler
        for handler in root.handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
    ]
    assert console_handlers
    assert all(handler.level == logging.WARNING for handler in console_handlers)


def test_idempotent_calls_dont_duplicate(
    tmp_path: Path, isolated_root_logger: logging.Logger
):
    isolated_root_logger.addHandler(logging.StreamHandler())

    setup_file_logging(tmp_path)
    before = len(isolated_root_logger.handlers)
    setup_file_logging(tmp_path)

    assert len(isolated_root_logger.handlers) == before
