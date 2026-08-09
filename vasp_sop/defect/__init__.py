"""Point-defect calculation pipeline: CPD, unitcell, defect stages.

This package provides three independent submodules:
- ``builder``: supercell construction, defect enumeration, VASP input generation
- ``compute``: VASP job submission with CONTCAR restart loop
- ``analysis``: post-processing (energy corrections, defect summaries)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.builder import build_all, construct_complex_defects
from vasp_sop.defect.compute import run_vasp
from vasp_sop.defect.analysis import analyze

logger = logging.getLogger(__name__)

_DEFECT_DIR = "defect"
DEFECT_NEW_DIR = "defect_new"

# Directories that are never defect calculation dirs
_NON_DEFECT_DIRS = frozenset({"perfect", "defect_new", "__pycache__"})

# Anion-role elements: the negative partner in these oxide/sulfide hosts.
# A single substitution where exactly one side is an anion-role element is
# an anion-cation antisite (ADR 0013) — a cation on an anion site or an
# anion on a cation site is physically unreasonable and excluded from the
# defect set (still enumerated by pydefect, but never submitted or analyzed).
_ANION_ELEMENTS = frozenset(
    {"O", "S", "Se", "Te", "F", "Cl", "Br", "I", "N", "P"}
)
_SINGLE_DEFECT_RE = re.compile(r"^([A-Z][a-z]?)_([A-Z][a-z]?)(\d+)_(-?\d+)$")


def _is_anion_cation_antisite(name: str) -> bool:
    """True if *name* is a single substitution with exactly one anion side.

    ``O_Ga1_-1`` (anion on a cation site) and ``Bi_O1_0`` (cation on an
    anion site) both match; ``Gd_Sb1_6``, ``Va_O1_0`` and complex defects
    (``Gd_Ga1+Va_O1_-1``) do not.
    """
    m = _SINGLE_DEFECT_RE.match(name)
    if m is None or m.group(1) == "Va":
        return False
    return (m.group(1) in _ANION_ELEMENTS) != (m.group(2) in _ANION_ELEMENTS)


def is_valid_defect_dir(path: Path, *, include_defect_new: bool = False) -> bool:
    """Return True if *path* is a legitimate defect calculation directory.

    A directory is valid if:
      - Its name matches the ``Name_Charge`` pattern (contains ``_`` with
        non-empty parts on both sides), OR
      - It contains a ``defect_entry.json`` file.

    The ``defect_new/`` parallel tree is always excluded unless
    *include_defect_new* is True (opt-in via ``plan.yaml`` key
    ``defects.include_defect_new: true``).

    Junk directories (no ``_`` in name, no ``defect_entry.json``) are
    excluded so they are never counted in scans or accidentally submitted.
    """
    if not path.is_dir():
        return False

    name = path.name

    # Explicit exclusion of defect_new unless opted in
    if name == DEFECT_NEW_DIR:
        return include_defect_new

    # Exclude known non-defect dirs
    if name in _NON_DEFECT_DIRS:
        return False

    # Hidden dirs
    if name.startswith("."):
        return False

    # Anion-cation antisites are excluded (ADR 0013): still on disk, but
    # never submitted or counted by any scan (this gate is the single
    # entry point for wave2 submission and analysis enumeration).
    if _is_anion_cation_antisite(name):
        return False

    # Check Name_Charge pattern: split on first "_", both parts non-empty
    if "_" in name:
        parts = name.split("_", 1)
        if parts[0] and parts[1]:
            return True

    # Fallback: directory contains defect_entry.json
    if (path / "defect_entry.json").is_file():
        return True

    return False


def iter_defect_dirs(
    defect_root: Path,
    *,
    include_perfect: bool = False,
    include_defect_new: bool = False,
) -> list[Path]:
    """Return sorted list of valid defect directories under *defect_root*.

    Filters out junk dirs and defect_new/ (unless opted in).
    Optionally includes the ``perfect/`` directory.
    """
    dirs: list[Path] = []
    if not defect_root.is_dir():
        return dirs
    for d in sorted(defect_root.iterdir()):
        if not d.is_dir():
            continue
        if include_perfect and d.name == "perfect":
            dirs.append(d)
            continue
        if is_valid_defect_dir(d, include_defect_new=include_defect_new):
            dirs.append(d)
    return dirs



