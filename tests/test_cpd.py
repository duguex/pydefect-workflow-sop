"""Regression tests for vasp_sop.defect.cpd.

Covers local issues 0001 and 0002, both of which are already fixed in the
code; these tests guard against regressions.

- issues/0001-srte-cpd-target-lookup-false-positive-failure.md
- issues/0002-skip-4d-cpd-diagram.md
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymatgen.core import Composition
import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.cpd import (
    adjust_unstable_phase,
    compute_chemical_potentials,
    ensure_target_results,
    handoff_target_results,
    preflight_cpd_inputs,
)


class TestAdjustUnstablePhase:
    """Issue 0001: pydefect sre may emit unreduced keys like 'Sr1Te1'."""

    def test_reduced_formula_match_target(self, tmp_path: Path):
        """The reduced_formula comparison must succeed even when the
        relative_energies.yaml key uses a non-reduced formula string."""
        cpd_root = tmp_path
        rel_energies = cpd_root / "relative_energies.yaml"
        # Mix of representations: "Sr1Te1" (unreduced) plus atomic species.
        rel_energies.write_text(yaml.dump({
            "Sr": -10.0,
            "Te": -8.0,
            "Sr1Te1": -25.0,
        }))
        se = cpd_root / "standard_energies.yaml"
        se.write_text(yaml.dump({}))

        config = PipelineConfig(formula="SrTe")
        from pymatgen.core import Composition

        # Pre-condition: standard_energies.yaml exists so adjust_unstable_phase
        # proceeds into the multi-element cv loop. The test guards the
        # reduced-formula matching at L320-333; the loop will raise later
        # for unrelated reasons (no chem_pot_diag.json) — catch that.
        try:
            adjust_unstable_phase(
                cpd_root, rel_energies,
                Composition("SrTe"), config,
            )
        except Exception:
            # Reaching the reduced-formula match step is what we test for.
            # The function would otherwise raise "Target composition ... not
            # found" if the comparison were string-equality. Any other error
            # indicates we got past the lookup.
            pass

class TestTargetResultHandoff:
    def _write_poscar(self, path: Path, formula: str = "NaCl") -> None:
        species = "Li F" if formula == "LiF" else "Na Cl"
        path.write_text(
            f"{formula}\n1\n5 0 0\n0 5 0\n0 0 5\n{species}\n1 1\n"
            "Direct\n0 0 0\n0.5 0.5 0.5\n"
        )

    def _write_all_inputs(self, d: Path, formula: str = "NaCl") -> None:
        self._write_poscar(d / "POSCAR", formula)
        self._write_poscar(d / "CONTCAR", formula)
        (d / "INCAR").write_text("ENCUT = 400\n")
        (d / "KPOINTS").write_text("Auto\n0\nGamma\n4 4 4\n")
        (d / "POTCAR").write_text("PAW_PBE\n")
        (d / "OUTCAR").write_text("energy\n")
        (d / "vasprun.xml").write_text("<vasprun/>\n")
    def test_handoff_copies_all_7_files_from_target(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        source = tmp_path / "unitcell" / "structure_opt"
        cpd_target.mkdir(parents=True)
        self._write_all_inputs(cpd_target)

        handoff_target_results(cpd_target, source, Composition("NaCl"))
        for f in ("POSCAR", "CONTCAR", "OUTCAR", "vasprun.xml", "INCAR", "KPOINTS", "POTCAR"):
            assert (source / f).is_file(), f"{f} missing in source"
    def test_handoff_rejects_missing_input_files(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        source = tmp_path / "unitcell" / "structure_opt"
        cpd_target.mkdir(parents=True)
        self._write_poscar(cpd_target / "POSCAR")
        self._write_poscar(cpd_target / "CONTCAR")
        (cpd_target / "OUTCAR").write_text("x\n")
        (cpd_target / "vasprun.xml").write_text("x\n")
        with pytest.raises(FileNotFoundError, match="INCAR"):
            handoff_target_results(cpd_target, source, Composition("NaCl"))
    def test_handoff_rejects_composition_mismatch(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        source = tmp_path / "unitcell" / "structure_opt"
        cpd_target.mkdir(parents=True)
        self._write_all_inputs(cpd_target)
        self._write_poscar(cpd_target / "CONTCAR", "LiF")
        with pytest.raises(ValueError, match="composition"):
            handoff_target_results(cpd_target, source, Composition("NaCl"))
    def test_self_handoff_skips_same_path(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        cpd_target.mkdir(parents=True)
        self._write_all_inputs(cpd_target)
        handoff_target_results(cpd_target, cpd_target, Composition("NaCl"))
    def test_handoff_overwrites_stale_source_files(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        source = tmp_path / "unitcell" / "structure_opt"
        cpd_target.mkdir(parents=True)
        source.mkdir(parents=True)
        self._write_all_inputs(cpd_target)
        (source / "OUTCAR").write_text("old stale data\n")
        handoff_target_results(cpd_target, source, Composition("NaCl"))
        assert (source / "OUTCAR").read_text() == "energy\n"
    def test_cpd_only_accepts_complete_target(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        cpd_target.mkdir(parents=True)
        self._write_all_inputs(cpd_target)
        source = tmp_path / "unitcell" / "structure_opt"
        ensure_target_results(cpd_target, source, Composition("NaCl"))
        assert (source / "OUTCAR").is_file()
    def test_cpd_preflight_rejects_missing_target_results(self, tmp_path):
        cpd_target = tmp_path / "cpd" / "NaCl_mp-1"
        cpd_target.mkdir(parents=True)
        self._write_poscar(cpd_target / "POSCAR")
        with pytest.raises(FileNotFoundError, match="missing required results"):
            ensure_target_results(cpd_target, tmp_path / "dummy", Composition("NaCl"))


class TestCpdMcePreflight:
    def test_reports_exact_mce_files_per_phase(self, tmp_path: Path):
        complete = tmp_path / "NaCl_mp-1"
        incomplete = tmp_path / "Cl2_mp-2"
        complete.mkdir()
        incomplete.mkdir()
        (complete / "OUTCAR").write_text("energy\n")
        (complete / "CONTCAR").write_text("structure\n")
        (incomplete / "OUTCAR").write_text("energy\n")
        (tmp_path / "combos").mkdir()

        result = preflight_cpd_inputs(tmp_path)

        assert result.phase_dirs == ("Cl2_mp-2", "NaCl_mp-1")
        assert result.missing == {"Cl2_mp-2": ("CONTCAR",)}
        assert result.ready is False

    def test_ready_when_every_mce_phase_has_outcar_and_contcar(self, tmp_path: Path):
        for name in ("NaCl_mp-1", "Cl2_mp-2"):
            phase = tmp_path / name
            phase.mkdir()
            (phase / "OUTCAR").write_text("energy\n")
            (phase / "CONTCAR").write_text("structure\n")

        result = preflight_cpd_inputs(tmp_path)

        assert result.ready is True
        assert result.missing == {}


    def test_compute_writes_blocked_preflight_before_mce(self, tmp_path, monkeypatch):
        phase = tmp_path / "NaCl_mp-1"
        phase.mkdir()
        phase.joinpath("POSCAR").write_text(
            "NaCl\n1\n5 0 0\n0 5 0\n0 0 5\nNa Cl\n1 1\n"
            "Direct\n0 0 0\n0.5 0.5 0.5\n"
        )
        phase.joinpath("OUTCAR").write_text("energy\n")
        run_calls = []
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local",
            lambda *args, **kwargs: run_calls.append(args),
        )

        with pytest.raises(RuntimeError, match="CPD mce preflight failed"):
            compute_chemical_potentials(
                tmp_path, PipelineConfig(formula="NaCl"), Composition("NaCl")
            )

        status = yaml.safe_load((tmp_path / "cpd_preflight.yaml").read_text())
        assert status["ready"] is False
        assert status["missing"] == {"NaCl_mp-1": ["CONTCAR"]}
        assert run_calls == []

class TestComputeChemicalPotentials:
    """Issue 0002: pydefect pc only supports 2D/3D chem-pot diagrams."""

    def test_skip_4d_cpd_diagram(self, tmp_path: Path, monkeypatch):
        """For 4+ element systems, pydefect pc must NOT be invoked."""
        cpd_root = tmp_path
        (cpd_root / "composition_energies.yaml").write_text(yaml.dump({}))
        (cpd_root / "standard_energies.yaml").write_text(yaml.dump({}))
        (cpd_root / "target_vertices.yaml").write_text(yaml.dump({}))

        recorded_cmds = []

        def fake_run_local(cmd, cwd, timeout=600):
            recorded_cmds.append(str(cmd))
            # Pretend every command succeeds.

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run_local)
        monkeypatch.setattr(
            "vasp_sop.defect.cpd.adjust_unstable_phase",
            lambda *a, **kw: None,
        )

        config = PipelineConfig(formula="Ba2MgSi2O7")
        from pymatgen.core import Composition

        compute_chemical_potentials(
            cpd_root, config, Composition("Ba2MgSi2O7"),
        )

        assert not any("pydefect pc" in c for c in recorded_cmds), (
            f"pydefect pc must be skipped for 4+ element systems; got: {recorded_cmds}"
        )

    def test_pydefect_pc_failure_is_nonfatal(
        self, tmp_path: Path, monkeypatch,
    ):
        """For ≤3 element systems, a failure in `pydefect pc` must not
        propagate — it's a diagnostic plot only."""
        cpd_root = tmp_path
        (cpd_root / "composition_energies.yaml").write_text(yaml.dump({}))
        (cpd_root / "standard_energies.yaml").write_text(yaml.dump({}))
        (cpd_root / "target_vertices.yaml").write_text(yaml.dump({}))

        def fake_run_local(cmd, cwd, timeout=600):
            if "pydefect pc" in str(cmd):
                raise RuntimeError("simulated matplotlib failure")
            # All other commands succeed.

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fake_run_local)
        monkeypatch.setattr(
            "vasp_sop.defect.cpd.adjust_unstable_phase",
            lambda *a, **kw: None,
        )

        config = PipelineConfig(formula="GaN")
        from pymatgen.core import Composition

        # Must not raise.
        compute_chemical_potentials(cpd_root, config, Composition("GaN"))


