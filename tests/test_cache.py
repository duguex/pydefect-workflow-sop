"""Tests for vasp_sop.core.cache — cache layer for MP / VASP / CPD results."""

from pathlib import Path

import pytest

from vasp_sop.core import cache as _cache
from vasp_sop import materials as _mp

# Access cache-path constants through the module so monkeypatch takes effect.
def _mp_cache() -> Path:
    """Access cache.MP_CACHE (evaluated at call time for monkeypatch)."""
    return _cache.MP_CACHE


def _poscar_cache() -> Path:
    """Access cache.POSCAR_CACHE (evaluated at call time for monkeypatch)."""
    return _cache.POSCAR_CACHE


def _calc_cache() -> Path:
    """Access cache.CALC_CACHE (evaluated at call time for monkeypatch)."""
    return _cache.CALC_CACHE


# ── Fixture ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path) -> None:
    """Redirect all cache paths into tmp_path."""
    from vasp_sop.core.cache import override_cache_root
    override_cache_root(tmp_path / ".vasp_sop")
    # override_cache_root sets cache.MP_CACHE, but mp.py imported the old
    # value at module load time via ``from cache import MP_CACHE``.
    # Directly patch both modules so all code paths see the new path.
    import vasp_sop.materials.mp as _mp_mod
    _mp_mod.MP_CACHE = tmp_path / ".vasp_sop" / "mp_cache"
    _mp_mod.POSCAR_CACHE = _mp_mod.MP_CACHE / "poscars"
# ══════════════════════════════════════════════════════════════════════════
#  Combined MP download cache  (mp_combo_*)
# ══════════════════════════════════════════════════════════════════════════


