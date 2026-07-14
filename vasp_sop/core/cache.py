"""Adapter from vasp-sop to vasp-cache (signac results cache).

MP download paths remain under ``~/.vasp_sop`` (or test override root).
VASP **results** live in vasp-cache (default ``~/.vasp_cache``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vasp_cache import (
    content_hash as _vc_content_hash,
    fetch as _vc_fetch,
    get_meta as _vc_get_meta,
    has as _vc_has,
    list_entries as _vc_list_entries,
    put as _vc_put,
    query as _vc_query,
    stats as _vc_stats,
)
from vasp_cache.fingerprint import _incar_fingerprint  # noqa: F401
from vasp_cache.parse import MAX_LATTICE  # re-export for jobs.py
from vasp_cache.parse import _extract_tags  # noqa: F401
from vasp_cache.paths import cache_root as _vc_cache_root
from vasp_cache.paths import override_cache_root as _vc_override_cache_root

logger = logging.getLogger(__name__)

# ── SOP-local paths (NOT the results cache) ─────────────────────────────
SOP_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = SOP_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"
CALC_CACHE: Path = SOP_ROOT / "calc_cache"

# Results cache root (vasp-cache)
CACHE_ROOT: Path = _vc_cache_root()


def lattice_too_large(src_dir: Path) -> bool:
    """True if max lattice vector exceeds MAX_LATTICE."""
    if MAX_LATTICE is None:
        return False
    try:
        from pymatgen.core.structure import Structure

        for cand in (Path(src_dir) / "CONTCAR", Path(src_dir) / "POSCAR"):
            if cand.is_file():
                a, b, c = Structure.from_file(str(cand)).lattice.abc
                return max(a, b, c) > MAX_LATTICE
    except Exception:
        return False
    return False


def override_cache_root(p: Path | None) -> None:
    """Swap results cache root and SOP path constants (for tests)."""
    global CACHE_ROOT, SOP_ROOT, MP_CACHE, POSCAR_CACHE, CALC_CACHE
    if p is None:
        SOP_ROOT = Path.home() / ".vasp_sop"
        _vc_override_cache_root(None)
    else:
        root = Path(p)
        SOP_ROOT = root
        _vc_override_cache_root(root / "vasp_cache_results")
    MP_CACHE = SOP_ROOT / "mp_cache"
    POSCAR_CACHE = MP_CACHE / "poscars"
    CALC_CACHE = SOP_ROOT / "calc_cache"
    CACHE_ROOT = _vc_cache_root()


def _content_hash(src_dir: Path) -> str:
    return _vc_content_hash(Path(src_dir))


def _detect_calc_info(src_dir: Path) -> tuple[str, str, str]:
    """Return (formula, content_hash, task_name) for *src_dir*."""
    p = Path(src_dir)
    name = p.name
    if "_mp-" in name:
        formula = name.split("_mp-", 1)[0]
        task_name = name
    else:
        task_name = name
        formula = "unknown"
        for cand in (p / "CONTCAR", p / "POSCAR"):
            if cand.is_file():
                try:
                    from pymatgen.core.structure import Structure

                    formula = Structure.from_file(str(cand)).composition.reduced_formula
                    break
                except Exception:
                    continue
    return formula, _content_hash(p), task_name


def vasp_results_put(
    src_dir: Path,
    formula: str | None = None,
    content_hash: str | None = None,
    task_name: str | None = None,
) -> None:
    """Store VASP results from *src_dir* in vasp-cache.

    Legacy callers pass ``(dir, formula, key)`` where *key* was content_hash
    or mpid-like token; we treat a lone third arg as *task_name* for lookup.
    """
    if task_name is None and content_hash is not None:
        task_name = content_hash
    _vc_put(src_dir, formula=formula, task_name=task_name)

def vasp_results_get(formula: str, key: str) -> dict[str, Any] | None:
    """Return cached result for (formula, key)."""
    row = _vc_get_meta(formula=formula, key=key)
    if row is None:
        return None
    # Legacy JSONStore fields expected by older tests/callers
    if "converged" in row:
        row["converged"] = int(bool(row["converged"]))
    for k in ("incar_json", "structure_json", "outcar_json", "vasprun_json", "kpoints_json"):
        row.setdefault(k, None)
    return row


def cache_lookup(src_dir: Path) -> dict[str, Any] | None:
    """Return cached result for *src_dir*, or None."""
    if not _vc_has(src_dir):
        return None
    return _vc_get_meta(src_dir)


def restore_from_cache(src_dir: Path) -> bool:
    """Restore OUTCAR/CONTCAR/vasprun.xml from cache to *src_dir*."""
    return _vc_fetch(src_dir)


def query(
    formula: str | None = None,
    functional: str | None = None,
    calc_type: str | None = None,
    tags_contains: str | None = None,
    bandgap_min: float | None = None,
    lattice_max: float | None = None,
    converged_only: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return _vc_query(
        formula=formula,
        functional=functional,
        calc_type=calc_type,
        tags_contains=tags_contains,
        bandgap_min=bandgap_min,
        lattice_max=lattice_max,
        converged_only=converged_only,
        limit=limit,
    )


def list_cache(limit: int = 50) -> list[dict[str, Any]]:
    return _vc_list_entries(limit=limit)


def cache_stats() -> dict[str, Any]:
    return _vc_stats()


def migrate_from_sqlite() -> int:
    """Legacy no-op: JSONStore/SQLite results cache abandoned."""
    logger.warning("migrate_from_sqlite is a no-op; results cache is vasp-cache/signac")
    return 0
