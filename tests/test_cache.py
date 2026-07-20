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
        (d / ".schema-v3").touch()
        return d

    # -- tests -------------------------------------------------------------

    def test_combo_get_hit(self):
        """Cache hit when .done + directory exist."""
        d = _mp_cache() / "Ga-N"
        d.mkdir(parents=True)
        (d / ".done").touch()
        (d / ".schema-v3").touch()
        (d / "mol_N2").mkdir()
        (d / "mol_N2" / "POSCAR").write_text("N2\n")

        result = _mp.mp_combo_get(["Ga", "N"])
        assert result == d

    def test_combo_get_miss_without_schema_marker(self):
        """Legacy .done-only caches are invalidated after layout changes."""
        d = _mp_cache() / "Ga-N"
        d.mkdir(parents=True)
        (d / ".done").touch()

        assert _mp.mp_combo_get(["Ga", "N"]) is None

    def test_combo_get_miss_missing_f2_resource(self, tmp_path: Path):
        """Schema-v3 caches lacking mol_F2 are not reusable."""
        self._populate_combo_cache(tmp_path, ["Cs", "F"], ["Cs_mp-1"])

        assert _mp.mp_combo_get(["Cs", "F"]) is None

    def test_combo_get_miss_when_molecule_resource_is_file(self, tmp_path: Path):
        """A file named mol_F2 does not satisfy the resource requirement."""
        self._populate_combo_cache(tmp_path, ["Cs", "F"], ["Cs_mp-1", "mol_F2"])
        cache = _mp_cache() / _mp.mp._combo_key(["Cs", "F"])
        (cache / "mol_F2" / "POSCAR").unlink()
        (cache / "mol_F2").rmdir()
        (cache / "mol_F2").write_text("not a phase directory\n")

        assert _mp.mp_combo_get(["Cs", "F"]) is None

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
        src_root = self._make_src_root(
            tmp_path, ["Ga_mp-123", "Ga₂O₃_mp-456", "mol_O₂"]
        )

        result = _mp.mp_combo_put(["Ga", "O"], src_root)

        assert result.is_dir()
        assert (result / ".done").is_file()
        assert (result / ".schema-v3").is_file()
        for name in ("Ga_mp-123", "Ga₂O₃_mp-456", "mol_O₂"):
            assert (result / name).is_dir()
            assert (result / name / "POSCAR").read_text() == "dummy poscar\n"

    def test_combo_put_replaces_legacy_phase_dirs(self, tmp_path: Path):
        """Writing a new schema removes phase dirs from a legacy cache."""
        legacy = _mp_cache() / _mp.mp._combo_key(["Ga", "Cl"])
        legacy.mkdir(parents=True)
        (legacy / "Cl2_mp-22848").mkdir()
        (legacy / ".done").touch()

        src_root = self._make_src_root(tmp_path, ["mol_Cl2"])
        result = _mp.mp_combo_put(["Ga", "Cl"], src_root)

        assert (result / "mol_Cl2").is_dir()
        assert not (result / "Cl2_mp-22848").exists()
        assert (result / ".done").is_file()
        assert (result / ".schema-v3").is_file()

    def test_combo_put_empty_src(self, tmp_path: Path):
        """mp_combo_put succeeds even when src_root has no subdirs."""
        src_root = tmp_path / "empty_src"
        src_root.mkdir()

        result = _mp.mp_combo_put(["Ga", "X"], src_root)

        assert result.is_dir()
        assert (result / ".done").is_file()
        # No phase dirs were copied
        assert {p.name for p in result.iterdir()} == {".done", ".schema-v3"}

    def test_combo_restore_when_cache_exists(self, tmp_path: Path):
        """mp_combo_restore copies phase dirs and omits cache markers."""
        self._populate_combo_cache(tmp_path, ["N", "Ga"], ["GaN", "mol_N₂"])

        dst_root = tmp_path / "restored"
        dst_root.mkdir()
        _mp.mp_combo_restore(["N", "Ga"], dst_root)

        assert (dst_root / "GaN").is_dir()
        assert (dst_root / "mol_N₂").is_dir()
        assert not (dst_root / ".done").exists()
        assert not (dst_root / ".schema-v3").exists()

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

    def test_combo_restore_removes_stale_managed_phases(self, tmp_path: Path):
        """Restore removes old MP/molecule phases absent from the cache."""
        self._populate_combo_cache(tmp_path, ["N", "Ga"], ["Ga_mp-1", "mol_N2"])
        dst_root = tmp_path / "restored"
        dst_root.mkdir()
        (dst_root / "Old_mp-9").mkdir()
        (dst_root / "mol_legacy").mkdir()
        (dst_root / "keep.txt").write_text("preserve\n")

        _mp.mp_combo_restore(["N", "Ga"], dst_root)

        assert not (dst_root / "Old_mp-9").exists()
        assert not (dst_root / "mol_legacy").exists()
        assert (dst_root / "Ga_mp-1").is_dir()
        assert (dst_root / "mol_N2").is_dir()
        assert (dst_root / "keep.txt").is_file()

    def test_combo_key_dedup_and_sort(self, tmp_path: Path):
        """Duplicate / differently ordered elements → same cache dir."""
        _mp.mp_combo_put(["Ga", "N"], self._make_src_root(tmp_path, ["GaN", "mol_N2"]))

        # Get with reversed dedup order
        result = _mp.mp_combo_get(["N", "Ga", "Ga", "N"])
        assert result is not None
        assert result.name == "Ga-N"


