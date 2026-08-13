"""Tests for the configuration system."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from vasp_sop.core.config import PipelineConfig


class TestPipelineConfig:
    def test_minimal_valid(self):
        c = PipelineConfig(formula="GaN")
        assert c.formula == "GaN"
        assert c.dopant_elements == []
        assert c.complex_defect_order == 1

    def test_empty_formula_raises(self):
        with pytest.raises(ValueError, match="formula"):
            PipelineConfig(formula="")

    def test_invalid_supercell_range(self):
        with pytest.raises(ValueError, match="supercell_max_atoms"):
            PipelineConfig(
                formula="GaN", supercell_min_atoms=500, supercell_max_atoms=200
            )

    def test_negative_complex_order(self):
        with pytest.raises(ValueError, match="complex_defect_order"):
            PipelineConfig(formula="GaN", complex_defect_order=0)

    def test_negative_cutoff(self):
        with pytest.raises(ValueError, match="remote_cutoff"):
            PipelineConfig(formula="GaN", remote_cutoff=-1)

    def test_negative_energy_step(self):
        with pytest.raises(ValueError, match="energy_adjust_step"):
            PipelineConfig(formula="GaN", energy_adjust_step=0)

    def test_yaml_roundtrip(self):
        c1 = PipelineConfig(formula="GaN", dopant_elements=["Mg"])
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            tmp = Path(f.name)
        try:
            c1.to_yaml(tmp)
            c2 = PipelineConfig.from_yaml(tmp)
            assert c2.formula == "GaN"
            assert c2.dopant_elements == ["Mg"]
        finally:
            tmp.unlink(missing_ok=True)

    def test_correction_policy_round_trip(self):
        c1 = PipelineConfig(
            formula="GaN", correction_policy="custom_molecular_reference"
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            tmp = Path(f.name)
        try:
            c1.to_yaml(tmp)
            c2 = PipelineConfig.from_yaml(tmp)
            assert c2.correction_policy == "custom_molecular_reference"
        finally:
            tmp.unlink(missing_ok=True)

    def test_invalid_correction_policy(self):
        with pytest.raises(ValueError, match="correction_policy"):
            PipelineConfig(formula="GaN", correction_policy="mp2020")

    def test_diatomic_correction_defaults(self):
        config = PipelineConfig(formula="GaN")
        assert config.molecule_corrections == {
            "H2": 0.358,
            "N2": 0.722,
            "O2": 1.374,
            "F2": 0.924,
            "Cl2": 1.228,
        }


    def test_yaml_roundtrip_minmax_pydefect(self):
        """Issue #13: pydefect supercell min/max_atoms must survive round-trip,
        even though the doped-specific min_distance is also written."""
        c1 = PipelineConfig(
            formula="GaN",
            supercell_tool="pydefect",
            supercell_min_atoms=200,
            supercell_max_atoms=600,
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            tmp = Path(f.name)
        try:
            c1.to_yaml(tmp)
            c2 = PipelineConfig.from_yaml(tmp)
            assert c2.supercell_tool == "pydefect"
            assert c2.supercell_min_atoms == 200
            assert c2.supercell_max_atoms == 600
            assert c2.supercell_min_distance == 10.0  # default, no data loss
        finally:
            tmp.unlink(missing_ok=True)

    def test_yaml_roundtrip_minmax_doped(self):
        """Issue #13: doped supercell min_distance must survive round-trip,
        even though the pydefect-specific min/max_atoms are also written."""
        c1 = PipelineConfig(
            formula="GaN",
            supercell_tool="doped",
            supercell_min_distance=12.5,
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            tmp = Path(f.name)
        try:
            c1.to_yaml(tmp)
            c2 = PipelineConfig.from_yaml(tmp)
            assert c2.supercell_tool == "doped"
            assert c2.supercell_min_distance == 12.5
            assert c2.supercell_min_atoms == 200  # default, no data loss
            assert c2.supercell_max_atoms == 600
        finally:
            tmp.unlink(missing_ok=True)

    def test_yaml_flat_backward_compat(self):
        """Flat-format YAML should still load."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("formula: Al2O3\n")
            tmp = Path(f.name)
        try:
            c = PipelineConfig.from_yaml(tmp, root=Path("/my/project"))
            assert c.formula == "Al2O3"
            assert c.root == Path("/my/project")
        finally:
            tmp.unlink(missing_ok=True)

    def test_yaml_flat_corrections_preserve_all_diatomics(self, tmp_path):
        path = tmp_path / "flat.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "formula": "ClF",
                    "molecule_corrections": {
                        "H2": 0.111,
                        "N2": 0.222,
                        "O2": 1.374,
                        "F2": 0.924,
                        "Cl2": 1.228,
                    },
                    "correction_policy": "custom_molecular_reference",
                }
            )
        )
        config = PipelineConfig.from_yaml(path)
        assert config.molecule_corrections == {
            "H2": 0.111,
            "N2": 0.222,
            "O2": 1.374,
            "F2": 0.924,
            "Cl2": 1.228,
        }
        assert config.correction_policy == "custom_molecular_reference"

    def test_from_legacy_json(self):
        """Migrate from legacy info.json format."""
        data = {
            "obj": "GaN",
            "dopant_element": ["Mg"],
            "interstitial": True,
            "iindex": ["0", "1"],
            "complex_defect": 2,
            "remote": 3.0,
            "pp": ["Cr_sv_GW"],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(data, f)
            tmp = Path(f.name)
        try:
            c = PipelineConfig.from_legacy_json(tmp)
            assert c.formula == "GaN"
            assert c.dopant_elements == ["Mg"]
            assert c.interstitial is True
            assert c.interstitial_indices == [0, 1]
            assert c.complex_defect_order == 2
            assert c.remote_cutoff == 3.0
            assert c.potcar_overrides == ["Cr_sv_GW"]
        finally:
            tmp.unlink(missing_ok=True)

    def test_interstitial_indices_roundtrip(self):
        """interstitial_indices survives a YAML round-trip."""
        c1 = PipelineConfig(
            formula="GaN", interstitial=True, interstitial_indices=[0, 2, 5]
        )
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            tmp = Path(f.name)
        try:
            c1.to_yaml(tmp)
            c2 = PipelineConfig.from_yaml(tmp)
            assert c2.interstitial is True
            assert c2.interstitial_indices == [0, 2, 5]
        finally:
            tmp.unlink(missing_ok=True)

    def test_generate_config(self):
        """generate_config() produces valid plan.yaml."""
        from vasp_sop.core.config import generate_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = generate_config(root, formula="GaN", dopant_elements=["Mg"])
            assert path.name == "plan.yaml"
            assert path.exists()
            c = PipelineConfig.from_yaml(path, root=root)
            assert c.formula == "GaN"
            assert c.dopant_elements == ["Mg"]

    def test_empty_yaml_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write("")
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError):
                PipelineConfig.from_yaml(tmp)
        finally:
            tmp.unlink(missing_ok=True)