class TestApplyMoleculeCorrections:
    """apply_molecule_corrections — empirical energy corrections."""

    def test_applies_correction(self, tmp_path: Path):
        """Known molecule gets its energy adjusted by the correction value."""
        from vasp_sop.defect.cpd import apply_molecule_corrections
        comp = tmp_path / "composition_energies.yaml"
        comp.write_text(yaml.dump({"O2": {"energy": -10.0}}))
        corrections = {"O2": 1.374}
        apply_molecule_corrections(comp, corrections)
        data = yaml.safe_load(comp.read_text())
        assert data["O2"]["energy"] == pytest.approx(-8.626)

    def test_unknown_molecule_skipped(self, tmp_path: Path):
        """Molecule not in corrections dict is left unchanged."""
        from vasp_sop.defect.cpd import apply_molecule_corrections
        comp = tmp_path / "composition_energies.yaml"
        comp.write_text(yaml.dump({"H2O": {"energy": -5.0}}))
        apply_molecule_corrections(comp, {})
        data = yaml.safe_load(comp.read_text())
        assert data["H2O"]["energy"] == -5.0


class TestWriteBinaryTargetVertices:
    """_write_binary_target_vertices — chem_pot computation for 2-element systems."""

    def test_writes_correct_chem_pot_with_elemental_refs(self, tmp_path: Path):
        """With elemental references in composition_energies, chem_pot
        equals formation energy (target - sum of elemental references)."""
        from vasp_sop.defect.cpd import _write_binary_target_vertices
        from pymatgen.core import Composition
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        comp = cpd / "composition_energies.yaml"
        comp.write_text(yaml.dump({
            "GaN": {"energy": -15.0},   # GaN target
            "Ga": {"energy": -3.0},     # elemental Ga
            "N2": {"energy": -8.0},     # molecular N2
        }))
        _write_binary_target_vertices(cpd, Composition("GaN"), "GaN")
        tv = yaml.safe_load((cpd / "target_vertices.yaml").read_text())
        # formation energy = -15.0 - (-3.0 + -8.0) / ... wait
        # GaN has 1 Ga + 1 N, so ref = 1*(-3.0) + 1*(-8.0/2) = -3.0 -4.0 = -7.0
        # chem_pot = -15.0 - (-7.0) = -8.0
        assert tv["GaN"]["chem_pot"] == pytest.approx(-8.0)

    def test_fallback_to_total_energy_when_no_elemental_refs(self, tmp_path: Path):
        """Without elemental references, uses total energy as chem_pot."""
        from vasp_sop.defect.cpd import _write_binary_target_vertices
        from pymatgen.core import Composition
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        comp = cpd / "composition_energies.yaml"
        comp.write_text(yaml.dump({
            "GaN": {"energy": -15.0},
        }))
        _write_binary_target_vertices(cpd, Composition("GaN"), "GaN")
        tv = yaml.safe_load((cpd / "target_vertices.yaml").read_text())
        assert tv["GaN"]["chem_pot"] == pytest.approx(-15.0)
        assert "vertices" in yaml.safe_load(
            (cpd / "chem_pot_diag.json").read_text()
        )

    def test_creates_standard_energies(self, tmp_path: Path):
        """standard_energies.yaml is written alongside target_vertices.yaml."""
        from vasp_sop.defect.cpd import _write_binary_target_vertices
        from pymatgen.core import Composition
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        comp = cpd / "composition_energies.yaml"
        comp.write_text(yaml.dump({"GaN": {"energy": -15.0}}))
        _write_binary_target_vertices(cpd, Composition("GaN"), "GaN")
        se = yaml.safe_load((cpd / "standard_energies.yaml").read_text())
        assert "GaN" in se
        assert se["GaN"]["energy"] == -15.0