class TestMpComboCache:
    """Tests for mp_combo_get / mp_combo_put / mp_combo_restore."""

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _make_src_root(tmp_path: Path, names: list[str]) -> Path:
        """Create source root with phase subdirs (each with a marker)."""
        src_root = tmp_path / "src_phases"
        src_root.mkdir()
        for name in names:
            d = src_root / name
            d.mkdir()
            (d / "POSCAR").write_text("dummy poscar\n")
        return src_root

    @staticmethod
    def _populate_combo_cache(
        tmp_path: Path, elements: list[str], phase_names: list[str]
    ) -> Path:
        """Create a combo cache entry with phase dirs and .done marker."""
        key = _mp.mp._combo_key(elements)
        d = _mp_cache() / key
        d.mkdir(parents=True)
        for name in phase_names:
            p = d / name
            p.mkdir()
            (p / "POSCAR").write_text("dummy poscar\n")
        (d / ".done").touch()
        return d

    # -- tests -------------------------------------------------------------

    def test_combo_get_hit(self):
        """Cache hit when .done + directory exist."""
        d = _mp_cache() / "Ga-N"
        d.mkdir(parents=True)
        (d / ".done").touch()

        result = _mp.mp_combo_get(["Ga", "N"])
        assert result == d

    def test_combo_get_miss_no_dir(self):
        """Returns None when directory does not exist."""
        assert _mp.mp_combo_get(["Ga", "N"]) is None

    def test_combo_get_miss_no_dotdone(self):
        """Returns None when directory exists but .done is missing."""
        d = _mp_cache() / "Ga-N"
        d.mkdir(parents=True)

        assert _mp.mp_combo_get(["Ga", "N"]) is None

    def test_combo_put_copies_phase_dirs(self, tmp_path: Path):
        """mp_combo_put copies all phase subdirs from src_root."""
        src_root = self._make_src_root(tmp_path, ["Ga_mp-123", "Ga₂O₃_mp-456", "mol_O₂"])

        result = _mp.mp_combo_put(["Ga", "O"], src_root)

        assert result.is_dir()
        assert (result / ".done").is_file()
        for name in ("Ga_mp-123", "Ga₂O₃_mp-456", "mol_O₂"):
            assert (result / name).is_dir()
            assert (result / name / "POSCAR").read_text() == "dummy poscar\n"

    def test_combo_put_empty_src(self, tmp_path: Path):
        """mp_combo_put succeeds even when src_root has no subdirs."""
        src_root = tmp_path / "empty_src"
        src_root.mkdir()

        result = _mp.mp_combo_put(["Ga", "N"], src_root)

        assert result.is_dir()
        assert (result / ".done").is_file()
        # No phase dirs were copied
        assert list(result.iterdir()) == [result / ".done"]

    def test_combo_restore_when_cache_exists(self, tmp_path: Path):
        """mp_combo_restore copies phase dirs to dst_root, omitting .done."""
        self._populate_combo_cache(tmp_path, ["N", "Ga"],
                                   ["GaN", "mol_N₂"])

        dst_root = tmp_path / "restored"
        dst_root.mkdir()
        _mp.mp_combo_restore(["N", "Ga"], dst_root)

        assert (dst_root / "GaN").is_dir()
        assert (dst_root / "mol_N₂").is_dir()
        assert not (dst_root / ".done").exists()

    def test_combo_restore_when_cache_missing(self, tmp_path: Path):
        """mp_combo_restore does nothing when cache has no entry."""
        dst_root = tmp_path / "restored"
        dst_root.mkdir()
        _mp.mp_combo_restore(["X", "Y"], dst_root)

        assert list(dst_root.iterdir()) == []  # nothing created

    def test_combo_restore_overwrites_existing_dirs(self, tmp_path: Path):
        """mp_combo_restore overwrites existing dirs in dst_root."""
        self._populate_combo_cache(tmp_path, ["Ga", "N"], ["GaN", "mol_N₂"])
        dst_root = tmp_path / "restored"
        dst_root.mkdir()
        # Pre-create a conflicting dir with different content
        (dst_root / "GaN").mkdir()
        (dst_root / "GaN" / "ORIGINAL").write_text("keep me\n")
        _mp.mp_combo_restore(["Ga", "N"], dst_root)
        # Existing dir was overwritten (cache marker present, original gone)
        assert (dst_root / "GaN" / "POSCAR").is_file()
        assert not (dst_root / "GaN" / "ORIGINAL").exists()
        # New dir was restored
        assert (dst_root / "mol_N₂").is_dir()

    def test_combo_key_dedup_and_sort(self, tmp_path: Path):
        """Duplicate / differently ordered elements → same cache dir."""
        _mp.mp_combo_put(["Ga", "N"],
                            self._make_src_root(tmp_path, ["GaN"]))

        # Get with reversed dedup order
        result = _mp.mp_combo_get(["N", "Ga", "Ga", "N"])
        assert result is not None
        assert result.name == "Ga-N"


# ══════════════════════════════════════════════════════════════════════════
#  VASP calculation cache  (vasp_results_*)
# ══════════════════════════════════════════════════════════════════════════


