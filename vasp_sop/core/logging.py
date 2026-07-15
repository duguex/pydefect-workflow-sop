"""Batch-loop file logging setup."""

from __future__ import annotations

import logging
from pathlib import Path

_STDERR_HANDLER_ATTR = "_vasp_sop_stderr_handler"


def setup_file_logging(root: Path, *, log_path: Path | None = None) -> None:
    """Enable file logging for batch loop mode.

    - File handler at INFO → ``{root}/batch_run.log``
    - Existing stderr handler lifted to WARNING (terminal quiet)
    - Call once at loop start.
    """
    fp = log_path or (root / "batch_run.log")
    root_logger = logging.getLogger()

    # Stash the current stderr handler so we don't add a second one.
    existing = getattr(root_logger, _STDERR_HANDLER_ATTR, None)
    if existing is not None:
        return  # already configured

    # Promote the console handler we already have (if any).
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.StreamHandler) and handler.stream is not None:
            handler.setLevel(logging.WARNING)
            setattr(root_logger, _STDERR_HANDLER_ATTR, handler)

    file_handler = logging.FileHandler(str(fp), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    logging.info("─── batch run loop started, log: %s ───", fp)
