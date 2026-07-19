"""Point-defect calculation pipeline: CPD, unitcell, defect stages.

This package provides three independent submodules:
- ``builder``: supercell construction, defect enumeration, VASP input generation
- ``compute``: VASP job submission with CONTCAR restart loop
- ``analysis``: post-processing (energy corrections, defect summaries)
"""

from __future__ import annotations

import logging
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



