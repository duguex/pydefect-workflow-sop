"""Pipeline orchestration — ties the three defect stages together.

Each stage is idempotent: ``run_point_defect_pipeline`` checks the
persisted state and only executes stages that are not yet ``DONE``.
"""

from __future__ import annotations

import logging

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.state import PipelineState, StateStore, StepStatus
from vasp_sop.defect.cpd import run_cpd
from vasp_sop.defect.unitcell import run_unitcell
from vasp_sop.defect.defects import run_defect

logger = logging.getLogger(__name__)


def run_point_defect_pipeline(
    config: PipelineConfig,
) -> PipelineState:
    """Execute the full point-defect pipeline from CPD through defects.

    Args:
        config: Pipeline configuration (formula, dopants, parameters, …).

    Returns:
        Terminal ``PipelineState`` with all three stages marked ``DONE``.
    """
    state = StateStore.load(config.root)
    logger.info(
        "Pipeline state: CPD=%s Unitcell=%s Defect=%s",
        state.cpd_status.value,
        state.unitcell_status.value,
        state.defect_status.value,
    )

    # ── Stage 1: Chemical-potential diagram ─────────────────────────
    if state.cpd_status != StepStatus.DONE:
        logger.info("=== Stage 1/3: CPD ===")
        try:
            run_cpd(config, state)
        except Exception:
            state.cpd_status = StepStatus.FAILED
            StateStore.save(state)
            raise
    else:
        logger.info("Stage 1 (CPD) already complete.")

    # ── Stage 2: Unitcell properties ────────────────────────────────
    if state.unitcell_status != StepStatus.DONE:
        logger.info("=== Stage 2/3: Unitcell ===")
        try:
            run_unitcell(config, state)
        except Exception:
            state.unitcell_status = StepStatus.FAILED
            StateStore.save(state)
            raise
    else:
        logger.info("Stage 2 (Unitcell) already complete.")

    # ── Stage 3: Defect calculations ────────────────────────────────
    if state.defect_status != StepStatus.DONE:
        logger.info("=== Stage 3/3: Defects ===")
        try:
            run_defect(config, state)
        except Exception:
            state.defect_status = StepStatus.FAILED
            StateStore.save(state)
            raise
    else:
        logger.info("Stage 3 (Defects) already complete.")

    if state.is_terminal():
        logger.info("All stages complete.")
    else:
        logger.warning(
            "Pipeline finished with: CPD=%s Unitcell=%s Defect=%s",
            state.cpd_status.value,
            state.unitcell_status.value,
            state.defect_status.value,
        )

    return state
