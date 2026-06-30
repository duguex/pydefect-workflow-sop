"""Tests for the workflow state — StepStatus only after #77 cleanup."""

from vasp_sop.core.state import StepStatus


class TestStepStatus:
    """StepStatus enum — the only remaining class in state.py."""

    def test_status_enum_values(self):
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.DONE.value == "done"
        assert StepStatus.FAILED.value == "failed"
