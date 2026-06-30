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



