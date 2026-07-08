"""Tests for vasp_sop.core.job_store — JobStore record/query lifecycle."""

from pathlib import Path
import time
import pytest


@pytest.fixture
def store(tmp_path: Path):
    from vasp_sop.core.job_store import JobStore
    return JobStore(tmp_path / "jobs.db")


class TestJobStore:
    def test_record_and_latest(self, store):
        store.record("/sys/band", "submitted")
        assert store.latest("/sys/band") == "submitted"

    def test_history_ordering(self, store):
        store.record("/sys/band", "pending")
        time.sleep(0.01)
        store.record("/sys/band", "submitted")
        store.record("/sys/band", "converged")
        history = store.history("/sys/band")
        assert len(history) == 3
        assert [r["status"] for r in history] == ["pending", "submitted", "converged"]

    def test_empty_latest(self, store):
        assert store.latest("/nonexistent") is None

    def test_empty_history(self, store):
        assert store.history("/nonexistent") == []

    def test_latest_all_multiple(self, store):
        store.record("/sysA/band", "converged")
        store.record("/sysB/band", "submitted")
        all_st = store.latest_all()
        assert all_st["/sysA/band"] == "converged"
        assert all_st["/sysB/band"] == "submitted"

    def test_record_updates_latest(self, store):
        store.record("/sys/band", "pending")
        assert store.latest("/sys/band") == "pending"
        store.record("/sys/band", "converged")
        assert store.latest("/sys/band") == "converged"

    def test_custom_source(self, store):
        store.record("/sys/band", "converged", source="init")
        assert store.history("/sys/band")[0]["source"] == "init"

    def test_persistence(self, tmp_path):
        from vasp_sop.core.job_store import JobStore
        db_path = tmp_path / "jobs.db"
        s1 = JobStore(db_path)
        s1.record("/sys/band", "converged")
        s1.close()
        s2 = JobStore(db_path)
        assert s2.latest("/sys/band") == "converged"
        s2.close()

    def test_invalid_status_raises(self, store):
        import re
        with pytest.raises(ValueError, match="Invalid status"):
            store.record("/sys/x", "invalid_status")
