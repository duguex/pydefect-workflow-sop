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

class TestPhaseIsDiskDerived:
    """ADR 0011: phase comes from the filesystem; state.json is ignored."""

    def test_phase_ignores_state_json_marker(self, tmp_path: Path):
        s = _make_system(tmp_path)
        # A stale marker (legacy ADR 0001 era) must not override disk truth
        (s.root / "state.json").write_text(json.dumps({"phase": COMPLETE}))
        assert s.phase() == NO_TARGET

    def test_phase_ignores_corrupt_state_json(self, tmp_path: Path):
        s = _make_system(tmp_path)
        (s.root / "state.json").write_text("not json {{{")
        assert s.phase() == NO_TARGET

    def test_phase_without_state_json(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.phase() == NO_TARGET

    def test_derive_phase_matches_phase(self, tmp_path: Path):
        s = _make_system(tmp_path)
        assert s.phase() == s.derive_phase()


# ── Phase inference ─────────────────────────────────────────────────────────

class TestPhaseInference:
    """Test _infer_phase via the public phase() method (no state.json)."""

    def _patch(self, monkeypatch, job_states: dict[str, str] | None = None,
               *, verdict_converged: bool | None = None, **io_overrides):
        """Patch JobStore and vasp.io helpers used by _infer_phase.

        *verdict_converged* overrides the mocked convergence verdict
        (default False — the gate's checks then always block).
        """
        import vasp_sop.core.job_store as js_mod
        import vasp_sop.core.jobs as jobs_mod
        import vasp_sop.vasp.io as io_mod
        import vasp_sop.vasp.convergence as conv_mod
        from vasp_sop.vasp.convergence import ConvergenceVerdict

        fake = FakeJobStore(job_states or {})
        monkeypatch.setattr(js_mod, "JobStore", lambda: fake, raising=False)
        monkeypatch.setattr(jobs_mod, "crisp_terminal_status", lambda d: None, raising=False)
        monkeypatch.setattr(
            conv_mod, "convergence_verdict",
            lambda d, task_type="": ConvergenceVerdict(
                verdict_converged if verdict_converged is not None else False,
                "mock",
            ),
            raising=False,
        )
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
        (s.cpd_dir / "standard_energies.yaml").write_text("GaN: {}")
        # No unitcell INCAR files → UNITCELL_DEFECT
        self._patch(monkeypatch, {})
        assert s.phase() == UNITCELL_DEFECT

    @staticmethod
    def _build_complete_system(tmp_path: Path) -> tuple[System, Path]:
        """Scaffold a system where every engaged calculation is converged.

        Returns ``(system, defect_root)`` — the only missing piece is the
        phase gate itself.
        """
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
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (defect / f).write_text("x")
        (defect / "calc_results.json").write_text("{}")
        (defect / "correction.json").write_text("{}")
        (defect / "defect_structure_info.json").write_text("{}")

        perfect = df / "perfect"
        perfect.mkdir()
        (perfect / "perfect_band_edge_state.json").write_text("{}")

        # Every calculation on disk must pass the convergence verdict
        # (ADR 0004) — write verdict-converged OUTCARs everywhere.
        def _converged(d: Path) -> None:
            (d / "OUTCAR").write_text(
                " General timing and accounting\n"
                " TOTAL-FORCE (eV/Angst)\n ---\n"
                " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
            )

        _converged(td)
        for task in ("band", "dos", "dielectric"):
            _converged(uc / task)
        _converged(defect)
        _converged(perfect)
        (df / "defect_energy_summary.json").write_text("{}")
        return s, df

    def test_complete_when_all_artifacts_present(self, tmp_path: Path, monkeypatch):
        s, _ = self._build_complete_system(tmp_path)
        self._patch(monkeypatch, {}, input_ready=lambda d: True,
                    verdict_converged=True)
        assert s.phase() == COMPLETE

    def test_complete_ignores_antisite_defect_dirs(self, tmp_path: Path, monkeypatch):
        """ADR 0013: anion-cation antisites are excluded from the defect set
        and must not block the COMPLETE phase gate — even when they carry
        full inputs and never ran (no OUTCAR / no post-processing)."""
        s, df = self._build_complete_system(tmp_path)
        for name in ("Al_O1_0", "O_Al1_0"):
            antisite = df / name
            antisite.mkdir()
            for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
                (antisite / f).write_text("x")
        # No OUTCAR, calc_results.json, correction.json, or
        # defect_structure_info.json in either antisite dir.
        self._patch(monkeypatch, {}, input_ready=lambda d: True,
                    verdict_converged=True)
        assert s.phase() == COMPLETE

    def test_unitcell_defect_when_antisite_is_invalid(self, tmp_path: Path, monkeypatch):
        """An antisite dir that somehow converged still counts as excluded:
        a real valid defect missing its correction keeps blocking."""
        s, df = self._build_complete_system(tmp_path)
        antisite = df / "Al_O1_0"
        antisite.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "OUTCAR"):
            (antisite / f).write_text("x")
        (antisite / "calc_results.json").write_text("{}")
        (antisite / "correction.json").write_text("{}")
        (antisite / "defect_structure_info.json").write_text("{}")
        # A second, valid defect dir with no correction.json → still blocks.
        (df / "Va_Ga_1").mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS", "OUTCAR"):
            (df / "Va_Ga_1" / f).write_text("x")
        (df / "Va_Ga_1" / "calc_results.json").write_text("{}")

        self._patch(monkeypatch, {}, input_ready=lambda d: True,
                    verdict_converged=True)
        assert s.phase() == UNITCELL_DEFECT

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


