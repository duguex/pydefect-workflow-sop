"""Tests for vasp_sop.core.phase_store — PhaseStore record/query lifecycle."""

from pathlib import Path
import time
import pytest


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, "PhaseStore"]:
    from vasp_sop.core.phase_store import PhaseStore
    db_path = tmp_path / "phases.db"
    return db_path, PhaseStore(db_path)


class TestPhaseStore:
    def test_record_and_latest(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        assert s.latest("GaN") == "TARGET"

    def test_history_ordering(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        time.sleep(0.01)
        s.record("GaN", "COMPETING")
        s.record("GaN", "DONE")
        history = s.history("GaN")
        assert len(history) == 3
        assert [r["phase"] for r in history] == ["TARGET", "COMPETING", "DONE"]
        assert all(r["source"] == "batch_run" for r in history)

    def test_empty_latest(self, store):
        _, s = store
        assert s.latest("nonexistent") is None

    def test_empty_history(self, store):
        _, s = store
        assert s.history("nonexistent") == []

    def test_latest_all_multiple_systems(self, store):
        _, s = store
        s.record("GaN", "DONE")
        s.record("SiC", "UC_DF")
        s.record("hBN", "DONE")
        all_phases = s.latest_all()
        assert all_phases["GaN"] == "DONE"
        assert all_phases["SiC"] == "UC_DF"
        assert all_phases["hBN"] == "DONE"

    def test_record_updates_latest(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        assert s.latest("GaN") == "TARGET"
        s.record("GaN", "DONE")
        assert s.latest("GaN") == "DONE"

    def test_custom_source(self, store):
        _, s = store
        s.record("GaN", "DONE", source="manual")
        assert s.history("GaN")[0]["source"] == "manual"

    def test_persistence(self, tmp_path):
        from vasp_sop.core.phase_store import PhaseStore
        db_path = tmp_path / "phases.db"
        s1 = PhaseStore(db_path)
        s1.record("GaN", "DONE")
        s1.close()
        s2 = PhaseStore(db_path)
        assert s2.latest("GaN") == "DONE"
        s2.close()
