"""Batch-loop file logging setup."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_MANAGED_HANDLER_ATTR = "_vasp_sop_file_handler"
_logger = logging.getLogger(__name__)


def setup_file_logging(root: Path, *, log_path: Path | None = None) -> None:
    """Enable file logging for batch loop mode.

    - File handler at INFO → ``{root}/batch_run.log``
    - Existing stderr handler lifted to WARNING (terminal quiet)
    - Idempotent for same path; switches path on re-invocation.
    """
    fp = (log_path or (root / "batch_run.log")).resolve()
    root_logger = logging.getLogger()

    # ── Replace managed handler if path changed ──────────────────────
    existing: logging.FileHandler | None = getattr(
        root_logger, _MANAGED_HANDLER_ATTR, None
    )
    if existing is not None:
        old_path = Path(getattr(existing, "baseFilename", "")).resolve()
        if old_path == fp:
            return  # same path, no-op
        root_logger.removeHandler(existing)
        existing.close()

    # ── Stderr → WARNING (once, on first managed handler creation) ──
    if existing is None:
        for handler in root_logger.handlers:
            if (isinstance(handler, logging.StreamHandler)
                    and handler.stream is sys.stderr):
                handler.setLevel(logging.WARNING)

    # ── New file handler ─────────────────────────────────────────────
    file_handler = logging.FileHandler(str(fp), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
        )
    )
    root_logger.addHandler(file_handler)
    setattr(root_logger, _MANAGED_HANDLER_ATTR, file_handler)
    _logger.info("─── batch run loop started, log: %s ───", fp)


def teardown_file_logging() -> None:
    """Remove managed file handler (for test isolation)."""
    root_logger = logging.getLogger()
    handler: logging.FileHandler | None = getattr(
        root_logger, _MANAGED_HANDLER_ATTR, None
    )
    if handler is not None:
        root_logger.removeHandler(handler)
        handler.close()
        delattr(root_logger, _MANAGED_HANDLER_ATTR)