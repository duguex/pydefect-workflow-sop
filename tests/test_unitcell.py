"""Tests for vasp_sop.defect.unitcell — unitcell post-processing."""

from pathlib import Path

from vasp_sop.core.config import PipelineConfig


class TestBuildUnitcellYaml:
    """build_unitcell_yaml — band/DOS/dielectric post-processing."""

    def test_skip_when_yaml_exists(self, tmp_path: Path):
        """If unitcell.yaml exists, returns early without running commands."""
        from vasp_sop.defect.unitcell import build_unitcell_yaml
        (tmp_path / "unitcell.yaml").write_text("dummy\n")
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="GaN"))

    def test_calls_vise_commands(self, tmp_path: Path, monkeypatch):
        """With band/dos/dielectric dirs, runs vise + pydefect commands."""
        for d in ("band", "dos", "dielectric"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "OUTCAR").write_text("converged\n")
        (tmp_path / "band" / "vasprun.xml").write_text("<xml/>")
        (tmp_path / "dos" / "OUTCAR").write_text("converged\n")
        (tmp_path / "dielectric" / "OUTCAR").write_text("converged\n")

        recorded = []
        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local",
                           lambda cmd, cwd, **kw: recorded.append(cmd))

        from vasp_sop.defect.unitcell import build_unitcell_yaml
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="GaN"))

        assert any("vise pb" in c for c in recorded)
        assert any("vise pd" in c for c in recorded)
        assert any("pydefect_vasp le" in c for c in recorded)
        assert any("pydefect_vasp u" in c for c in recorded)

    def test_quotes_paths_with_parentheses(self, tmp_path: Path, monkeypatch):
        """All pydefect_vasp u path args are shell-safe (#0023)."""
        import shlex

        uc = tmp_path / "Sn(SeO3)2" / "unitcell"
        for d in ("band", "dos", "dielectric"):
            (uc / d).mkdir(parents=True)
            (uc / d / "OUTCAR").write_text("converged\n")
        (uc / "band" / "vasprun.xml").write_text("<xml/>\n")
        recorded = []
        monkeypatch.setattr(
            "vasp_sop.defect.pydefect_adapter.run_local",
            lambda cmd, cwd, **kw: recorded.append(cmd),
        )

        from vasp_sop.defect.unitcell import build_unitcell_yaml

        build_unitcell_yaml(uc, PipelineConfig(formula="Sn(SeO3)2"))
        cmd = next(c for c in recorded if "pydefect_vasp u" in c)
        assert shlex.quote(str((uc / "band" / "vasprun.xml").resolve())) in cmd
        assert shlex.quote(str((uc / "band" / "OUTCAR").resolve())) in cmd
        assert shlex.quote(str((uc / "dielectric" / "OUTCAR").resolve())) in cmd
        assert shlex.quote("Sn(SeO3)2") in cmd

    def test_dielectric_failure_nonfatal(self, tmp_path: Path, monkeypatch):
        """vise pdf failure logs a warning, does not crash."""
        for d in ("band", "dos", "dielectric"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "OUTCAR").write_text("converged\n")

        def fake_run(cmd, cwd, **kw):
            if "vise pdf" in str(cmd):
                raise RuntimeError("simulated")

        recorded = []
        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local",
                           lambda cmd, cwd, **kw: recorded.append(cmd) or
                           fake_run(cmd, cwd, **kw) if "vise pdf" in str(cmd) else None)

        from vasp_sop.defect.unitcell import build_unitcell_yaml
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="GaN"))



    def test_zero_gap_failure_records_terminal_diagnostic(self, tmp_path: Path, monkeypatch):
        for name in ("band", "dos", "dielectric"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "OUTCAR").write_text("converged\n")
        (tmp_path / "band" / "vasprun.xml").write_text("<xml/>\n")

        def fail_zero_gap(cmd, cwd, **kwargs):
            if "pydefect_vasp u" in cmd:
                raise RuntimeError("pydefect_vasp u failed: zero band gap")

        monkeypatch.setattr("vasp_sop.defect.pydefect_adapter.run_local", fail_zero_gap)

        from vasp_sop.defect.unitcell import build_unitcell_yaml
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="SeO2"))

        import json
        status = json.loads((tmp_path / "unitcell_build_status.json").read_text())
        assert status["status"] == "failed"
        assert status["reason"] == "zero_gap"
        assert "zero band gap" in status["diagnostic"]

    def test_failure_reason_classification_is_conservative(self):
        from vasp_sop.defect.unitcell import _unitcell_failure_reason

        assert _unitcell_failure_reason("ZERO BAND GAP") == "zero_gap"
        assert _unitcell_failure_reason("near-zero gap detected") == "zero_gap"
        assert _unitcell_failure_reason("near zero band gap") == "zero_gap"
        assert _unitcell_failure_reason("missing vasprun.xml") == "missing_vasprun"
class TestCopyInputFromOpt:
    """_copy_input_from_opt — copies files from structure_opt to task dir."""

    def test_copies_poscar(self, tmp_path: Path):
        from vasp_sop.defect.unitcell import _copy_input_from_opt
        src = tmp_path / "src"
        src.mkdir()
        (src / "POSCAR").write_text("dummy\n")
        dst = tmp_path / "dst"
        dst.mkdir()
        _copy_input_from_opt(src, dst)
        assert (dst / "POSCAR").is_file()

    def test_uses_contcar_when_both_exist(self, tmp_path: Path):
        """CONTCAR takes priority over POSCAR for band/dos/dielectric tasks."""
        from vasp_sop.defect.unitcell import _copy_input_from_opt
        src = tmp_path / "src"
        src.mkdir()
        (src / "POSCAR").write_text("initial structure\n")
        (src / "CONTCAR").write_text("optimized structure\n")
        dst = tmp_path / "dst"
        dst.mkdir()
        _copy_input_from_opt(src, dst)
        assert (dst / "POSCAR").read_text() == "optimized structure\n"

    def test_copies_prior_info(self, tmp_path: Path):
        from vasp_sop.defect.unitcell import _copy_input_from_opt
        src = tmp_path / "src"
        src.mkdir()
        (src / "prior_info.yaml").write_text("dummy\n")
        dst = tmp_path / "dst"
        dst.mkdir()
        _copy_input_from_opt(src, dst)
        assert (dst / "prior_info.yaml").is_file()

    def test_skips_when_src_missing(self, tmp_path: Path):
        """No crash when src has no POSCAR or prior_info.yaml."""
        from vasp_sop.defect.unitcell import _copy_input_from_opt
        src = tmp_path / "src"
        src.mkdir()
        dst = tmp_path / "dst"
        dst.mkdir()
        _copy_input_from_opt(src, dst)


class TestGetTaskDirs:
    """_get_task_dirs — returns band, dos, dielectric paths."""

    def test_returns_three_dirs(self):
        from vasp_sop.defect.unitcell import _get_task_dirs
        dirs = _get_task_dirs(Path("/root"), PipelineConfig(formula="GaN"))
        assert len(dirs) == 3
        assert all(d.name in ("band", "dos", "dielectric") for d in dirs)
