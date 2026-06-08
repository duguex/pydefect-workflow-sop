"""Tests for pydefect_auto.config: plan.yaml -> flat info dict.

The config module is the seam between plan.yaml (nested) and stage modules
(consume flat dict). Schema changes must keep _flatten() in sync.
"""

import pytest

from pydefect_auto.config import PLAN_FILENAME, _flatten, load_plan


class TestFlatten:
    def test_basic_fields(self, valid_plan_dict):
        flat = _flatten(valid_plan_dict)
        assert flat["obj"] == "SiC"
        assert flat["dopant_element"] == ["O"]  # singular, not "dopant_elements"
        assert flat["encut"] == 520
        assert flat["hubbard_u"] is False
        assert flat["pp"] == ["Si", "C"]
        assert flat["functional"] == "pbesol"

    def test_supercell(self, valid_plan_dict):
        flat = _flatten(valid_plan_dict)
        assert flat["supercell"] == {"max_atoms": 600, "min_atoms": 200}

    def test_defect_aliases(self, valid_plan_dict):
        flat = _flatten(valid_plan_dict)
        # Aliases: YAML uses plural/canonical, flat uses stage-friendly
        assert flat["interstitial"] is False
        assert flat["complex_defect"] == 1  # from complex_n
        assert flat["max_distance"] == 3.0
        assert flat["min_distance"] == 0.3
        assert flat["charges"] == [0]  # NEW: required since commit 90f7820
        assert flat["gas_corrections"] == {"O2": 1.374}

    def test_stages_passthrough(self, valid_plan_dict):
        flat = _flatten(valid_plan_dict)
        assert flat["stages"]["unitcell"] is True
        assert flat["stages"]["complex"] is False

    def test_raw_preserved(self, valid_plan_dict):
        flat = _flatten(valid_plan_dict)
        assert flat["_raw"] is valid_plan_dict

    def test_minimal_plan(self):
        # All required fields absent -> empty defaults
        minimal = {
            "project": {"obj": "Si"},
            "parameters": {"functional": "pbesol", "encut": None},
            "supercell": {"max_atoms": 600, "min_atoms": 200},
            "defects": {
                "vacancies": [], "substitutionals": [], "interstitials": False,
                "iindex": [], "charges": [0], "complex_n": 1,
            },
            "stages": {
                "unitcell": True, "cpd": True, "defect_gen": True,
                "submit": True, "postproc": True, "doping": False, "complex": False,
            },
        }
        flat = _flatten(minimal)
        assert flat["obj"] == "Si"
        assert flat["encut"] is None
        assert flat["dopant_element"] == []


class TestLoadPlan:
    def test_loads_file(self, plan_yaml_path):
        info, raw = load_plan(str(plan_yaml_path.parent))
        assert info["obj"] == "SiC"
        assert raw["project"]["obj"] == "SiC"

    def test_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_plan(str(tmp_path))

    def test_filename_constant(self):
        assert PLAN_FILENAME == "plan.yaml"
