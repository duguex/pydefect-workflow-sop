"""Batch-loop file logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_STDERR_HANDLER_ATTR = "_vasp_sop_stderr_handler"
logger = logging.getLogger(__name__)



def setup_file_logging(root: Path, *, log_path: Path | None = None) -> None:
    """Enable file logging for batch loop mode.

    - File handler at INFO → ``{root}/batch_run.log``
    - Existing stderr handler lifted to WARNING (terminal quiet)
    - Call once at loop start.
    """
    fp = log_path or (root / "batch_run.log")
    root_logger = logging.getLogger()

    # Mark the root logger before configuring handlers so repeated calls are no-ops.
    if hasattr(root_logger, _STDERR_HANDLER_ATTR):
        return
    setattr(root_logger, _STDERR_HANDLER_ATTR, True)

    # Promote only the existing stderr console handler (if any).
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stderr:
            handler.setLevel(logging.WARNING)

    file_handler = logging.FileHandler(str(fp), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    logger.info("─── batch run loop started, log: %s ───", fp)
