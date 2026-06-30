"""Workflow state — only StepStatus remains after #77 cleanup.

PipelineState, StateStore, and the Result dataclasses were removed
when the legacy ``pipeline.py`` was deleted.  The batch pipeline uses
filesystem + cache + submissions.db for state tracking.
"""

from __future__ import annotations

import enum


class StepStatus(enum.Enum):
    """Status of a single pipeline stage."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
