"""Tests for the configuration system."""

import json
import tempfile
from pathlib import Path

import pytest

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
            PipelineConfig(formula="GaN", supercell_min_atoms=500, supercell_max_atoms=200)

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
        c1 = PipelineConfig(formula="GaN", interstitial=True,
                            interstitial_indices=[0, 2, 5])
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
