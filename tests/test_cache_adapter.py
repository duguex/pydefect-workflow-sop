"""Smoke tests for vasp-sop cache adapter → vasp-cache."""

from pathlib import Path

import pytest

from vasp_sop.core.cache import (
    cache_lookup,
    cache_stats,
    list_cache,
    override_cache_root,
    restore_from_cache,
    vasp_results_get,
    vasp_results_put,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path):
    override_cache_root(tmp_path / ".vasp_sop")
    yield
    override_cache_root(None)


def _complete(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
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
        '<modeling><calculation><scstep><energy>'
        '<i name="e_fr_energy">-5.0</i>'
        '</energy></scstep></calculation></modeling>\n'
    )
    (d / "OUTCAR").write_text(
        " free  energy    TOTEN  =    -5.0 eV\n General timing and accounting\n"
    )
    return d


def test_put_lookup_restore(tmp_path: Path):
    calc = _complete(tmp_path / "calc")
    vasp_results_put(calc)
    hit = cache_lookup(calc)
    assert hit is not None
    assert hit.get("formula") == "Si"
    work = tmp_path / "work"
    _complete(work)
    (work / "OUTCAR").unlink()
    assert restore_from_cache(work) is True
    assert (work / "OUTCAR").is_file()
    assert cache_stats()["entries"] >= 1
    assert len(list_cache(10)) >= 1
