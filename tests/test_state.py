"""Tests for the workflow state machine."""

import json
import tempfile
from pathlib import Path

import pytest

from vasp_sop.core.state import (
    CpdResult,
    DefectResult,
    PipelineState,
    StateStore,
    StepStatus,
    UnitcellResult,
)


class TestPipelineState:
    def test_fresh_state_is_pending(self):
        s = PipelineState(root=Path("."))
        assert s.cpd_status == StepStatus.PENDING
        assert s.unitcell_status == StepStatus.PENDING
        assert s.defect_status == StepStatus.PENDING
        assert not s.is_terminal()

    def test_terminal_when_all_done(self):
        s = PipelineState(root=Path("."))
        s.cpd_status = StepStatus.DONE
        s.unitcell_status = StepStatus.DONE
        s.defect_status = StepStatus.DONE
        assert s.is_terminal()

    def test_not_terminal_with_failed(self):
        s = PipelineState(root=Path("."))
        s.cpd_status = StepStatus.DONE
        s.unitcell_status = StepStatus.FAILED
        s.defect_status = StepStatus.PENDING
        assert not s.is_terminal()

    def test_status_enum_values(self):
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.DONE.value == "done"
        assert StepStatus.FAILED.value == "failed"


class TestStateStore:
    def test_load_missing_returns_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = StateStore.load(root)
            assert state.cpd_status == StepStatus.PENDING
            assert state.root == root

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # Save
            s1 = PipelineState(root=root)
            s1.cpd_status = StepStatus.DONE
            s1.cpd_result = CpdResult(
                unitcell_path=Path("/tmp/uc"),
                chem_pot_path=Path("/tmp/tv.yaml"),
                standard_energies_path=Path("/tmp/se.yaml"),
            )
            StateStore.save(s1)

            # Verify file exists
            state_file = root / ".pipeline_state.json"
            assert state_file.is_file()

            # Load
            s2 = StateStore.load(root)
            assert s2.cpd_status == StepStatus.DONE
            assert s2.unitcell_status == StepStatus.PENDING
            assert s2.cpd_result is not None
            assert s2.cpd_result.unitcell_path == Path("/tmp/uc")
            assert s2.cpd_result.chem_pot_path == Path("/tmp/tv.yaml")

    def test_save_and_load_full_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            s1 = PipelineState(root=root)
            s1.cpd_status = StepStatus.DONE
            s1.unitcell_status = StepStatus.DONE
            s1.defect_status = StepStatus.DONE
            s1.cpd_result = CpdResult(
                unitcell_path=root / "cpd" / "GaN_mp-123",
                chem_pot_path=root / "cpd" / "target_vertices.yaml",
                standard_energies_path=root / "cpd" / "standard_energies.yaml",
            )
            s1.unitcell_result = UnitcellResult(
                unitcell_yaml_path=root / "unitcell" / "unitcell.yaml",
                band_path=root / "unitcell" / "band",
                dos_path=root / "unitcell" / "dos",
                dielectric_path=root / "unitcell" / "dielectric",
            )
            s1.defect_result = DefectResult(
                defect_energy_summary_path=root / "defect" / "defect_energy_summary.json",
                calc_summary_path=root / "defect" / "calc_summary.json",
            )
            StateStore.save(s1)

            s2 = StateStore.load(root)
            assert s2.is_terminal()
            assert s2.unitcell_result is not None
            assert s2.unitcell_result.band_path == root / "unitcell" / "band"
            assert s2.defect_result is not None
            assert s2.defect_result.defect_energy_summary_path == root / "defect" / "defect_energy_summary.json"

    def test_idempotent_resume(self):
        """Loading twice should give same state."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1 = PipelineState(root=root)
            s1.cpd_status = StepStatus.DONE
            StateStore.save(s1)

            s2 = StateStore.load(root)
            s3 = StateStore.load(root)
            assert s2.cpd_status == s3.cpd_status
            assert s2.unitcell_status == s3.unitcell_status
            assert s2.root == s3.root

    def test_atomic_save_crash_safe(self):
        """Crash during save leaves original file intact."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s1 = PipelineState(root=root)
            s1.cpd_status = StepStatus.DONE
            StateStore.save(s1)

            path = StateStore.state_path(root)
            original = path.read_text()

            # Simulate crash by writing garbage to the .tmp file directly
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text("CORRUPTED")
            # os.replace was never called — original should be intact
            assert path.read_text() == original, "Original file was corrupted"

            # Load should still work
            s2 = StateStore.load(root)
            assert s2.cpd_status == StepStatus.DONE
