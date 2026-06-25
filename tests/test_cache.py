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




# ══════════════════════════════════════════════════════════════════════════
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
