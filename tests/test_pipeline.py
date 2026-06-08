"""Smoke tests for the 7-stage pipeline.

These tests run with mocked external commands (vise, pydefect, vasp).
Goal is to verify stage sequencing and idempotency, NOT to validate
the actual VASP outputs.
"""

import os
import shutil
from pathlib import Path
from unittest import mock

import pytest


# ---------- pipeline.single_run ----------

class TestSingleRun:
    def test_pipelines_iterates_in_order(self, tmp_project, valid_plan_dict):
        """Each stage module gets called once with the right args."""
        from pydefect_auto import pipeline
        from pydefect_auto.config import _flatten

        # Stages consume the FLAT dict, not the raw nested plan
        info = _flatten(valid_plan_dict)

        # Stub every stage module
        calls = []

        def make_stub(name):
            def run(project_root, info, auto=False):
                calls.append(name)
                return True
            return run

        with mock.patch.dict(os.environ, {"PYTHONPATH": ""}):
            stubs = {}
            for _, mod_name, _, _ in pipeline.STAGES:
                m = mock.MagicMock()
                m.run = make_stub(mod_name)
                stubs[mod_name] = m
                # Make __import__ return our stub for this module
            real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def fake_import(name, *args, **kwargs):
                if name.startswith("pydefect_auto.stages."):
                    short = name.split(".")[-1]
                    if short in stubs:
                        return stubs[short]
                return real_import(name, *args, **kwargs)

            with mock.patch("builtins.__import__", side_effect=fake_import):
                result = pipeline.single_run(
                    str(tmp_project), info, auto=False, stage_config={}
                )
        assert result is True
        # All 7 stages called in order
        assert len(calls) == 7
        assert calls == [s[1] for s in pipeline.STAGES]

    def test_disabled_stage_skipped(self, tmp_project, valid_plan_dict):
        from pydefect_auto import pipeline
        from pydefect_auto.config import _flatten

        info = _flatten(valid_plan_dict)
        calls = []

        def make_stub(name):
            def run(project_root, info, auto=False):
                calls.append(name)
                return True
            return run

        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pydefect_auto.stages."):
                short = name.split(".")[-1]
                return mock.MagicMock(run=make_stub(short))
            return real_import(name, *args, **kwargs)

        # Disable cpd stage
        cfg = {s[2]: True for s in pipeline.STAGES}
        cfg["cpd"] = False

        with mock.patch("builtins.__import__", side_effect=fake_import):
            result = pipeline.single_run(
                str(tmp_project), info, auto=False, stage_config=cfg
            )
        assert result is True
        assert "stage2_cpd" not in calls
        # Other 6 still ran
        assert len(calls) == 6

    def test_failed_stage_stops_pipeline(self, tmp_project, valid_plan_dict):
        from pydefect_auto import pipeline
        from pydefect_auto.config import _flatten

        info = _flatten(valid_plan_dict)
        calls = []
        # Make stage 1 fail; subsequent stages should NOT be called
        def make_stub(name, success):
            def run(project_root, info, auto=False):
                calls.append(name)
                return success
            return run

        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("pydefect_auto.stages."):
                short = name.split(".")[-1]
                # First stage fails, others succeed
                success = short != "stage1_unitcell"
                return mock.MagicMock(run=make_stub(short, success))
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fake_import):
            result = pipeline.single_run(
                str(tmp_project), info, auto=False, stage_config={}
            )
        # Pipeline returns False on first failure
        assert result is False
        # Stage 1 ran and failed
        assert calls[0] == "stage1_unitcell"
        # No later stages
        assert "stage2_cpd" not in calls


class TestStagesOrder:
    def test_pipeline_declares_7_stages(self):
        from pydefect_auto.pipeline import STAGES
        assert len(STAGES) == 7
        # Stage numbers and keys
        expected = [
            ("1", "stage1_unitcell", "unitcell"),
            ("2", "stage2_cpd", "cpd"),
            ("3", "stage3_defect_gen", "defect_gen"),
            ("4", "stage4_submit", "submit"),
            ("5", "stage5_postproc", "postproc"),
            ("6", "stage6_doping", "doping"),
            ("7", "stage7_complex", "complex"),
        ]
        for actual, exp in zip(STAGES, expected):
            assert actual[0] == exp[0]
            assert actual[1] == exp[1]
            assert actual[2] == exp[2]