# ══════════════════════════════════════════════════════════════════════════
#  VASP calculation cache  (vasp_results_*)
# ══════════════════════════════════════════════════════════════════════════


class TestVaspResultsCache:
    """Tests for vasp_results_put / cache_lookup / vasp_results_get (v0.3.0)."""

    @staticmethod
    def _minimal_inputs(d: Path, *, formula: str = "Si", energy: float = -9.18) -> None:
        """Write all 7 files required by vasp-cache v0.3.0 identity."""
        # Use Si cubic as simplest valid structure
        poscar = f"""{formula}
1.0
5.43 0 0
0 5.43 0
0 0 5.43
{formula}
2
Direct
0 0 0
0.25 0.25 0.25
"""
        (d / "POSCAR").write_text(poscar)
        (d / "CONTCAR").write_text(poscar)
        (d / "INCAR").write_text("ENCUT = 520\nGGA = PE\n")
        (d / "KPOINTS").write_text("A\n0\nGamma\n4 4 4\n0 0 0\n")
        (d / "POTCAR").write_text(
            f"  PAW_PBE {formula} 05Jan2001\n"
            f"  TITEL  = PAW_PBE {formula} 05Jan2001\n"
            "   4.00000000000000\n"
        )
        (d / "vasprun.xml").write_text(
            "<modeling><calculation><scstep><energy>"
            f'<i name="e_fr_energy">{energy}</i>'
            "</energy></scstep></calculation></modeling>\n"
        )
        (d / "OUTCAR").write_text(
            f" free  energy    TOTEN  =    {energy} eV\n"
            "    reached required accuracy - convergence\n"
            " General timing and accounting\n"
        )

    def test_put_and_cache_lookup(self, tmp_path: Path):
        """put returns identity key; cache_lookup finds the entry."""
        src = tmp_path / "src"
        src.mkdir()
        self._minimal_inputs(src, energy=-9.18)
        key = _cache.vasp_results_put(src)
        assert key is not None
        result = _cache.cache_lookup(src)
        assert result is not None
        assert result["formula"] == "Si"

    def test_put_identity_key_fetchable(self, tmp_path: Path):
        """Stored key can be used with vasp_results_get."""
        src = tmp_path / "src"
        src.mkdir()
        self._minimal_inputs(src, energy=-9.18)
        key = _cache.vasp_results_put(src)
        assert key is not None
        result = _cache.vasp_results_get("Si", key)
        assert result is not None
        assert result["formula"] == "Si"

    def test_put_missing_inputs_returns_none(self, tmp_path: Path):
        """put returns None when identity can't be computed (missing files)."""
        src = tmp_path / "empty"
        src.mkdir()
        (src / "OUTCAR").write_text("free energy TOTEN = -5.0 eV\n")
        key = _cache.vasp_results_put(src)
        assert key is None

    def test_cache_lookup_miss(self, tmp_path: Path):
        """Directory with full inputs but never cached → None."""
        src = tmp_path / "uncached"
        src.mkdir()
        self._minimal_inputs(src, energy=-5.0)
        assert _cache.cache_lookup(src) is None

    def test_cache_lookup_empty_dir(self, tmp_path: Path):
        """Empty directory → None."""
        d = tmp_path / "empty"
        d.mkdir()
        assert _cache.cache_lookup(d) is None

    def test_restore_from_cache(self, tmp_path: Path):
        """restore_from_cache fetches output files back to the dir."""
        src = tmp_path / "src"
        src.mkdir()
        self._minimal_inputs(src, energy=-9.18)
        _cache.vasp_results_put(src)

        work = tmp_path / "work"
        work.mkdir()
        self._minimal_inputs(work, energy=-9.18)
        (work / "OUTCAR").unlink()
        (work / "vasprun.xml").unlink()
        assert _cache.restore_from_cache(work) is True
        assert (work / "OUTCAR").is_file()

    def test_restore_from_cache_missing_inputs(self, tmp_path: Path):
        """restore_from_cache returns False when dir has no identity inputs."""
        d = tmp_path / "bare"
        d.mkdir()
        assert _cache.restore_from_cache(d) is False


