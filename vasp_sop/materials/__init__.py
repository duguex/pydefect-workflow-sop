"""Materials Project query and analysis tools.

Provides functions to download competing phases from Materials Project,
manage local caches, and infer VASP parameters from downloaded structures.
"""

from vasp_sop.materials.mp import (
    # Cache functions
    mp_combo_get,
    mp_combo_put,
    mp_combo_restore,
    mp_phases_get,
    mp_phases_put,
    mp_poscar_get,
    mp_poscar_put,
    # Public API
    fetch_candidate_phases,
    get_intrinsic_elements,
    list_phases,
    list_potcar_variants,
    detect_encut,
    needs_hubbard_u,
)

__all__ = [
    "mp_combo_get",
    "mp_combo_put",
    "mp_combo_restore",
    "mp_phases_get",
    "mp_phases_put",
    "mp_poscar_get",
    "mp_poscar_put",
    "fetch_candidate_phases",
    "get_intrinsic_elements",
    "list_phases",
    "list_potcar_variants",
    "detect_encut",
    "needs_hubbard_u",
]
