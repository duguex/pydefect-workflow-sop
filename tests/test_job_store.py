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

    def test_unconverged_status_valid(self, store):
        store.record("/sys/def", "unconverged", reason="nsw_exhausted")
        assert store.latest("/sys/def") == "unconverged"

    def test_reconcile_false_converged(self, store, tmp_path: Path, monkeypatch):
        from vasp_sop.core import job_store as js_mod

        d = tmp_path / "defect" / "Va_X_0"
        d.mkdir(parents=True)
        (d / "OUTCAR").write_text("no timing\n")
        store.record(str(d.resolve()), "converged")
        monkeypatch.setattr(js_mod, "calc_done_on_disk", lambda p, task_type="": False)
        stats = js_mod.reconcile_false_converged(store)
        assert stats["fixed"] == 1
        assert store.latest(str(d.resolve())) == "unconverged"

    def test_reconcile_filters_by_tree(self, store, tmp_path, monkeypatch):
        from vasp_sop.core import job_store as js_mod

        def _make(p, name):
            d = p / name; d.mkdir(parents=True)
            (d / "OUTCAR").write_text("no timing\n")
            store.record(str(d.resolve()), "converged")
            return d

        main = _make(tmp_path / "defect", "Va_Ga_0")
        dn = _make(tmp_path / "defect_new", "Va_Ga_0")
        cpd = _make(tmp_path / "cpd", "NaCl_mp-12345")
        uc = _make(tmp_path / "unitcell", "band")

        monkeypatch.setattr(js_mod, "calc_done_on_disk", lambda p, task_type="": False)
        stats = js_mod.reconcile_false_converged(store)
        assert stats["fixed"] == 3
        for d in (main, cpd, uc):
            assert store.latest(str(d.resolve())) == "unconverged"
        assert store.latest(str(dn.resolve())) == "converged"

    def test_record_if_done_converged(self, store, tmp_path: Path, monkeypatch):
        from vasp_sop.core import job_store as js_mod

        d = tmp_path / "band"
        d.mkdir()
        monkeypatch.setattr(js_mod, "calc_done_on_disk", lambda p, task_type="": True)
        assert js_mod.record_if_done(store, d) == "converged"
        assert store.latest(str(d.resolve())) == "converged"


class TestPruneMissing:
    def test_prune_removes_records_for_deleted_dirs(self, store, tmp_path):
        alive = tmp_path / "alive_calc"
        alive.mkdir()
        ghost = tmp_path / "ghost_calc"  # never created
        store.record(str(alive.resolve()), "submitted")
        store.track(str(alive.resolve()))
        store.record(str(ghost.resolve()), "submitted")
        store.track(str(ghost.resolve()))

        n_hist, n_trk = store.prune_missing()

        assert (n_hist, n_trk) == (1, 1)
        assert store.latest(str(alive.resolve())) == "submitted"
        assert store.latest(str(ghost.resolve())) is None
        assert [r["dir_path"] for r in store.tracked_dirs()] == [str(alive.resolve())]

    def test_prune_keeps_live_records(self, store, tmp_path):
        alive = tmp_path / "alive"
        alive.mkdir()
        store.record(str(alive.resolve()), "converged")
        n_hist, n_trk = store.prune_missing()
        assert (n_hist, n_trk) == (0, 0)
        assert store.latest(str(alive.resolve())) == "converged"
