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

    def test_sync_lattice_from_perfect(self, tmp_path: Path):
        """Perfect(ISIF=3)收敛后,其晶格同步到全部 defect POSCAR——
        只换晶格,原子坐标(缺陷结构)不变;perfect 未收敛则不动。"""
        from pymatgen.core import Lattice, Structure
        from vasp_sop.defect import builder as b

        defect_root = tmp_path / "defect"
        perfect = defect_root / "perfect"
        a_def = defect_root / "Bi_Gd1_0"
        perfect.mkdir(parents=True)
        a_def.mkdir(parents=True)

        # perfect 弛豫晶格(ISIF=3 后)与构建晶格不同。
        build_lattice = Lattice.from_parameters(10.0, 10.0, 12.5, 90, 90, 90)
        relaxed_lattice = Lattice.from_parameters(10.253, 10.253, 12.5, 90, 90, 90)
        Structure(build_lattice, ["Gd", "O"], [[0, 0, 0], [0.5, 0.5, 0.5]]) \
            .to(fmt="poscar", filename=str(perfect / "CONTCAR"))
        def_struct = Structure(build_lattice, ["Gd", "O"], [[0, 0, 0], [0.5, 0.5, 0.5]])
        def_struct.to(fmt="poscar", filename=str(a_def / "POSCAR"))

        # 未同步前:构建晶格(对称)。
        before = Structure.from_file(str(a_def / "POSCAR"))
        assert before.lattice.a == pytest.approx(10.0)

        n = b.sync_lattice_from_perfect(defect_root)
        assert n == 1
        after = Structure.from_file(str(a_def / "POSCAR"))
        assert after.lattice.a == pytest.approx(10.253)
        # 原子坐标/组成不变(只换晶格)。
        assert [str(s.species_string) for s in after] == ["Gd", "O"]
        assert after.frac_coords[0].tolist() == pytest.approx([0.0, 0.0, 0.0])

    def test_sync_skips_when_perfect_unconverged(self, tmp_path: Path):
        from vasp_sop.defect import builder as b

        defect_root = tmp_path / "defect"
        perfect = defect_root / "perfect"
        perfect.mkdir(parents=True)
        a_def = defect_root / "Bi_Gd1_0"
        a_def.mkdir(parents=True)
        from pymatgen.core import Lattice, Structure
        Structure(Lattice.cubic(10.0), ["Gd"], [[0, 0, 0]]) \
            .to(fmt="poscar", filename=str(a_def / "POSCAR"))
        # perfect 无 CONTCAR → 同步为 no-op。
        assert b.sync_lattice_from_perfect(defect_root) == 0

    def test_perfect_incar_gets_isif3(self, tmp_path: Path, monkeypatch):
        """2026-08-16 协议修正:perfect 无缺陷超胞 ISIF=3(晶格弛豫);
        普通 defect 保持 ISIF=2(固定 perfect 晶格)。"""
        from vasp_sop.defect import builder as b

        defect_root = tmp_path / "defect"
        perfect = defect_root / "perfect"
        a_def = defect_root / "Bi_Gd1_0"
        perfect.mkdir(parents=True)
        a_def.mkdir(parents=True)
        # prepare_inputs 被 mock 成"生成过 INCAR"(实际不写), 触发
        # perfect 的 ISIF=3 patch;input_ready 恒 False 走生成分支。
        calls: list[str] = []
        monkeypatch.setattr(
            "vasp_sop.vasp.io.prepare_inputs",
            lambda *a, **k: calls.append(k.get("charge", "?")) or None,
        )
        monkeypatch.setattr(
            "vasp_sop.vasp.io.input_ready", lambda d: False,
        )
        from vasp_sop.core.config import PipelineConfig
        cfg = PipelineConfig(formula="Gd2GaSbO7", supercell_tool="doped",
                             supercell_min_atoms=100, supercell_max_atoms=600)
        b._generate_vasp_inputs(defect_root, cfg)

        perfect_incar = (perfect / "INCAR").read_text()
        assert "ISIF = 3" in perfect_incar, perfect_incar
        assert "ISIF" not in (a_def / "INCAR").read_text()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
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


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
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