class TestVaspResultsCache:
    """Tests for vasp_results_get / vasp_results_put (DB-only storage)."""

    def _write_minimal_outcar(self, d: Path, energy: str = "-9.18") -> None:
        """Write OUTCAR that our regex parser can handle."""
        (d / "OUTCAR").write_text(
            f" free  energy    TOTEN  =    {energy} eV\n"
            "    reached required accuracy - convergence\n"
            " General timing and accounting\n"
        )

    def test_vasp_results_get_hit(self, tmp_path: Path):
        """Returns dict with total_energy when OUTCAR is cached."""
        src = tmp_path / "src"
        src.mkdir()
        self._write_minimal_outcar(src)
        (src / "CONTCAR").write_text(
            "H\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n1\nDirect\n0 0 0\n"
        )
        _cache.vasp_results_put(src, "GaN", "804")
        result = _cache.vasp_results_get("GaN", "804")
        assert result is not None
        assert result["total_energy"] == -9.18
        assert result["converged"] == 1
        assert result["n_sites"] == 1

    def test_vasp_results_get_miss_no_dir(self):
        """Returns None when not cached."""
        assert _cache.vasp_results_get("GaN", "804") is None

    def test_vasp_results_get_miss_no_outcar(self, tmp_path: Path):
        """Returns None when no OUTCAR in src_dir (nothing cached)."""
        src = tmp_path / "empty"
        src.mkdir()
        _cache.vasp_results_put(src, "GaN", "804")
        assert _cache.vasp_results_get("GaN", "804") is None

    def test_vasp_results_put_stores_parsed_data(self, tmp_path: Path):
        """vasp_results_put stores parsed data in SQLite, not files."""
        src = tmp_path / "src"
        src.mkdir()
        self._write_minimal_outcar(src)
        (src / "INCAR").write_text("SYSTEM = test\nENCUT = 520\n")
        (src / "CONTCAR").write_text(
            "H\n1.0\n10 0 0\n0 10 0\n0 0 10\nH\n1\nDirect\n0 0 0\n"
        )
        _cache.vasp_results_put(src, "GaN", "804")

        result = _cache.vasp_results_get("GaN", "804")
        assert result is not None
        assert result["total_energy"] == -9.18
        assert result["converged"] == 1
        assert result["n_sites"] == 1
        assert result["incar_json"] is not None

    def test_vasp_results_put_missing_src_files(self, tmp_path: Path):
        """Partial files still produce an entry (energy only)."""
        src = tmp_path / "src"
        src.mkdir()
        self._write_minimal_outcar(src)
        _cache.vasp_results_put(src, "GaN", "804")

        result = _cache.vasp_results_get("GaN", "804")
        assert result is not None
        assert result["total_energy"] == -9.18
        assert result["converged"] == 1
        assert result["incar_json"] is None
        assert result["structure_json"] is None

    def test_vasp_results_put_skips_no_outcar(self, tmp_path: Path):
        """No OUTCAR produces no cache entry."""
        src = tmp_path / "empty"
        src.mkdir()
        _cache.vasp_results_put(src, "GaN", "804")
        assert _cache.vasp_results_get("GaN", "804") is None