class TestScope:
    def test_default_scope_is_defects(self):
        c = PipelineConfig(formula="GaN")
        assert c.scope == "defects"

    def test_scope_parsed_from_plan(self):
        c = PipelineConfig.from_plan(
            {"project": {"formula": "GaN", "scope": "chemical-environment"}}
        )
        assert c.scope == "chemical-environment"

    def test_scope_missing_from_plan_defaults_to_defects(self):
        c = PipelineConfig.from_plan({"project": {"formula": "GaN"}})
        assert c.scope == "defects"

    def test_invalid_scope_rejected(self):
        with pytest.raises(ValueError, match="scope"):
            PipelineConfig(formula="GaN", scope="just-cpd")


class TestStage2SocParsing:
    def test_stage2_soc_under_parameters(self):
        c = PipelineConfig.from_plan({
            "project": {"formula": "GaN"},
            "parameters": {"soc": True, "stage2_soc": True},
        })
        assert c.stage2_soc is True

    def test_toplevel_stage2_soc_falls_back_with_warning(self):
        # ADR 0014 rollout wrote it toplevel (2026-08-10); the parser must
        # not silently disable the SOC supplement again.
        c = PipelineConfig.from_plan({
            "project": {"formula": "GaN"},
            "parameters": {"soc": True},
            "stage2_soc": True,
        })
        assert c.stage2_soc is True

    def test_stage2_soc_absent_defaults_false(self):
        c = PipelineConfig.from_plan({
            "project": {"formula": "GaN"},
            "parameters": {"soc": True},
        })
        assert c.stage2_soc is False


class TestReferencePhaseSelection:
    """ADR 0023: formula-search fallback must pick the lowest e_above_hull
    polymorph, never MP's first (material_id-sorted) doc."""

    def test_fallback_picks_lowest_eah_not_first_doc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MP_API_KEY", "test-key")
        # Main loop finds no target phase (no cpd dirs) -> fallback path.
        monkeypatch.setattr(
            "vasp_sop.materials.fetch_candidate_phases", lambda *a, **k: None
        )
        # MP returns docs in ascending-material_id order (the 2026-08-13
        # Y2Ti2O7 failure mode: first doc is the LEAST stable polymorph).
        docs = [
            SimpleNamespace(
                material_id="mp-1173093",
                formula_pretty="Y2Ti2O7",
                energy_above_hull=0.162,
            ),
            SimpleNamespace(
                material_id="mp-5373",
                formula_pretty="Y2Ti2O7",
                energy_above_hull=0.011,
            ),
        ]

        class _Search:
            def search(self, **kw):
                return docs

        class _Mats:
            summary = _Search()

        class _MR:
            def __init__(self, *a):
                self.materials = _Mats()

            def get_structure_by_material_id(self, mid):
                from pymatgen.core import Lattice, Structure

                return Structure(
                    Lattice.cubic(3.9),
                    ["Na", "Cl"],
                    [[0, 0, 0], [0.5, 0.5, 0.5]],
                )

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("mp_api.client.MPRester", _MR)

        from vasp_sop.core.config import generate_config

        path = generate_config(tmp_path, formula="Y2Ti2O7")
        c = PipelineConfig.from_yaml(path, root=tmp_path)
        assert c.poscar_src == "MP mp-5373"