class TestConfigFingerprint:
    """_config_fingerprint and _check_rebuild — issue #75."""

    def test_changes_on_different_config(self):
        """Different config values produce different fingerprints."""
        from vasp_sop.defect.builder import _config_fingerprint
        c1 = PipelineConfig(formula="GaN")
        c2 = PipelineConfig(formula="GaN", complex_defect_order=2)
        fp1 = _config_fingerprint(c1)
        fp2 = _config_fingerprint(c2)
        assert fp1 != fp2

    def test_stable_for_same_config(self):
        """Same config produces identical fingerprint across calls."""
        from vasp_sop.defect.builder import _config_fingerprint
        c = PipelineConfig(formula="SiC")
        assert _config_fingerprint(c) == _config_fingerprint(c)

    def test_check_rebuild_clears_flags_on_mismatch(self, tmp_path: Path):
        """_check_rebuild removes flag files when config fingerprint changes."""
        from vasp_sop.defect.builder import _config_fingerprint, _check_rebuild
        root = tmp_path / "defect"
        root.mkdir()
        c_old = PipelineConfig(formula="GaN")
        c_new = PipelineConfig(formula="GaN", complex_defect_order=3)

        # Write old fingerprint
        (root / ".build_fingerprint").write_text(
            _config_fingerprint(c_old) + "\n"
        )
        # Create flag files as if previous build ran
        (root / "supercell_info.json").touch()
        (root / "defect_generate_flag").touch()

        _check_rebuild(root, c_new)
        assert not (root / "supercell_info.json").is_file(), "flag should be cleared"
        assert not (root / "defect_generate_flag").is_file(), "flag should be cleared"

    def test_check_rebuild_preserves_flags_on_match(self, tmp_path: Path):
        """_check_rebuild leaves flag files untouched when fingerprint matches."""
        from vasp_sop.defect.builder import _config_fingerprint, _check_rebuild
        root = tmp_path / "defect"
        root.mkdir()
        c = PipelineConfig(formula="GaN")

        (root / ".build_fingerprint").write_text(
            _config_fingerprint(c) + "\n"
        )
        (root / "supercell_info.json").touch()

        _check_rebuild(root, c)
        assert (root / "supercell_info.json").is_file(), "flag should be preserved"


class TestWriteFingerprint:
    """_write_fingerprint — persists config fingerprint after build."""

    def test_writes_fingerprint_file(self, tmp_path: Path):
        from vasp_sop.defect.builder import _write_fingerprint, _config_fingerprint
        root = tmp_path / "defect"
        root.mkdir()
        config = PipelineConfig(formula="GaN")
        _write_fingerprint(root, config)
        fp_path = root / ".build_fingerprint"
        assert fp_path.is_file()
        content = fp_path.read_text().strip()
        assert content == _config_fingerprint(config)


class TestHandleInterstitials:
    """_handle_interstitials — early-return branches."""

    def test_skipped_when_disabled(self, tmp_path: Path):
        from vasp_sop.defect.builder import _handle_interstitials
        config = PipelineConfig(formula="GaN", interstitial=False)
        _handle_interstitials(tmp_path, config)
        # No crash = OK

    def test_skipped_without_supercell_info(self, tmp_path: Path):
        from vasp_sop.defect.builder import _handle_interstitials
        config = PipelineConfig(formula="GaN", interstitial=True)
        _handle_interstitials(tmp_path, config)

    def test_skipped_when_no_dos_extrema(self, tmp_path: Path):
        from vasp_sop.defect.builder import _handle_interstitials
        sc_info = tmp_path / "supercell_info.json"
        sc_info.write_text('{"interstitials": []}')
        config = PipelineConfig(formula="GaN", interstitial=True)
        _handle_interstitials(tmp_path, config)

    def test_skipped_when_interstitials_exist(self, tmp_path: Path):
        from vasp_sop.defect.builder import _handle_interstitials
        sc_info = tmp_path / "supercell_info.json"
        sc_info.write_text('{"interstitials": ["already_done"]}')
        config = PipelineConfig(formula="GaN", interstitial=True)
        _handle_interstitials(tmp_path, config)


