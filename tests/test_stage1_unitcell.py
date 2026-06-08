"""Tests for stage1 (unitcell). Verifies idempotency and POSCAR/CONTCAR flow.

Stage1 must:
- Skip structure_opt if .stage1_done flag exists
- Copy CONTCAR -> POSCAR after structure_opt completes
- Run vise vasp_set for band/dos/dielectric
- Generate unitcell.yaml at the end
"""

from pathlib import Path
from unittest import mock

import pytest

from pydefect_auto.stages.stage1_unitcell import run as stage1_run


class TestStage1Idempotency:
    def test_skip_when_done(self, tmp_project, valid_plan_dict):
        # Mark stage1 done
        (tmp_project / "unitcell" / ".stage1_done").touch()
        result = stage1_run(str(tmp_project), valid_plan_dict)
        assert result is True

    def test_no_poscar_fails(self, tmp_project, valid_plan_dict):
        # structure_opt/ exists but no POSCAR
        result = stage1_run(str(tmp_project), valid_plan_dict)
        assert result is False

    def test_missing_structure_opt_dir(self, tmp_project, valid_plan_dict):
        # No structure_opt/ directory at all
        import shutil
        shutil.rmtree(tmp_project / "unitcell" / "structure_opt")
        result = stage1_run(str(tmp_project), valid_plan_dict)
        assert result is False


class TestStage1InputsCheck:
    def test_vasp_input_check_recognizes_complete_dir(self, si_poscar):
        from pydefect_auto.utils import vasp_input_check
        # Just POSCAR isn't enough; need INCAR/POTCAR/KPOINTS
        assert vasp_input_check(str(si_poscar.parent)) is False

    def test_vasp_input_check_with_all_files(self, si_poscar):
        from pydefect_auto.utils import vasp_input_check
        d = si_poscar.parent
        (d / "INCAR").touch()
        (d / "POTCAR").touch()
        (d / "KPOINTS").touch()
        assert vasp_input_check(str(d)) is True


class TestPPFlag:
    def test_empty_pp_returns_empty_string(self, valid_plan_dict):
        from pydefect_auto.stages.stage1_unitcell import _pp_flag
        assert _pp_flag(valid_plan_dict) == ""

    def test_pp_present_returns_flag(self, valid_plan_dict):
        from pydefect_auto.stages.stage1_unitcell import _pp_flag
        valid_plan_dict["pp"] = ["Si", "C_pv"]
        flag = _pp_flag(valid_plan_dict)
        assert "--potcar" in flag
        assert "Si" in flag
        assert "C_pv" in flag


class TestHubbardFlag:
    def test_disabled(self, valid_plan_dict):
        from pydefect_auto.stages.stage1_unitcell import _hubbard_flag
        assert _hubbard_flag(valid_plan_dict) == ""

    def test_enabled(self, valid_plan_dict):
        from pydefect_auto.stages.stage1_unitcell import _hubbard_flag
        valid_plan_dict["hubbard_u"] = True
        assert "set_hubbard_u" in _hubbard_flag(valid_plan_dict)