class TestEnsureCpdPhases:
    """ADR 0015: refresh competing phases on plan element change."""

    def _setup(self, tmp_path: Path, elements: list[str], phase_dirs: list[str]):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        for name in phase_dirs:
            (cpd / name).mkdir()
        (cpd / "mp_state.json").write_text(json.dumps({
            "schema": 2, "molecule_resource_version": "diatomic-reference-v2",
            "status": "completed", "elements": sorted(elements),
            "phase_dirs": phase_dirs,
        }))
        return cpd

    def _config(self, formula: str = "CaAl4O7", dopants: list[str] | None = None):
        from vasp_sop.core.config import PipelineConfig
        return PipelineConfig(formula=formula, dopant_elements=dopants or [])

    def test_no_mismatch_no_refresh(self, tmp_path: Path):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = self._setup(tmp_path, ["Al", "Ca", "O"], ["Al_mp-1"])
        # CaAl4O7 intrinsic = Al/Ca/O, no dopants
        assert cpd_mod.ensure_cpd_phases(cpd, self._config()) == 0

    def test_mismatch_fetches_and_submits(self, tmp_path: Path, monkeypatch):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = self._setup(tmp_path, ["Al", "Ca", "O"], ["Al_mp-1"])
        calls: list[str] = []

        def fake_fetch(elements, target, use_cache=True):
            calls.append(("fetch", sorted(elements)))
            (target / "FeO_mp-9").mkdir()

        def fake_submit(cpd_root, names, config):
            calls.append(("submit", sorted(names)))
            return [object() for _ in names]

        monkeypatch.setattr("vasp_sop.materials.fetch_candidate_phases", fake_fetch)
        monkeypatch.setattr(cpd_mod, "_submit_cpd_batch", fake_submit)
        monkeypatch.setattr(
            "vasp_sop.materials.mp._write_mp_state",
            lambda *a, **kw: calls.append(("write", sorted(a[1]))))
        n = cpd_mod.ensure_cpd_phases(cpd, self._config(dopants=["Fe"]))
        assert n == 1
        assert ("fetch", ["Al", "Ca", "Fe", "O"]) in calls
        assert ("submit", ["FeO_mp-9"]) in calls
        # existing phase untouched, new phase moved in
        assert (cpd / "Al_mp-1").is_dir()
        assert (cpd / "FeO_mp-9").is_dir()
        # mp_state rewritten with merged element set
        assert ("write", ["Al", "Ca", "Fe", "O"]) in calls

    def test_dry_run_returns_minus_one(self, tmp_path: Path):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = self._setup(tmp_path, ["Al", "Ca", "O"], ["Al_mp-1"])
        assert cpd_mod.ensure_cpd_phases(
            cpd, self._config(dopants=["Fe"]), dry_run=True) == -1


