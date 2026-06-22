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
from vasp_sop.core.state import DefectResult, PipelineState, StateStore, StepStatus
from vasp_sop.defect.builder import build_all, construct_complex_defects
from vasp_sop.defect.compute import run_vasp
from vasp_sop.defect.analysis import analyze

logger = logging.getLogger(__name__)

_DEFECT_DIR = "defect"


def run_defect(
    config: PipelineConfig,
    state: PipelineState,
) -> DefectResult:
    """Execute (or skip) the Defect stage.

    Requires completed CPD and Unitcell stages.
    """
    if state.defect_status == StepStatus.DONE and state.defect_result is not None:
        logger.info("Defect stage already complete, skipping.")
        return state.defect_result

    if state.cpd_result is None or state.unitcell_result is None:
        raise RuntimeError("CPD and Unitcell stages must complete before defect stage.")

    root = config.root
    defect_root = root / _DEFECT_DIR
    defect_root.mkdir(parents=True, exist_ok=True)

    state.defect_status = StepStatus.RUNNING
    StateStore.save(state)

    # Path to the fully-relaxed unitcell CONTCAR
    uc_contcar = (
        root / "unitcell" / "structure_opt" / "CONTCAR"
    )
    if not uc_contcar.is_file():
        raise FileNotFoundError(
            f"Unitcell CONTCAR not found at {uc_contcar}. "
            "Run the unitcell stage first."
        )

    # ── 1. Build supercell + defect structures + VASP inputs ──────
    build_all(defect_root, uc_contcar.parent, config)

    # ── 2. Complex defect construction (order >= 2) ──────────────
    if config.complex_defect_order >= 2:
        construct_complex_defects(defect_root, config)

    # ── 3. Run VASP: perfect first, then all defects ─────────────
    run_vasp(defect_root)

    # ── 4. Post-processing ──────────────────────────────────────
    unitcell_dir = root / "unitcell"
    cpd_dir = root / "cpd"
    analyze(
        defect_root,
        root,
        config,
        unitcell_yaml=unitcell_dir / "unitcell.yaml",
        standard_energies=cpd_dir / "standard_energies.yaml",
        target_vertices=cpd_dir / "target_vertices.yaml",
    )

    result = DefectResult(
        defect_energy_summary_path=(defect_root / "defect_energy_summary.json").resolve(),
        calc_summary_path=(defect_root / "calc_summary.json").resolve(),
    )

    state.defect_result = result
    state.defect_status = StepStatus.DONE
    StateStore.save(state)
    logger.info("Defect stage complete.")
    return result
