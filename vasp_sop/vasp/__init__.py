"""VASP input/output utilities — shared across pipeline stages."""

from vasp_sop.vasp.io import (
    input_ready,
    prepare_inputs,
    check_complete,
    restart_from_contcar,
    read_incar,
    write_incar,
    patch_incar,
)
from vasp_sop.vasp.convergence import convergence_verdict, is_stalled
from vasp_sop.vasp.auto_heal import apply_correction

__all__ = [
    "input_ready",
    "prepare_inputs",
    "check_complete",
    "restart_from_contcar",
    "read_incar",
    "write_incar",
    "patch_incar",
    "apply_correction",
    "convergence_verdict",
    "is_stalled",
]