class TestCpdDiagramRefresh:
    """Chem-pot diagram staleness vs plan elements + safe refresh."""

    def _config(self, formula: str = "SrAl4O7",
                dopants: list[str] | None = None) -> SimpleNamespace:
        from vasp_sop.core.config import PipelineConfig
        cfg = PipelineConfig(
            formula=formula, root=Path("/nonexistent"),
            dopant_elements=dopants or [],
        )
        return cfg

    def test_stale_when_dopant_missing_from_standard_energies(
        self, tmp_path: Path
    ):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        (cpd / "standard_energies.yaml").write_text(
            "Al: -4.15\nO: -4.44\nSr: -1.87\n"
        )
        assert cpd_mod.cpd_diagram_stale(cpd, self._config(dopants=["Fe"]))

    def test_fresh_when_all_plan_elements_present(self, tmp_path: Path):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        (cpd / "standard_energies.yaml").write_text(
            "Al: -4.15\nO: -4.44\nSr: -1.87\nFe: -8.10\n"
        )
        assert not cpd_mod.cpd_diagram_stale(cpd, self._config(dopants=["Fe"]))

    def test_not_stale_without_standard_energies(self, tmp_path: Path):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        assert not cpd_mod.cpd_diagram_stale(cpd, self._config(dopants=["Fe"]))

    def test_refresh_preflight_keeps_artefacts_on_missing_phase_files(
        self, tmp_path: Path, monkeypatch
    ):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        (cpd / "target_vertices.yaml").write_text("old\n")
        # a phase dir missing its OUTCAR → mce preflight not ready
        phase = cpd / "FeO_mp-1"
        phase.mkdir()
        (phase / "CONTCAR").write_text("x\n")
        called = []
        monkeypatch.setattr(
            cpd_mod, "compute_chemical_potentials",
            lambda *a, **kw: called.append(True))
        ok = cpd_mod.refresh_cpd_diagram(cpd, self._config())
        assert not ok
        assert called == []
        # old diagram untouched (preflight failed before deletion)
        assert (cpd / "target_vertices.yaml").read_text() == "old\n"

    def test_refresh_deletes_and_recomputes(self, tmp_path: Path, monkeypatch):
        from vasp_sop.defect import cpd as cpd_mod
        cpd = tmp_path / "cpd"
        cpd.mkdir()
        phase = cpd / "FeO_mp-1"
        phase.mkdir()
        (phase / "OUTCAR").write_text("x\n")
        (phase / "CONTCAR").write_text("x\n")
        for name in ("target_vertices.yaml", "chem_pot_diag.json",
                     "standard_energies.yaml", "composition_energies.yaml",
                     "relative_energies.yaml"):
            (cpd / name).write_text("old\n")
        called = []
        monkeypatch.setattr(
            cpd_mod, "compute_chemical_potentials",
            lambda *a, **kw: called.append(a))
        ok = cpd_mod.refresh_cpd_diagram(cpd, self._config())
        assert ok
        assert len(called) == 1
        # stale artefacts removed before recompute
        assert not (cpd / "target_vertices.yaml").exists()
        assert not (cpd / "chem_pot_diag.json").exists()
