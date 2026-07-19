"""VASP error auto-healing — staged corrections for diagnosed failures.

Implements a correction registry that maps error types (from
:func:`vasp_sop.vasp.errors.diagnose_failure`) to staged INCAR
modifications.  Corrections escalate with repeated failures: least
invasive first, more aggressive on subsequent attempts.

Used by :mod:`vasp_sop.defect.compute` in the CONTCAR restart loop.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from vasp_sop.vasp.io import patch_incar, read_incar, restart_from_contcar

logger = logging.getLogger(__name__)

# Type alias: correction function takes (work_dir, attempt_number) -> None
CorrectionFn = callable


# ══════════════════════════════════════════════════════════════════════════
# Correction functions (staged escalation)
# ══════════════════════════════════════════════════════════════════════════


def _correct_positive_energy(work_dir: Path, attempt: int) -> None:
    """Positive total energy — bad smearing or initial structure.

    Stage 1: increase SIGMA (broader smearing).
    Stage 2: switch to Gaussian smearing (ISMEAR=0) + larger SIGMA.
    Stage 3+: reduce ENCUT slightly and use ISMEAR=0, SIGMA=0.2.
    """
    if attempt <= 1:
        patch_incar(work_dir, ISMEAR=0, SIGMA=0.1)
    elif attempt == 2:
        patch_incar(work_dir, ISMEAR=0, SIGMA=0.2)
    else:
        patch_incar(work_dir, ISMEAR=0, SIGMA=0.2, ALGO="Normal")


def _correct_frozen_job(work_dir: Path, attempt: int) -> None:
    """Ionic relaxation frozen (EDDDAV/ZPOTRF/EDDRMM/ZHEGV/CNORMN/FEXCF).

    Stage 1: increase POTIM slightly.
    Stage 2: switch IBRION to conjugate-gradient (2) + larger POTIM.
    Stage 3+: switch to damped MD (IBRION=3) with SMASS and POTIM.
    """
    if attempt <= 1:
        params = read_incar(work_dir)
        potim = float(params.get("POTIM", "0.5"))
        new_potim = min(potim * 1.5, 5.0)
        patch_incar(work_dir, POTIM=new_potim)
    elif attempt == 2:
        patch_incar(work_dir, IBRION=2, POTIM=1.0)
    else:
        patch_incar(work_dir, IBRION=3, POTIM=0.5, SMASS=3)


def _correct_scf_no_converge(work_dir: Path, attempt: int) -> None:
    """SCF not converging (Sub-Space-Matrix not hermitian).

    Stage 1: reduce mixing parameters.
    Stage 2: switch ALGO to Normal (Davidson) + tighter mixing.
    Stage 3+: ALGO=All + very small mixing + add AMIX_MAG/BMIX_MAG.
    """
    if attempt <= 1:
        patch_incar(work_dir, AMIX=0.1, BMIX=0.001)
    elif attempt == 2:
        patch_incar(work_dir, ALGO="Normal", AMIX=0.05, BMIX=0.0001)
    else:
        patch_incar(
            work_dir,
            ALGO="All",
            AMIX=0.02,
            BMIX=0.0001,
            AMIX_MAG=0.02,
            BMIX_MAG=0.0001,
        )


def _correct_edwav(work_dir: Path, attempt: int) -> None:
    """EDWAV wavefunction orthogonalisation error.

    Stage 1: reduce POTIM.
    Stage 2: switch ALGO to Normal + reduce POTIM further.
    Stage 3+: ALGO=Normal, very small POTIM, switch IBRION=1.
    """
    if attempt <= 1:
        params = read_incar(work_dir)
        potim = float(params.get("POTIM", "0.5"))
        new_potim = max(potim * 0.5, 0.01)
        patch_incar(work_dir, POTIM=new_potim)
    elif attempt == 2:
        patch_incar(work_dir, ALGO="Normal", POTIM=0.1)
    else:
        patch_incar(work_dir, ALGO="Normal", POTIM=0.05, IBRION=1)


def _correct_brion_error(work_dir: Path, attempt: int) -> None:
    """BRION/BRMIX ionic relaxation error.

    Stage 1: switch to IBRION=2 (CG) + increase POTIM.
    Stage 2: IBRION=1 (quasi-Newton) + moderate POTIM.
    Stage 3+: IBRION=3 (damped MD) + SMASS.
    """
    if attempt <= 1:
        patch_incar(work_dir, IBRION=2, POTIM=1.0)
    elif attempt == 2:
        patch_incar(work_dir, IBRION=1, POTIM=0.5)
    else:
        patch_incar(work_dir, IBRION=3, POTIM=0.5, SMASS=3)


# ══════════════════════════════════════════════════════════════════════════
# Correction registry
# ══════════════════════════════════════════════════════════════════════════

CORRECTION_REGISTRY: dict[str, CorrectionFn] = {
    "positive_energy": _correct_positive_energy,
    "frozen_job": _correct_frozen_job,
    "scf_no_converge": _correct_scf_no_converge,
    "edwav": _correct_edwav,
    "brion_error": _correct_brion_error,
}


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════


def apply_correction(work_dir: Path, error_type: str | None, attempt_number: int) -> bool:
    """Apply a staged correction for a diagnosed VASP error.

    Args:
        work_dir: VASP calculation directory (must contain INCAR).
        error_type: Error category from :func:`diagnose_failure`, or None.
        attempt_number: 1-based attempt counter for staged escalation.

    Returns:
        True if a correction was applied, False otherwise.

    For known error types, the appropriate staged correction is applied.
    For unknown/None errors, falls back to copying CONTCAR -> POSCAR
    (structure restart without INCAR modification).
    """
    work_dir = Path(work_dir)

    if error_type and error_type in CORRECTION_REGISTRY:
        correction_fn = CORRECTION_REGISTRY[error_type]
        logger.info(
            "Auto-heal: applying '%s' correction (stage %d) in %s",
            error_type, attempt_number, work_dir.name,
        )
        correction_fn(work_dir, attempt_number)
        # Always restart from CONTCAR after INCAR correction
        restart_from_contcar(work_dir)
        return True

    # Fallback: unknown error — just restart from CONTCAR
    contcar = work_dir / "CONTCAR"
    if contcar.is_file():
        logger.info(
            "Auto-heal: unknown error '%s', fallback CONTCAR->POSCAR in %s",
            error_type, work_dir.name,
        )
        restart_from_contcar(work_dir)
        return True

    logger.warning(
        "Auto-heal: no CONTCAR available for fallback in %s", work_dir.name
    )
    return False
