"""Tests for vasp_sop.core.system — System model and phase detection (#103)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vasp_sop.core.system import (
    COMPLETE,
    COMPETING,
    CHEM_POT_DIAGRAM,
    NO_TARGET,
    STRUCTURE_OPT,
    UNITCELL_DEFECT,
    System,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _cfg(poscar_src: str = "MP mp-830", formula: str = "GaN") -> SimpleNamespace:
    return SimpleNamespace(poscar_src=poscar_src, formula=formula)


def _make_system(tmp_path: Path, poscar_src: str = "MP mp-830") -> System:
    root = tmp_path / "GaN"
    root.mkdir()
    return System(root, _cfg(poscar_src))


class FakeJobStore:
    """In-memory JobStore stand-in for phase tests."""

    def __init__(self, states: dict[str, str] | None = None):
        self._states: dict[str, str] = dict(states or {})

    def latest(self, path: str) -> str | None:
        return self._states.get(path)

    def history(self, path: str) -> list:
        return []


def _patch_jobstore(monkeypatch, store: FakeJobStore) -> None:
    import vasp_sop.core.system as sys_mod
    monkeypatch.setattr(sys_mod, "_infer_phase_jobstore_factory", lambda: store, raising=False)


# ── Directory properties ────────────────────────────────────────────────────

class TestProperties:
    def test_name(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.name == "GaN"

    def test_cpd_dir(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.cpd_dir == tmp_path / "GaN" / "cpd"

    def test_uc_dir(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.uc_dir == tmp_path / "GaN" / "unitcell"

    def test_defect_dir(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.defect_dir == tmp_path / "GaN" / "defect"

    def test_target_dir_found(self, tmp_path: Path):
        s = _make_system(tmp_path)
        cpd = s.cpd_dir
        cpd.mkdir(parents=True)
        target = cpd / "GaN_mp-830"
        target.mkdir()
        (cpd / "GaN_mp-999").mkdir()
        assert s.target_dir == target

    def test_target_dir_none_when_no_cpd(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.target_dir is None

    def test_target_dir_none_when_no_mpid(self, tmp_path: Path):
        s = _make_system(tmp_path, poscar_src="local_file")
        cpd = s.cpd_dir
        cpd.mkdir(parents=True)
        (cpd / "GaN_mp-830").mkdir()
        assert s.target_dir is None


# ── defect_dirs ─────────────────────────────────────────────────────────────

class TestDefectDirs:
    def test_empty_when_no_dir(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.defect_dirs() == []

    def test_filters_perfect_and_defect_new(self, tmp_path: Path):
        s = _make_system(tmp_path)
        df = s.defect_dir
        df.mkdir(parents=True)
        (df / "perfect").mkdir()
        (df / "defect_new").mkdir()
        (df / "Va_Ga_0").mkdir()
        (df / "Sub_Ga_Mg_-1").mkdir()
        result = s.defect_dirs()
        names = [d.name for d in result]
        assert "perfect" not in names
        assert "defect_new" not in names
        assert "Va_Ga_0" in names
        assert "Sub_Ga_Mg_-1" in names

    def test_excludes_dirs_without_underscore(self, tmp_path: Path):
        s = _make_system(tmp_path)
        df = s.defect_dir
        df.mkdir(parents=True)
        (df / "junk").mkdir()
        (df / "Va_Ga_0").mkdir()
        names = [d.name for d in s.defect_dirs()]
        assert "junk" not in names
        assert "Va_Ga_0" in names

    def test_sorted(self, tmp_path: Path):
        s = _make_system(tmp_path)
        df = s.defect_dir
        df.mkdir(parents=True)
        (df / "Va_N_0").mkdir()
        (df / "Va_Ga_0").mkdir()
        names = [d.name for d in s.defect_dirs()]
        assert names == sorted(names)


# ── state.json ──────────────────────────────────────────────────────────────

class TestStateJson:
    def test_save_and_read_phase(self, tmp_path: Path):
        s = _make_system(tmp_path)
        s.save_phase(COMPLETE)
        state_file = s.root / "state.json"
        assert state_file.is_file()
        data = json.loads(state_file.read_text())
        assert data["phase"] == COMPLETE

    def test_phase_reads_state_json_first(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        s.save_phase(COMPETING)
        # Even without any filesystem structure, state.json wins.
        assert s.phase() == COMPETING

    def test_save_phase_preserves_other_keys(self, tmp_path: Path):
        s = _make_system(tmp_path)
        state_file = s.root / "state.json"
        state_file.write_text(json.dumps({"extra": 42}))
        s.save_phase(STRUCTURE_OPT)
        data = json.loads(state_file.read_text())
        assert data["phase"] == STRUCTURE_OPT
        assert data["extra"] == 42

    def test_phase_falls_back_when_no_state_json(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        # No state.json, no cpd dir → NO_TARGET
        assert s.phase() == NO_TARGET

    def test_phase_ignores_corrupt_state_json(self, tmp_path: Path):
        s = _make_system(tmp_path)
        (s.root / "state.json").write_text("not json {{{")
        # Falls back to filesystem inference → NO_TARGET (no cpd dir)
        assert s.phase() == NO_TARGET


# ── Phase inference ─────────────────────────────────────────────────────────

class TestPhaseInference:
    """Test _infer_phase via the public phase() method (no state.json)."""

    def _patch(self, monkeypatch, job_states: dict[str, str] | None = None, **io_overrides):
        """Patch JobStore and vasp.io helpers used by _infer_phase."""
        import vasp_sop.core.job_store as js_mod
        import vasp_sop.core.jobs as jobs_mod
        import vasp_sop.vasp.io as io_mod

        fake = FakeJobStore(job_states or {})
        monkeypatch.setattr(js_mod, "JobStore", lambda: fake, raising=False)
        monkeypatch.setattr(jobs_mod, "crisp_terminal_status", lambda d: None, raising=False)
        monkeypatch.setattr(io_mod, "check_converged", lambda d: False, raising=False)
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False, raising=False)
        for attr, val in io_overrides.items():
            monkeypatch.setattr(io_mod, attr, val, raising=False)

    def test_no_target_when_no_mpid(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "GaN"
        root.mkdir()
        s = System(root, _cfg(poscar_src="local"))
        assert s.phase() == NO_TARGET

    def test_structure_opt_when_target_not_converged(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)
        # JobStore returns None (not converged)
        self._patch(monkeypatch, {})
        assert s.phase() == STRUCTURE_OPT

    def test_competing_when_competing_dirs_exist(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)
        # A competing phase dir with inputs but not converged
        comp = s.cpd_dir / "Ga_mp-100"
        comp.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (comp / f).write_text("x")

        td_str = str(td.resolve())
        self._patch(monkeypatch, {td_str: "converged"}, input_ready=lambda d: True)
        assert s.phase() == COMPETING

    def test_chem_pot_diagram_when_all_converged(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)

        td_str = str(td.resolve())
        self._patch(monkeypatch, {td_str: "converged"})
        assert s.phase() == CHEM_POT_DIAGRAM

    def test_unitcell_defect_when_target_vertices_exists_but_no_uc_inputs(
        self, tmp_path: Path, monkeypatch
    ):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)
        (s.cpd_dir / "target_vertices.yaml").write_text("target: {}")
        # No unitcell INCAR files → UNITCELL_DEFECT
        self._patch(monkeypatch, {})
        assert s.phase() == UNITCELL_DEFECT

    def test_complete_when_all_artifacts_present(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)

        # CPD artifacts
        (s.cpd_dir / "target_vertices.yaml").write_text("target: {}")
        (s.cpd_dir / "composition_energies.yaml").write_text("{}")
        (s.cpd_dir / "standard_energies.yaml").write_text("{}")
        (s.cpd_dir / "chem_pot_diag.json").write_text("{}")

        # UC artifacts
        uc = s.uc_dir
        for task in ("band", "dos", "dielectric"):
            (uc / task).mkdir(parents=True)
            (uc / task / "INCAR").write_text("x")
        (uc / "unitcell.yaml").write_text("{}")

        # Defect artifacts
        df = s.defect_dir
        df.mkdir(parents=True)
        defect = df / "Va_Ga_0"
        defect.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "OUTCAR"):
            (defect / f).write_text("x")
        (defect / "calc_results.json").write_text("{}")
        (defect / "correction.json").write_text("{}")
        (defect / "defect_structure_info.json").write_text("{}")

        perfect = df / "perfect"
        perfect.mkdir()
        (perfect / "perfect_band_edge_state.json").write_text("{}")

        self._patch(monkeypatch, {}, input_ready=lambda d: True)
        assert s.phase() == COMPLETE

    def test_unitcell_defect_when_defect_missing_correction(self, tmp_path: Path, monkeypatch):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)

        (s.cpd_dir / "target_vertices.yaml").write_text("target: {}")
        (s.cpd_dir / "composition_energies.yaml").write_text("{}")
        (s.cpd_dir / "standard_energies.yaml").write_text("{}")
        (s.cpd_dir / "chem_pot_diag.json").write_text("{}")

        uc = s.uc_dir
        for task in ("band", "dos", "dielectric"):
            (uc / task).mkdir(parents=True)
            (uc / task / "INCAR").write_text("x")
        (uc / "unitcell.yaml").write_text("{}")

        df = s.defect_dir
        df.mkdir(parents=True)
        defect = df / "Va_Ga_0"
        defect.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "OUTCAR"):
            (defect / f).write_text("x")
        (defect / "calc_results.json").write_text("{}")
        # correction.json missing → UNITCELL_DEFECT

        perfect = df / "perfect"
        perfect.mkdir()
        (perfect / "perfect_band_edge_state.json").write_text("{}")

        self._patch(monkeypatch, {}, input_ready=lambda d: True)
        assert s.phase() == UNITCELL_DEFECT
