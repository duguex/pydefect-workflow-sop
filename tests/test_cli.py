"""Tests for cli.py: validation gating added in commit 90f7820.

Verifies that cmd_run / cmd_stage block on schema errors, and that
cmd_run --force bypasses validation. We invoke the CLI in-process via
the click-style main() to keep this hermetic.
"""

import io
import sys
from unittest import mock

import pytest

import pydefect_auto.cli as cli


def _run_cli(args, capsys):
    """Run main() with the given argv, return (rc, stdout)."""
    with mock.patch.object(sys, "argv", ["pydefect-run"] + args):
        try:
            cli.main()
        except SystemExit as e:
            rc = e.code
        else:
            rc = 0
    return capsys.readouterr(), rc


class TestCmdRun:
    def test_no_plan_file(self, tmp_path, capsys):
        out, _ = _run_cli(["run", str(tmp_path)], capsys)
        assert "plan.yaml not found" in out.out

    def test_valid_plan_passes_validation(self, plan_yaml_path, capsys, tmp_project):
        # We can't actually run the pipeline (no VASP). The point is to
        # confirm validation passes; stage1 will then fail with a clear
        # error about missing structure_opt/POSCAR, not a validation error.
        out, _ = _run_cli(["run", str(tmp_project)], capsys)
        # No validation error in the output (stage1 may complain about
        # missing POSCAR, that's expected and out of scope here)
        assert "校验失败" not in out.out

    def test_invalid_plan_blocks(self, tmp_path, capsys):
        # Write a plan with missing required fields
        import yaml
        plan = {"project": {"obj": ""}}  # missing parameters/supercell/...
        with open(tmp_path / "plan.yaml", "w") as f:
            yaml.safe_dump(plan, f)
        out, _ = _run_cli(["run", str(tmp_path)], capsys)
        assert "校验失败" in out.out
        # Should NOT print "Run with --force" with --force absent
        assert "--force" in out.out  # hint is shown

    def test_force_bypasses(self, tmp_path, capsys):
        import yaml
        plan = {"project": {"obj": ""}}
        with open(tmp_path / "plan.yaml", "w") as f:
            yaml.safe_dump(plan, f)
        out, _ = _run_cli(["run", str(tmp_path), "--force"], capsys)
        # Validation still shown (informational), but pipeline continues
        assert "校验失败" in out.out
        assert "--force set" in out.out

    def test_plan_validate_subcommand(self, tmp_path, capsys):
        import yaml
        bad = {"project": {"obj": "Si"}}  # missing required keys
        with open(tmp_path / "plan.yaml", "w") as f:
            yaml.safe_dump(bad, f)
        out, _ = _run_cli(["plan", str(tmp_path), "--validate"], capsys)
        assert "校验失败" in out.out

    def test_plan_validate_passes(self, plan_yaml_path, capsys, tmp_project):
        out, _ = _run_cli(["plan", str(tmp_project), "--validate"], capsys)
        assert "校验通过" in out.out


class TestCmdStage:
    def test_blocks_on_invalid_plan(self, tmp_path, capsys):
        import yaml
        plan = {"project": {"obj": ""}}
        with open(tmp_path / "plan.yaml", "w") as f:
            yaml.safe_dump(plan, f)
        out, _ = _run_cli(["stage", "1", str(tmp_path)], capsys)
        assert "校验失败" in out.out

    def test_disabled_stage(self, plan_yaml_path, capsys, tmp_project, valid_plan_dict):
        # Disable stage 1
        import yaml
        with open(tmp_project / "plan.yaml") as f:
            plan = yaml.safe_load(f)
        plan["stages"]["unitcell"] = False
        with open(tmp_project / "plan.yaml", "w") as f:
            yaml.safe_dump(plan, f)
        out, _ = _run_cli(["stage", "1", str(tmp_project)], capsys)
        assert "disabled" in out.out.lower()