class TestChemicalEnvironmentScope:
    """Scope=chemical-environment: COMPLETE is reached at CPD completion
    (ADR 0005) — no unit-cell or defect legs."""

    def _patch(self, monkeypatch, job_states: dict[str, str] | None = None,
               *, verdict_converged: bool | None = None, **io_overrides):
        """Shared patch helper (same as TestPhaseInference._patch)."""
        import vasp_sop.core.job_store as js_mod
        import vasp_sop.core.jobs as jobs_mod
        import vasp_sop.vasp.io as io_mod
        import vasp_sop.vasp.convergence as conv_mod
        from vasp_sop.vasp.convergence import ConvergenceVerdict

        fake = FakeJobStore(job_states or {})
        monkeypatch.setattr(js_mod, "JobStore", lambda: fake, raising=False)
        monkeypatch.setattr(jobs_mod, "crisp_terminal_status", lambda d: None, raising=False)
        monkeypatch.setattr(
            conv_mod, "convergence_verdict",
            lambda d, task_type="": ConvergenceVerdict(
                verdict_converged if verdict_converged is not None else False,
                "mock",
            ),
            raising=False,
        )
        monkeypatch.setattr(io_mod, "input_ready", lambda d: False, raising=False)
        for attr, val in io_overrides.items():
            monkeypatch.setattr(io_mod, attr, val, raising=False)

    def _ce_system(self, tmp_path: Path, *, with_artifacts: bool = True,
                   scope: str = "chemical-environment"):
        from types import SimpleNamespace
        s = _make_system(tmp_path)
        s.config = SimpleNamespace(poscar_src="MP mp-830", formula="GaN",
                                   scope=scope)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)
        (s.cpd_dir / "target_vertices.yaml").write_text("tv: {}")
        (s.cpd_dir / "standard_energies.yaml").write_text("se: {}")
        if with_artifacts:
            (s.cpd_dir / "composition_energies.yaml").write_text("ce: {}")
            (s.cpd_dir / "chem_pot_diag.json").write_text("{}")
        return s

    def test_complete_at_cpd_done(self, tmp_path, monkeypatch):
        s = self._ce_system(tmp_path)
        self._patch(monkeypatch, {}, verdict_converged=True)
        assert s.phase() == COMPLETE

    def test_missing_artifacts_keeps_cpd(self, tmp_path, monkeypatch):
        s = self._ce_system(tmp_path, with_artifacts=False)
        self._patch(monkeypatch, {}, verdict_converged=True)
        assert s.phase() == CHEM_POT_DIAGRAM

    def test_unconverged_phase_keeps_cpd(self, tmp_path, monkeypatch):
        s = self._ce_system(tmp_path)
        comp = s.cpd_dir / "Ga_mp-142"
        comp.mkdir()
        # verdict mock defaults to False -> competing phase not converged
        self._patch(monkeypatch, {})
        assert s.phase() == CHEM_POT_DIAGRAM

    def test_defects_scope_unaffected(self, tmp_path, monkeypatch):
        """Default scope still requires the full defect workflow."""
        s = self._ce_system(tmp_path, scope="defects")  # no UC/defect dirs
        self._patch(monkeypatch, {}, verdict_converged=True)
        assert s.phase() == UNITCELL_DEFECT