class TestBuildAll:
    """build_all — calls sub-functions in correct order."""

    def test_calls_sub_functions(self, tmp_path: Path, monkeypatch):
        """With a target POSCAR, build_all calls _build_supercell,
        _handle_interstitials, _generate_defect_list, and others."""
        target = tmp_path / "target"
        target.mkdir()
        (target / "POSCAR").write_text("dummy\n")

        order = []
        monkeypatch.setattr("vasp_sop.defect.builder._build_supercell",
                           lambda *a, **kw: order.append("supercell"))
        monkeypatch.setattr("vasp_sop.defect.builder._handle_interstitials",
                           lambda *a, **kw: order.append("interstitials"))
        monkeypatch.setattr("vasp_sop.defect.builder._generate_defect_list",
                           lambda *a, **kw: order.append("defect_list"))
        monkeypatch.setattr("vasp_sop.defect.builder._generate_structures",
                           lambda *a, **kw: order.append("structures"))
        monkeypatch.setattr("vasp_sop.defect.builder._generate_vasp_inputs",
                           lambda *a, **kw: order.append("vasp_inputs"))
        monkeypatch.setattr("vasp_sop.defect.builder._check_rebuild",
                           lambda *a, **kw: order.append("check_rebuild"))
        monkeypatch.setattr("vasp_sop.defect.builder._write_fingerprint",
                           lambda *a, **kw: order.append("fingerprint"))

        from vasp_sop.defect.builder import build_all
        config = PipelineConfig(formula="GaN")
        build_all(tmp_path / "defect", target, config)

        assert "supercell" in order
        assert "defect_list" in order
        assert "structures" in order
        assert "vasp_inputs" in order
        assert "fingerprint" in order


