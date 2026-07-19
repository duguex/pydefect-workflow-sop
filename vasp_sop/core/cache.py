"""Adapter from vasp-sop to vasp-cache (SQLite identity cache v0.3.0).

MP download paths remain under ``~/.vasp_sop`` (or test override root).
VASP **results** live in vasp-cache (default ``~/.cache/vasp_cache``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from vasp_cache import (
    fetch as _vc_fetch,
    get_meta as _vc_get_meta,
    has as _vc_has,
    list_entries as _vc_list_entries,
    put as _vc_put,
    query as _vc_query,
    stats as _vc_stats,
    override_cache_root as _vc_override_cache_root,
    IdentityInputError,
)
from vasp_cache.index import identity_for_directory

logger = logging.getLogger(__name__)

# ── Configurable thresholds ───────────────────────────────────────────
# Calculations with max lattice vector > MAX_LATTICE are skipped
# (not cached, not submitted).  Set to None to disable.
MAX_LATTICE: float | None = 25.0

# ── SOP-local paths (NOT the results cache) ────────────────────────────
SOP_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = SOP_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"
CALC_CACHE: Path = SOP_ROOT / "calc_cache"


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
    global SOP_ROOT, MP_CACHE, POSCAR_CACHE, CALC_CACHE
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


def _content_hash(src_dir: Path) -> str:
    """Return identity key for *src_dir*. Raises IdentityInputError."""
    return identity_for_directory(Path(src_dir)).key


def _detect_calc_info(src_dir: Path) -> tuple[str, str, str]:
    """Return (formula, identity_key, dir_name) for *src_dir*."""
    p = Path(src_dir)
    try:
        ident = identity_for_directory(p)
        return ident.formula, ident.key, p.name
    except IdentityInputError:
        pass
    formula = "unknown"
    for cand in (p / "CONTCAR", p / "POSCAR"):
        if cand.is_file():
            try:
                from pymatgen.core.structure import Structure
                formula = Structure.from_file(str(cand)).composition.reduced_formula
                break
            except Exception:
                continue
    return formula, "", p.name


def vasp_results_put(
    src_dir: Path,
    formula: str | None = None,
    content_hash: str | None = None,
    task_name: str | None = None,
    *,
    cache_root: Path | None = None,
) -> str | None:
    """Store VASP results from *src_dir* in vasp-cache.

    Legacy *formula*, *content_hash*, and *task_name* are ignored;
    vasp-cache v0.3.0 auto-detects identity from directory content.

    Returns identity key on success, None if identity could not be
    computed (missing required input files).
    """
    return _vc_put(src_dir, root=cache_root)


def vasp_results_get(
    formula: str, key: str, *, cache_root: Path | None = None
) -> dict[str, Any] | None:
    """Return cached metadata for (formula, identity_key)."""
    return _vc_get_meta(formula=formula, key=key, root=cache_root)


def cache_lookup(
    src_dir: Path, *, cache_root: Path | None = None
) -> dict[str, Any] | None:
    """Return cached result for *src_dir*, or None."""
    try:
        if not _vc_has(src_dir, root=cache_root):
            return None
        return _vc_get_meta(input_dir=src_dir, root=cache_root)
    except IdentityInputError:
        return None


def restore_from_cache(
    src_dir: Path, *, cache_root: Path | None = None
) -> bool:
    """Restore OUTCAR/CONTCAR/vasprun.xml from cache to *src_dir*."""
    try:
        ident = identity_for_directory(Path(src_dir))
    except IdentityInputError:
        return False
    if not _vc_has(src_dir, root=cache_root):
        return False
    # fetch() requires a non-existing target dir: restore via uncreated staging
    tgt = Path(src_dir)
    import uuid, shutil
    staging = tgt.parent / f".vc-restore-{uuid.uuid4().hex[:12]}"
    try:
        if not _vc_fetch(ident.key, staging, root=cache_root):
            return False
        for name in ("OUTCAR", "CONTCAR", "vasprun.xml"):
            src = staging / name
            if src.is_file():
                shutil.copy2(src, tgt / name)
        return True
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def restore_from_key(
    key: str, target_dir: Path, *, cache_root: Path | None = None
) -> bool:
    """Restore calculation to *target_dir* using explicit *key*.

    Fetches to a staging directory, then atomically replaces *target_dir*
    via rename. Safe against fetch failure — leaves original intact.
    """
    tgt = Path(target_dir)
    import uuid, shutil
    staging = tgt.parent / f".vc-restore-{uuid.uuid4().hex[:12]}"
    backup = tgt.parent / f".vc-backup-{uuid.uuid4().hex[:12]}"
    try:
        if not _vc_fetch(key, staging, root=cache_root):
            return False
        if tgt.exists():
            tgt.rename(backup)
        staging.rename(tgt)
        return True
    except Exception:
        # Rollback: restore backup if staging was renamed
        if staging.exists() and tgt.exists():
            pass  # rename succeeded, no rollback needed
        if backup.exists() and not tgt.exists():
            backup.rename(tgt)
        return False
    finally:
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)

def query(
    formula: str | None = None,
    functional: str | None = None,
    calc_type: str | None = None,
    tags_contains: str | None = None,
    bandgap_min: float | None = None,
    lattice_max: float | None = None,
    converged_only: bool = True,
    limit: int = 100,
    *,
    cache_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Query vasp-cache by formula.

    vasp-cache v0.3.0 supports ``formula`` and ``limit`` only.
    Raises ``ValueError`` for unsupported filter parameters.
    """
    unsupported = []
    if functional is not None:
        unsupported.append("functional")
    if calc_type is not None:
        unsupported.append("calc_type")
    if tags_contains is not None:
        unsupported.append("tags_contains")
    if bandgap_min is not None:
        unsupported.append("bandgap_min")
    if lattice_max is not None:
        unsupported.append("lattice_max")
    if unsupported:
        raise ValueError(
            "query filter(s) not supported by vasp-cache v0.3.0: "
            + ", ".join(unsupported)
        )
    return _vc_query(formula=formula, limit=limit, root=cache_root)


def list_cache(
    limit: int = 50, *, cache_root: Path | None = None
) -> list[dict[str, Any]]:
    return _vc_list_entries(limit=limit, root=cache_root)


def cache_stats(*, cache_root: Path | None = None) -> dict[str, Any]:
    return _vc_stats(root=cache_root)


def migrate_from_sqlite() -> int:
    """Legacy no-op: SQLite results cache replaced by vasp-cache v0.3.0."""
    logger.warning(
        "migrate_from_sqlite is a no-op; results cache is vasp-cache v0.3.0"
    )
    return 0