class TestCacheAutoDetect:
    """Tests for auto-detection and tag extraction."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        _cache.override_cache_root(tmp_path / ".vasp_sop")

    # ── _detect_calc_info ─────────────────────────────────────────────

    def test_detect_mp_naming(self, tmp_path: Path):
        """_mp- in dir name → (formula, mpid)."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        f, ch, tn = _cache._detect_calc_info(d)
        assert f == "GaN"
        assert ch != ""
        assert tn == "GaN_mp-804"

    def test_detect_no_mp_with_poscar(self, tmp_path: Path):
        """No _mp- but POSCAR present → formula from structure."""
        d = tmp_path / "Va_Na_0"
        d.mkdir()
        (d / "POSCAR").write_text(
            "NaCl\n1.0\n5.64 0 0\n0 5.64 0\n0 0 5.64\nNa Cl\n1 1\nDirect\n"
            "0 0 0\n0.5 0.5 0.5\n"
        )
        f, ch, tn = _cache._detect_calc_info(d)
        assert f == "NaCl"
        assert ch != ""
        assert tn == "Va_Na_0"

    def test_detect_no_mp_no_poscar(self, tmp_path: Path):
        """No _mp- and no POSCAR → formula unknown."""
        d = tmp_path / "some_dir"
        d.mkdir()
        f, ch, tn = _cache._detect_calc_info(d)
        assert f == "unknown"
        assert ch != ""
        assert tn == "some_dir"

    # ── _incar_fingerprint ───────────────────────────────────────────

    def test_incar_fingerprint_no_incar(self, tmp_path: Path):
        """No INCAR file → 'default'."""
        assert _cache._incar_fingerprint(tmp_path) == "default"

    def test_incar_fingerprint_matches_keys(self, tmp_path: Path):
        """INCAR with known tags → compact string."""
        (tmp_path / "INCAR").write_text("ENCUT = 520\nISIF = 3\nISPIN = 2\n")
        fp = _cache._incar_fingerprint(tmp_path)
        assert "ENCUT=520.0" in fp
        assert "ISIF=3" in fp
        assert "|" in fp

    def test_incar_fingerprint_irrelevant_tags_ignored(self, tmp_path: Path):
        """Tags not in fingerprint keys are excluded."""
        (tmp_path / "INCAR").write_text("ENCUT = 400\nSYSTEM = test\n")
        fp = _cache._incar_fingerprint(tmp_path)
        assert "ENCUT=400.0" in fp
        assert "SYSTEM" not in fp

    # ── _extract_tags ────────────────────────────────────────────────

    def test_extract_tags_no_input(self):
        """No inputs → empty string."""
        assert _cache._extract_tags() == ""

    def test_extract_tags_structure_only(self):
        """Only structure → composition tag."""
        from pymatgen.core import Lattice, Structure
        s = Structure(Lattice.cubic(5.64), ["Na", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]])
        tags = _cache._extract_tags(structure=s)
        assert "Na1Cl1" in tags

    def test_extract_tags_gamma_kpoints(self):
        """Gamma-only KPOINTS → 'gamma' tag."""
        from pymatgen.io.vasp import Kpoints
        k = Kpoints(kpts=[[1, 1, 1]], style=Kpoints.supported_modes.Gamma)
        tags = _cache._extract_tags(kpoints=k)
        assert "gamma" in tags

    def test_extract_tags_grid_kpoints(self):
        """Regular mesh KPOINTS → grid string tag."""
        from pymatgen.io.vasp import Kpoints
        k = Kpoints(kpts=[[4, 4, 4]], style=Kpoints.supported_modes.Monkhorst)
        tags = _cache._extract_tags(kpoints=k)
        assert "444" in tags

    def test_extract_tags_line_mode_kpoints(self):
        """Line_mode KPOINTS → 'band-structure' tag."""
        from pymatgen.io.vasp import Kpoints
        k = Kpoints(kpts=[[0, 0, 0], [0.5, 0.5, 0.5]],
                     style=Kpoints.supported_modes.Line_mode)
        tags = _cache._extract_tags(kpoints=k)
        assert "band-structure" in tags

    def test_extract_tags_space_group(self):
        """Space group from sga NOT included in simplified tags."""
        from pymatgen.core import Lattice, Structure
        from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
        s = Structure(Lattice.cubic(5.43), ["Si"]*2, [[0, 0, 0], [0.25, 0.25, 0.25]])
        sga = SpacegroupAnalyzer(s)
        sg = sga.get_space_group_symbol()
        tags = _cache._extract_tags(structure=s, sga=sga)
        # Simplified tags use composition string, not space group
        assert sg not in tags
        assert "Si2" in tags

    def test_extract_tags_incar_gga_and_spin(self):
        """PBE + spin → tags contain PBE and spin."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"GGA": "PE", "ISPIN": 2}))
        assert "PBE" in tags
        assert "spin" in tags

    def test_extract_tags_incar_hybrid_hse(self):
        """HSE → hybrid and specific type."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"LHFCALC": True, "HFSCREEN": 0.2}))
        assert "hybrid" in tags
        assert "HSE" in tags

    def test_extract_tags_incar_ldau(self):
        """LDAU → DFT+U tag."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"LDAU": True}))
        assert "DFT+U" in tags

    # ── vasp_results_put auto-detect ─────────────────────────────────

    def test_put_auto_detect_mp_dir(self, tmp_path: Path):
        """vasp_results_put with auto-detect from _mp- naming."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d)
        f, ch, _ = _cache._detect_calc_info(d)
        r = _cache.vasp_results_get(f, ch)
        assert r is not None
        assert r["total_energy"] == -12.0
        assert r["converged"] == 1

    def test_put_auto_detect_defect_dir(self, tmp_path: Path):
        """vasp_results_put with auto-detect from POSCAR."""
        d = tmp_path / "Va_Na_0"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -5.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "POSCAR").write_text(
            "NaCl\n1.0\n5.64 0 0\n0 5.64 0\n0 0 5.64\nNa Cl\n1 1\nDirect\n"
            "0 0 0\n0.5 0.5 0.5\n"
        )
        (d / "INCAR").write_text("ENCUT = 400\n")
        _cache.vasp_results_put(d)
        f, ch, _ = _cache._detect_calc_info(d)
        r = _cache.vasp_results_get(f, ch)
        assert r is not None
        assert r["total_energy"] == -5.0

    def test_put_sets_source_dir(self, tmp_path: Path):
        """Cached entry contains source_dir pointing to src dir."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d)
        f, ch, _ = _cache._detect_calc_info(d)
        r = _cache.vasp_results_get(f, ch)
        assert r is not None
        assert r["source_dir"] == str(d.resolve())

    # ── _extract_tags — more INCAR variants ─────────────────────────

    def test_extract_tags_pbesol(self):
        """PBEsol functional tag."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"GGA": "PS"}))
        assert "PBEsol" in tags

    def test_extract_tags_scan(self):
        """SCAN metaGGA tag."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"METAGGA": "SCAN"}))
        assert "SCAN" in tags

    def test_extract_tags_phonon(self):
        """Phonon calculation tag."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"IBRION": 6, "NFREE": 2}))
        assert "phonon" in tags

    def test_extract_tags_dielectric(self):
        """Dielectric property tag."""
        from pymatgen.io.vasp import Incar
        tags = _cache._extract_tags(incar=Incar({"LEPSILON": True, "LOPTICS": True}))
        assert "dielectric" in tags
        assert "optics" in tags

    def test_extract_tags_encut_tiers(self):
        """High and low ENCUT tier tags."""
        from pymatgen.io.vasp import Incar
        low = _cache._extract_tags(incar=Incar({"ENCUT": 250}))
        high = _cache._extract_tags(incar=Incar({"ENCUT": 700}))
        assert "low-encut" in low
        assert "high-encut" in high

    def test_extract_tags_combined(self):
        """Full realistic scenario: structure + KPOINTS + INCAR."""
        from pymatgen.io.vasp import Incar, Kpoints
        from pymatgen.core import Lattice, Structure
        s = Structure(Lattice.cubic(5.64), ["Na", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]])
        k = Kpoints(kpts=[[4, 4, 4]], style=Kpoints.supported_modes.Monkhorst)
        i = Incar({"GGA": "PE", "ISPIN": 2, "LDAU": True, "ENCUT": 400})
        tags = _cache._extract_tags(incar=i, kpoints=k, structure=s)
        assert "Na1Cl1" in tags
        assert "444" in tags
        assert "PBE" in tags
        assert "spin" in tags
        assert "DFT+U" in tags

    # ── partial auto-detect ──────────────────────────────────────────

    def test_put_explicit_formula_auto_task_id(self, tmp_path: Path):
        """Only formula given, task_id auto-detected."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d, formula="AlN")
        _, ch, _ = _cache._detect_calc_info(d)
        r = _cache.vasp_results_get("AlN", ch)
        assert r is not None
        assert r["total_energy"] == -12.0

    def test_put_explicit_task_id_auto_formula(self, tmp_path: Path):
        """Only task_id given, formula auto-detected from POSCAR."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d, task_name="custom_id")
        f, ch, tn = _cache._detect_calc_info(d)
        r = _cache.vasp_results_get("GaN", ch)
        assert r is not None