class TestVerifyNelect:
    """verify_nelect — charge-count verification of defect INCARs."""

    def _make_dir(self, root: Path, name: str, comp: dict[str, int],
                  q: int | None, nelect: str | None) -> Path:
        """Create a defect dir with POSCAR/POTCAR/INCAR, return path."""
        wd = root / name
        wd.mkdir(parents=True)
        els = " ".join(comp.keys())
        counts = " ".join(str(n) for n in comp.values())
        wd.joinpath("POSCAR").write_text(
            "title\n1.0\n"
            "10.0 0.0 0.0\n0.0 10.0 0.0\n0.0 0.0 10.0\n"
            f"{els}\n{counts}\nDirect\n"
            + "\n".join("0 0 0" for _ in range(sum(comp.values())))
        )
        # POTCAR with real ZVALs (La 11, O 6, Zr_sv 12)
        zvals = {"La": 11.0, "O": 6.0, "Zr": 12.0, "Al": 3.0, "Sr": 10.0}
        pot = []
        for el in comp:
            zv = zvals.get(el, 6.0)
            pot.append(f"   TITEL  = PAW_PBE {el} 01Jan2000\n"
                       f"   POMASS = 1.0; ZVAL   = {zv:8.3f}    mass and valenz\n")
        wd.joinpath("POTCAR").write_text("".join(pot))
        incar = "ALGO = Normal\nNSW = 50\n"
        if nelect is not None:
            incar += f"NELECT = {nelect}\n"
        wd.joinpath("INCAR").write_text(incar)
        return wd

    def test_all_correct(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_nelect
        root = tmp_path / "df"
        self._make_dir(root, "perfect", {"La": 8, "Zr": 4, "O": 28}, 0, None)
        self._make_dir(root, "Va_O1_2", {"La": 8, "Zr": 4, "O": 27}, 2, "296")
        # correct: 8*11 + 4*12 + 27*6 - 2 = 88+48+162-2 = 296
        self._make_dir(root, "Va_O1_-1", {"La": 8, "Zr": 4, "O": 27}, -1, "299")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        assert verify_nelect(root, cfg) == []

    def test_detects_wrong_charged(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_nelect
        root = tmp_path / "df"
        self._make_dir(root, "Va_O1_2", {"La": 8, "Zr": 4, "O": 27}, 2, "424")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_nelect(root, cfg)
        assert len(problems) == 1
        assert "296" in problems[0]

    def test_detects_missing_for_charged(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_nelect
        root = tmp_path / "df"
        self._make_dir(root, "Va_O1_2", {"La": 8, "Zr": 4, "O": 27}, 2, None)
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_nelect(root, cfg)
        assert len(problems) == 1
        assert "missing" in problems[0]

    def test_neutral_with_wrong_value_detected(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_nelect
        root = tmp_path / "df"
        # neutral q=0 but INCAR carries the host's wrong NELECT (2025-style bug)
        self._make_dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27}, 0, "424")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_nelect(root, cfg)
        assert len(problems) == 1
        assert "neutral" in problems[0]


class TestVerifyInputs:
    """verify_inputs — input file completeness/consistency checks."""

    def _dir(self, root: Path, name: str, comp: dict[str, int],
             *, potcar_order: list[str] | None = None, no_potcar=False,
             incar: str | None = None, no_incar=False, bad_coords=False,
             no_kpoints=False) -> Path:
        wd = root / name
        wd.mkdir(parents=True)
        els = " ".join(comp.keys())
        counts = " ".join(str(n) for n in comp.values())
        n = sum(comp.values())
        coord_lines = ["0.1 0.2 0.3"] * (n - 1 if bad_coords else n)
        wd.joinpath("POSCAR").write_text(
            "title\n1.0\n10.0 0.0 0.0\n0.0 10.0 0.0\n0.0 0.0 10.0\n"
            f"{els}\n{counts}\nDirect\n" + "\n".join(coord_lines)
        )
        if not no_potcar:
            order = potcar_order or list(comp.keys())
            pot = []
            for el in order:
                pot.append(f"   TITEL  = PAW_PBE {el} 01Jan2000\n"
                           f"   POMASS = 1.0; ZVAL   =    6.000    mass and valenz\n"
                           f"   ENMAX  =  400.0; ENMIN  =  300.0\n")
            wd.joinpath("POTCAR").write_text("".join(pot))
        if not no_incar:
            wd.joinpath("INCAR").write_text(
                incar or "NSW = 50\nIBRION = 2\nEDIFFG = -0.03\nENCUT = 600\n"
            )
        if not no_kpoints:
            wd.joinpath("KPOINTS").write_text("0\nGamma\n1 1 1\n0 0 0\n")
        return wd

    def test_clean_dir_no_problems(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27})
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        assert verify_inputs(root, cfg) == []

    def test_potcar_species_mismatch(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"Ba": 8, "O": 27}, potcar_order=["O", "Ba"])
        cfg = PipelineConfig(formula="BaO", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        assert any("POTCAR species" in p for p in problems)

    def test_missing_incar_tag(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27},
                  incar="NSW = 50\nIBRION = 2\n")  # no EDIFFG
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        assert any("missing EDIFFG" in p for p in problems)

    def test_encut_below_convention(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27},
                  incar="NSW = 50\nIBRION = 2\nEDIFFG = -0.03\nENCUT = 100\n")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        assert any("ENCUT" in p and "WARN" in p for p in problems)

    def test_magnetic_element_no_ispin(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"Fe": 8, "O": 27})
        cfg = PipelineConfig(formula="FeO", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        assert any("ISPIN" in p for p in problems)

    def test_coordinate_count_mismatch(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27}, bad_coords=True)
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        # one atom short → 38 rows vs 39 atoms
        assert any("rows for" in p and "39 atoms" in p for p in problems)

    def test_velocity_rows_are_legal(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        wd = self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27})
        # N zero velocity rows after the coordinates — legal (velocities=0)
        with open(wd / "POSCAR", "a") as f:
            f.write("\n".join(["0.00000000E+00 0.00000000E+00 0.00000000E+00"] * 39) + "\n")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        assert verify_inputs(root, cfg) == []

    def test_too_many_rows_detected(self, tmp_path: Path):
        from vasp_sop.defect.builder import verify_inputs
        root = tmp_path / "df"
        wd = self._dir(root, "Va_O1_0", {"La": 8, "Zr": 4, "O": 27})
        # 3× rows — beyond coords+velocities, must flag
        with open(wd / "POSCAR", "a") as f:
            f.write("\n".join(["0.1 0.2 0.3"] * 39) + "\n")
            f.write("\n".join(["0.1 0.2 0.3"] * 39) + "\n")
        cfg = PipelineConfig(formula="La2Zr2O7", supercell_tool="doped")
        problems = verify_inputs(root, cfg)
        assert any("velocity" in p for p in problems)
