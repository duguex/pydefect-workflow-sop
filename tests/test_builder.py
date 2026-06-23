"""Tests for vasp_sop.defect.builder — defect structure generation."""

from pathlib import Path

import pytest

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.builder import build_all


class TestBuildAll:
    """build_all — supercell construction, defect enumeration, VASP inputs."""

    def test_creates_defect_dir(self, tmp_path: Path):
        """build_all creates defect_root if it doesn't exist."""
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "POSCAR").write_text("dummy\n")

        defect_root = tmp_path / "defect"
        assert not defect_root.is_dir()

        config = PipelineConfig(formula="X", supercell_min_atoms=10, supercell_max_atoms=50)

        # build_all calls pydefect s, which will fail — that's ok,
        # we just want to verify the mkdir happened before it.
        with pytest.raises((FileNotFoundError, RuntimeError)):
            build_all(defect_root, target_dir, config)

        assert defect_root.is_dir()

    def test_raises_on_missing_poscar(self, tmp_path: Path):
        """build_all raises FileNotFoundError if target POSCAR missing."""
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        defect_root = tmp_path / "defect"

        with pytest.raises(FileNotFoundError, match="POSCAR"):
            build_all(defect_root, target_dir, PipelineConfig(formula="X"))

