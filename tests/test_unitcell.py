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
        monkeypatch.setattr("vasp_sop.defect.unitcell.run_local",
                           lambda cmd, cwd, **kw: recorded.append(cmd))

        from vasp_sop.defect.unitcell import build_unitcell_yaml
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="GaN"))

        assert any("vise pb" in c for c in recorded)
        assert any("vise pd" in c for c in recorded)
        assert any("pydefect_vasp le" in c for c in recorded)
        assert any("pydefect_vasp u" in c for c in recorded)

    def test_dielectric_failure_nonfatal(self, tmp_path: Path, monkeypatch):
        """vise pdf failure logs a warning, does not crash."""
        for d in ("band", "dos", "dielectric"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "OUTCAR").write_text("converged\n")

        def fake_run(cmd, cwd, **kw):
            if "vise pdf" in str(cmd):
                raise RuntimeError("simulated")

        recorded = []
        monkeypatch.setattr("vasp_sop.defect.unitcell.run_local",
                           lambda cmd, cwd, **kw: recorded.append(cmd) or
                           fake_run(cmd, cwd, **kw) if "vise pdf" in str(cmd) else None)

        from vasp_sop.defect.unitcell import build_unitcell_yaml
        build_unitcell_yaml(tmp_path, PipelineConfig(formula="GaN"))


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
