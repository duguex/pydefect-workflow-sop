"""Path roots owned by vasp-sop itself (not the crisp result cache).

JobStore state, MP download caches, and POSCAR mirrors live under
``~/.vasp_sop``.  Calculation **results** and their reuse belong to crisp
(``crisp cache``); vasp-sop never touches that store.
"""

from __future__ import annotations

from pathlib import Path

SOP_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = SOP_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"


def override_cache_root(p: Path | None) -> None:
    """Swap the SOP path constants (for tests)."""
    global SOP_ROOT, MP_CACHE, POSCAR_CACHE
    if p is None:
        SOP_ROOT = Path.home() / ".vasp_sop"
    else:
        SOP_ROOT = Path(p)
    MP_CACHE = SOP_ROOT / "mp_cache"
    POSCAR_CACHE = MP_CACHE / "poscars"