# ── Stale JobStore 'converged' (ADR 0016 parity for cpd) ────────────────────

class TestCompetingStaleConverged:
    """A JobStore ``converged`` record whose disk verdict is unconverged
    must resubmit — the record alone must not gate the phase (SrGa4O7:Fe
    deadlock, issue #121)."""

    def _make(self, tmp_path: Path):
        s = _make_system(tmp_path)
        td = s.cpd_dir / "GaN_mp-830"
        td.mkdir(parents=True)
        comp = s.cpd_dir / "Ga_mp-100"
        comp.mkdir()
        for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"):
            (comp / f).write_text("x")
        return s, comp

    def _patch_verdict(self, monkeypatch, converged: bool, reason: str):
        import vasp_sop.vasp.convergence as conv_mod
        import vasp_sop.vasp.io as io_mod
        from vasp_sop.vasp.convergence import ConvergenceVerdict

        monkeypatch.setattr(
            conv_mod, "convergence_verdict",
            lambda d, task_type="": ConvergenceVerdict(converged, reason),
            raising=False,
        )
        monkeypatch.setattr(io_mod, "input_ready", lambda d: True, raising=False)

    def test_stale_converged_resubmits(self, tmp_path, monkeypatch):
        s, comp = self._make(tmp_path)
        store = FakeJobStore({str(comp.resolve()): "converged"})
        self._patch_verdict(monkeypatch, converged=False, reason="missing_outcar")
        assert s.competing_dirs(store) == [comp]

    def test_disk_converged_never_resubmits(self, tmp_path, monkeypatch):
        s, comp = self._make(tmp_path)
        store = FakeJobStore({str(comp.resolve()): "converged"})
        self._patch_verdict(monkeypatch, converged=True, reason="")
        assert s.competing_dirs(store) == []

    def test_disk_converged_ignores_stale_failed_marker(self, tmp_path, monkeypatch):
        s, comp = self._make(tmp_path)
        # Genuinely stale: the marker predates the (successful) output.
        (comp / ".failed").write_text("CRISP_FAILED\nEXIT_CODE: 1\n")
        (comp / "OUTCAR").write_text(
            " General timing and accounting\n"
            " TOTAL-FORCE (eV/Angst)\n ---\n"
            " 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000\n"
        )
        import os
        base = os.stat(comp / ".failed").st_mtime_ns
        os.utime(comp / ".failed", ns=(base, base))
        os.utime(comp / "OUTCAR", ns=(base + 1_000_000, base + 1_000_000))
        store = FakeJobStore({str(comp.resolve()): "failed"})
        self._patch_verdict(monkeypatch, converged=True, reason="")

        assert s.competing_dirs(store) == []
        assert s.competing_blockers(store) == []

    def test_stale_converged_is_blocker(self, tmp_path, monkeypatch):
        s, comp = self._make(tmp_path)
        store = FakeJobStore({str(comp.resolve()): "converged"})
        self._patch_verdict(monkeypatch, converged=False, reason="missing_outcar")
        assert s.competing_blockers(store) == [comp]

    def test_disk_converged_not_blocker(self, tmp_path, monkeypatch):
        s, comp = self._make(tmp_path)
        store = FakeJobStore({str(comp.resolve()): "converged"})
        self._patch_verdict(monkeypatch, converged=True, reason="")
        assert s.competing_blockers(store) == []
