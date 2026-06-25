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

        config = PipelineConfig(formula="X", supercell_tool="pydefect",
                                supercell_min_atoms=10, supercell_max_atoms=50)

        # build_all calls pydefect s, which will fail — that's ok,
        # we just want to verify the mkdir happened before it.
        with pytest.raises((FileNotFoundError, RuntimeError)):
            build_all(defect_root, target_dir, config)

        assert defect_root.is_dir()

    def test_raises_on_missing_poscar(self, tmp_path: Path):
        """Issue #16: build_all must raise FileNotFoundError when the target
        POSCAR is missing — protects against regressions of the early guard
        at defect/builder.py:33-34."""
        defect_root = tmp_path / "defect"
        target_dir = tmp_path / "no_target"  # never created

        config = PipelineConfig(formula="X", supercell_tool="pydefect",
                                supercell_min_atoms=10, supercell_max_atoms=50)

        with pytest.raises(FileNotFoundError, match="POSCAR"):
            build_all(defect_root, target_dir, config)


def test_build_supercell_doped(tmp_path: Path):
    """_build_supercell with supercell_tool='doped' creates supercell_info.json."""
    from pymatgen.core.structure import Structure

    # Create a minimal NaCl primitive cell (2 atoms)
    uc = Structure.from_spacegroup(
        "Fm-3m",
        [[5.46, 0, 0], [0, 5.46, 0], [0, 0, 5.46]],
        ["Na", "Cl"],
        [[0, 0, 0], [0.5, 0.5, 0.5]],
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    uc.to(filename=str(target_dir / "POSCAR"))

    defect_root = tmp_path / "defect"
    defect_root.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig(
        formula="NaCl",
        supercell_tool="doped",
        supercell_min_distance=10.0,
        supercell_min_atoms=50,
        supercell_max_atoms=300,
    )

    from vasp_sop.defect.builder import _build_supercell
    _build_supercell(defect_root, target_dir / "POSCAR", config)

    sc_json = defect_root / "supercell_info.json"
    assert sc_json.is_file(), "supercell_info.json was not created"

    # Verify it loads correctly via pydefect's loader
    from pydefect.input_maker.supercell_info import SupercellInfo
    sc_info = SupercellInfo.load(str(sc_json))
    assert sc_info.space_group == "Fm-3m"
    assert sc_info.structure.num_sites >= 50
    assert len(sc_info.sites) >= 2  # at least Na and Cl site groups
    assert sc_info.transformation_matrix is not None


def test_doped_supercell_sites_sorted(tmp_path: Path):
    """Issues #19 + #21: equivalent_atoms must be in ascending index order.

    The prior hand-rolled loop appended equivalent_atoms in raw iteration
    order; StructureSymmetrizer sorts them. This regression test ensures
    the sort is preserved.
    """
    from pymatgen.core.structure import Structure

    uc = Structure.from_spacegroup(
        "Fm-3m",
        [[5.46, 0, 0], [0, 5.46, 0], [0, 0, 5.46]],
        ["Na", "Cl"],
        [[0, 0, 0], [0.5, 0.5, 0.5]],
    )
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    uc.to(filename=str(target_dir / "POSCAR"))

    defect_root = tmp_path / "defect"
    defect_root.mkdir(parents=True, exist_ok=True)
    config = PipelineConfig(
        formula="NaCl",
        supercell_tool="doped",
        supercell_min_distance=10.0,
        supercell_min_atoms=50,
        supercell_max_atoms=300,
    )

    from vasp_sop.defect.builder import _build_supercell
    _build_supercell(defect_root, target_dir / "POSCAR", config)

    from pydefect.input_maker.supercell_info import SupercellInfo
    sc_info = SupercellInfo.load(str(defect_root / "supercell_info.json"))

    for name, site in sc_info.sites.items():
        eq = list(site.equivalent_atoms)
        assert eq == sorted(eq), (
            f"Site {name} equivalent_atoms not sorted: {eq}"
        )