class TestCacheAutoDetect:
    """Tests for _detect_calc_info and auto-detect put."""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        _cache.override_cache_root(tmp_path / ".vasp_sop")

    # ── _detect_calc_info ─────────────────────────────────────────────

    def test_detect_mp_naming(self, tmp_path: Path):
        """_mp- in dir name + full inputs → (formula, key, dir_name)."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        _write_si_inputs(d)  # formula from POSCAR beats dir name
        f, ch, tn = _cache._detect_calc_info(d)
        assert f == "Si"
        assert len(ch) == 64  # SHA-256 hex
        assert tn == "GaN_mp-804"

    def test_detect_no_mp_no_poscar(self, tmp_path: Path):
        """No inputs → formula unknown, empty key."""
        d = tmp_path / "some_dir"
        d.mkdir()
        f, ch, tn = _cache._detect_calc_info(d)
        assert f == "unknown"
        assert ch == ""
        assert tn == "some_dir"

    # ── put auto-detect ──────────────────────────────────────────────

    def test_put_auto_detect_sets_source_path(self, tmp_path: Path):
        """put records source_path in cached entry."""
        d = tmp_path / "GaN_mp-804"
        d.mkdir()
        _write_si_inputs(d, energy=-12.0)
        _cache.vasp_results_put(d)
        result = _cache.cache_lookup(d)
        assert result is not None
        assert result["source_path"] == str(d.resolve())


class TestCacheLookup:
    """Tests for cache_lookup — the unified completion check."""

    def test_cache_lookup_hit(self, tmp_path: Path):
        """Full inputs cached → returns result dict."""
        d = tmp_path / "test_system"
        d.mkdir()
        _write_si_inputs(d)
        _cache.vasp_results_put(d)
        result = _cache.cache_lookup(d)
        assert result is not None
        assert result["formula"] == "Si"

    def test_cache_lookup_miss(self, tmp_path: Path):
        """Full inputs but never cached → None."""
        d = tmp_path / "uncached"
        d.mkdir()
        _write_si_inputs(d)
        assert _cache.cache_lookup(d) is None

    def test_cache_lookup_empty_dir(self, tmp_path: Path):
        """Empty dir → None."""
        d = tmp_path / "empty"
        d.mkdir()
        assert _cache.cache_lookup(d) is None


def _write_si_inputs(d: Path, *, energy: float = -9.18) -> None:
    """Shared helper: write all 7 files for a Si calculation."""
    poscar = """Si
1.0
5.43 0 0
0 5.43 0
0 0 5.43
Si
2
Direct
0 0 0
0.25 0.25 0.25
"""
    (d / "POSCAR").write_text(poscar)
    (d / "CONTCAR").write_text(poscar)
    (d / "INCAR").write_text("ENCUT = 520\nGGA = PE\n")
    (d / "KPOINTS").write_text("A\n0\nGamma\n4 4 4\n0 0 0\n")
    (d / "POTCAR").write_text(
        "  PAW_PBE Si 05Jan2001\n  TITEL  = PAW_PBE Si 05Jan2001\n   4.00000000000000\n"
    )
    (d / "vasprun.xml").write_text(
        "<modeling><calculation><scstep><energy>"
        f'<i name="e_fr_energy">{energy}</i>'
        "</energy></scstep></calculation></modeling>\n"
    )
    (d / "OUTCAR").write_text(
        f" free  energy    TOTEN  =    {energy} eV\n"
        " General timing and accounting\n"
    )




class TestRestoreFromKey:
    """restore_from_key: atomic replace via explicit key."""

    def test_restore_to_new_dir(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        _write_si_inputs(tmp_path / "src")
        key = _cache.vasp_results_put(tmp_path / "src")
        assert key
        tgt = tmp_path / "tgt"
        assert _cache.restore_from_key(key, tgt)
        assert (tgt / "OUTCAR").is_file()

    def test_restore_replaces_existing(self, tmp_path: Path):
        (tmp_path / "src").mkdir()
        _write_si_inputs(tmp_path / "src")
        key = _cache.vasp_results_put(tmp_path / "src")
        tgt = tmp_path / "tgt"
        tgt.mkdir()
        (tgt / "OUTCAR").write_text("old\n")
        assert _cache.restore_from_key(key, tgt)
        assert (tgt / "OUTCAR").read_text() != "old\n"

    def test_bad_key_returns_false(self, tmp_path: Path):
        assert not _cache.restore_from_key("no-such-key", tmp_path / "tgt")

    def test_existing_dir_preserved_on_failure(self, tmp_path: Path):
        tgt = tmp_path / "tgt"
        tgt.mkdir()
        (tgt / "OUTCAR").write_text("keep\n")
        assert not _cache.restore_from_key("bad-key", tgt)
        assert (tgt / "OUTCAR").read_text() == "keep\n"

    def test_overwrite_passed_through_to_put(self, tmp_path: Path, monkeypatch):
        overwrite_calls = []
        monkeypatch.setattr("vasp_sop.core.cache._vc_put",
                           lambda src_dir, **kw: (overwrite_calls.append(kw.get("overwrite", False)) or "key"))
        _cache.vasp_results_put(tmp_path, overwrite=True)
        assert overwrite_calls == [True]
        _cache.vasp_results_put(tmp_path)
        assert overwrite_calls == [True, False]
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
