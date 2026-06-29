"""VASP error diagnosis — pattern matching on OUTCAR output.

Patterns extracted from custodian's error handler catalog for the most
common VASP failure modes.  Used by :mod:`vasp_sop.defect.compute` to
decide whether to apply targeted fixes during CONTCAR restarts.
"""

from __future__ import annotations

import re as _re
from pathlib import Path

_ERROR_PATTERNS: dict[str, list[str]] = {
    "positive_energy": [
        r"the total energy is positive",
    ],
    "frozen_job": [
        r"EDDDAV",
        r"ZPOTRF",
        r"EDDRMM",
        r"ZHEGV",
        r"CNORMN",
        r"FEXCF",
    ],
    "scf_no_converge": [
        r"WARNING: Sub-Space-Matrix is not hermitian",
        r"WARNING: Sub-Space-Matrix is not hermitian in DAV",
    ],
    "brion_error": [
        r"BRION: computational error",
        r"BRMIX: very serious problems",
    ],
    "real_optlay": [
        r"REAL_OPTLAY: internal error",
    ],
    "edwav": [
        r"WARNING in EDWAV: call to DAV",
    ],
    "pssyevx": [
        r"ERROR in subspace rotation PSSYEVX",
    ],
    "bz_inequiv": [
        r"VERY BAD NEWS! internal error in subroutine BZ_INEQUIV",
    ],
    "rhosyg": [
        r"RHOSYG: internal error",
    ],
    "posmap": [
        r"POSMAP: internal error",
    ],
    "point_group": [
        r"ERROR: the point group of the supercell is not the same as the point group",
    ],
}


def diagnose_failure(outcar_path: Path) -> str | None:
    """Return the failure reason by matching OUTCAR text against known patterns.

    Returns the matched error category (e.g. ``"frozen_job"``,
    ``"positive_energy"``) or ``None`` if no known pattern is detected.
    """
    if not outcar_path.is_file():
        return None

    text = outcar_path.read_text(encoding="utf-8", errors="replace")
    text_tail = text[-16384:]

    # Check in tail first (most recent output), then full text
    for reason, patterns in _ERROR_PATTERNS.items():
        for pat in patterns:
            if _re.search(pat, text_tail):
                return reason
    for reason, patterns in _ERROR_PATTERNS.items():
        for pat in patterns:
            if _re.search(pat, text):
                return reason

    return None


_RECOMMENDED_FIXES: dict[str, str] = {
    "positive_energy": (
        "Try increasing SIGMA or switching ISMEAR to 0 "
        "(positive energy may indicate bad smearing)."
    ),
    "frozen_job": (
        "Ionic relaxation frozen.  Try shaking the structure slightly "
        "or increasing POTIM."
    ),
    "scf_no_converge": (
        "SCF not converging.  Try reducing MIXING parameters (AMIX, BMIX) "
        "or switching to ALGO = Normal."
    ),
    "brion_error": (
        "Ionic relaxation error.  Try switching to IBRION = 2 "
        "and increasing POTIM."
    ),
    "edwav": (
        "Wavefunction orthogonalisation error.  Try reducing POTIM "
        "or using ALGO = Normal."
    ),
}


def recommended_fix(reason: str) -> str | None:
    """Return a human-readable fix suggestion for a diagnosed error."""
    return _RECOMMENDED_FIXES.get(reason)