class TestCacheLookup:
    """Tests for cache_lookup — the unified completion check."""

    def test_cache_lookup_hit(self, tmp_path: Path):
        """POSCAR+OUTCAR cached → returns result dict with total_energy."""
        d = tmp_path / "test_system"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -12.34 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        (d / "INCAR").write_text("ENCUT = 520\nISIF = 3\n")
        (d / "KPOINTS").write_text("K-Points\n0\nGamma\n4 4 4\n")
        _cache.vasp_results_put(d)
        result = _cache.cache_lookup(d)
        assert result is not None
        assert result["total_energy"] == -12.34
        assert result["converged"] == 1

    def test_cache_lookup_miss(self, tmp_path: Path):
        """Directory with VASP files but never cached → None."""
        d = tmp_path / "uncached"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -5.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        # No vasp_results_put called — cache is empty
        assert _cache.cache_lookup(d) is None

    def test_cache_lookup_empty_dir(self, tmp_path: Path):
        """Empty directory with no VASP files and no cache → None."""
        d = tmp_path / "empty"
        d.mkdir()
        assert _cache.cache_lookup(d) is None

    def test_cache_lookup_mp_naming(self, tmp_path: Path):
        """_mp- directory correctly resolves via content_hash."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -15.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d)
        result = _cache.cache_lookup(d)
        assert result is not None
        assert result["total_energy"] == -15.0

    def test_cache_lookup_after_delete(self, tmp_path: Path):
        """Lookup returns None after entry is deleted."""
        d = tmp_path / "deleteme"
        d.mkdir()
        (d / "OUTCAR").write_text(
            " free  energy    TOTEN  =    -8.0 eV\n"
            " General timing and accounting\n"
        )
        (d / "CONTCAR").write_text(
            "GaN\n1.0\n3.19 0 0\n0 3.19 0\n0 0 5.19\nGa N\n1 1\nDirect\n"
            "0 0 0\n0.333 0.667 0.5\n"
        )
        _cache.vasp_results_put(d)
        assert _cache.cache_lookup(d) is not None
        f, ch, _ = _cache._detect_calc_info(d)
        _cache.vasp_results_delete(f, ch)
        assert _cache.cache_lookup(d) is None


#  MP phase list cache  (dead code but test for completeness)
# ══════════════════════════════════════════════════════════════════════════


class TestMpPhasesCache:
    """Tests for mp_phases_get / mp_phases_put."""

    def test_phases_roundtrip(self):
        """Put then get returns the same phase data."""
        phases = [{"mpid": "mp-149", "formula": "Si"}]
        _mp.mp_phases_put("Si", phases)

        result = _mp.mp_phases_get("Si")
        assert result == phases

    def test_phases_get_miss(self):
        """Returns None when no cache file exists."""
        assert _mp.mp_phases_get("Si") is None

    def test_phases_get_corrupt_json(self):
        """Returns None (no exception) when the cached file is garbage."""
        f = _mp_cache() / "Si.json"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"\xff\xfe\x00\x01 garbage bytes")

        result = _mp.mp_phases_get("Si")
        assert result is None


class TestMpPoscarCache:
    """Tests for mp_poscar_get / mp_poscar_put."""

    def test_poscar_get_hit(self):
        """Returns cache path when POSCAR exists."""
        d = _poscar_cache() / "mp-149"
        d.mkdir(parents=True)
        (d / "POSCAR").write_text("poscar data\n")

        result = _mp.mp_poscar_get("mp-149")
        assert result == d / "POSCAR"

    def test_poscar_get_miss_no_dir(self):
        """Returns None when directory does not exist."""
        assert _mp.mp_poscar_get("mp-149") is None

    def test_poscar_get_miss_no_poscar(self):
        """Returns None when directory exists but POSCAR is missing."""
        d = _poscar_cache() / "mp-149"
        d.mkdir(parents=True)

        assert _mp.mp_poscar_get("mp-149") is None

    def test_poscar_put_copies_both(self, tmp_path: Path):
        """mp_poscar_put copies both POSCAR and POTCAR into cache."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "POSCAR").write_text("poscar\n")
        (src / "POTCAR").write_text("potcar\n")

        _mp.mp_poscar_put("mp-149", src)

        d = _poscar_cache() / "mp-149"
        assert (d / "POSCAR").read_text() == "poscar\n"
        assert (d / "POTCAR").read_text() == "potcar\n"

    def test_poscar_put_missing_potcar(self, tmp_path: Path):
        """Only POSCAR is copied when POTCAR is missing; no error."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "POSCAR").write_text("poscar\n")

        _mp.mp_poscar_put("mp-149", src)

        d = _poscar_cache() / "mp-149"
        assert (d / "POSCAR").read_text() == "poscar\n"
        assert not (d / "POTCAR").exists()
