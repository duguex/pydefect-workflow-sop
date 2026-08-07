"""Tests for the materials MP download cache (mp_combo_*) and path roots."""

from pathlib import Path

import pytest

from vasp_sop.core import paths as _paths
from vasp_sop import materials as _mp


# Access path constants through the module so monkeypatch takes effect.
def _mp_cache() -> Path:
    """Access paths.MP_CACHE (evaluated at call time for monkeypatch)."""
    return _paths.MP_CACHE


def _poscar_cache() -> Path:
    """Access paths.POSCAR_CACHE (evaluated at call time for monkeypatch)."""
    return _paths.POSCAR_CACHE


# ── Fixture ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path) -> None:
    """Redirect all cache paths into tmp_path."""
    from vasp_sop.core.paths import override_cache_root

    override_cache_root(tmp_path / ".vasp_sop")
    # override_cache_root sets paths.MP_CACHE, but mp.py imported the old
    # value at module load time via ``from paths import MP_CACHE``.
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
