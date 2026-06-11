"""Pipeline orchestration — two-wave VASP submission.

Wave 1: submit structure_opt (= CPD target phase)
        └── while it runs, generate all other VASP inputs locally
Wave 2: submit ALL remaining VASP (competing phases + band + dos +
        dielectric + perfect + all defects) in one batch
Wave 3: wait for all → post-process everything
"""

from __future__ import annotations

import logging
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.jobs import (
    VaspJob,
    move_crisp_outputs,
    submit_vasp,
    wait_all,
    run_local,
    _vasp_input_ready,
)
from vasp_sop.core.state import (
    CpdResult,
    DefectResult,
    PipelineState,
    StateStore,
    StepStatus,
    UnitcellResult,
)
from vasp_sop.defect import cpd as _cpd
from vasp_sop.defect import unitcell as _uc
from vasp_sop.defect import defects as _df

logger = logging.getLogger(__name__)

_CPD_DIR = "cpd"
_UC_DIR = "unitcell"
_DF_DIR = "defect"


def run_point_defect_pipeline(
    config: PipelineConfig,
) -> PipelineState:
    """Execute defect pipeline in three waves (see module docstring)."""
    state = StateStore.load(config.root)
    logger.info("Pipeline: CPD=%s Unitcell=%s Defect=%s",
                state.cpd_status.value, state.unitcell_status.value,
                state.defect_status.value)

    root = config.root
    cpd_root = root / _CPD_DIR
    uc_root = root / _UC_DIR
    df_root = root / _DF_DIR

    # ═══════════════════════════════════════════════════════════════════
    # Wave 1 — Submit structure_opt (=CPD target phase)
    #           + generate ALL other VASP inputs locally while it runs
    # ═══════════════════════════════════════════════════════════════════

    if state.cpd_status == StepStatus.DONE and state.cpd_result is not None:
        logger.info("CPD already done, using cached result.")
        cpd_result = state.cpd_result
    else:
        state.cpd_status = StepStatus.RUNNING
        StateStore.save(state)

        intrinsic_elements = _cpd._get_intrinsic_elements(config.formula)
        logger.info("Intrinsic elements: %s", intrinsic_elements)

        # Determine target and competing-phase directories
        cpd_info = _cpd._get_cpd_info(cpd_root, intrinsic_elements)
        target_dir, other_dirs = _cpd._split_target(
            cpd_root, cpd_info, config.formula)

        # --- Wave 1a: submit structure_opt (CPD target phase) ---
        _cpd._prepare_vasp_input(target_dir, config)
        opt_job = submit_vasp(target_dir.resolve())

        # --- Wave 1b: generate ALL other VASP inputs (local) ---
        # Competing phases (CPD)
        for d in other_dirs:
            _cpd._prepare_vasp_input(d, config)
        # Unitcell: band / dos / dielectric
        _uc._prepare_all_inputs(uc_root, target_dir, config)
        # Defect: supercell + defect enumeration + VASP inputs
        _df._prepare_all_inputs(df_root, target_dir, config)

        # --- Wave 1c: submit competing phases too (they're independent) ---
        comp_jobs = _cpd._submit_remaining(cpd_root, other_dirs, config)

        # --- Wave 1d: wait for structure_opt ---
        logger.info("Waiting for structure_opt ...")
        wait_all([opt_job])
        move_crisp_outputs(target_dir)
        logger.info("Structure optimisation complete.")

        # Create CPD result (other phases may still run in background)
        # For post-proc we still need to wait for all competing phases
        if comp_jobs:
            logger.info("Waiting for %d competing-phase jobs ...", len(comp_jobs))
            wait_all(comp_jobs)
            for j in comp_jobs:
                move_crisp_outputs(j.work_dir)

        # CPD post-processing
        target_composition = _cpd._get_target_composition(config.formula)
        _cpd._run_post_processing(cpd_root, config, target_composition)

        cpd_result = CpdResult(
            unitcell_path=target_dir.resolve(),
            chem_pot_path=(cpd_root / _cpd._TARGET_VERTICES).resolve(),
            standard_energies_path=(cpd_root / _cpd._STANDARD_ENERGIES).resolve(),
        )
        state.cpd_result = cpd_result
        state.cpd_status = StepStatus.DONE
        StateStore.save(state)
        logger.info("CPD stage complete.")

    # ═══════════════════════════════════════════════════════════════════
    # Wave 2 — Submit ALL remaining VASP in one batch + wait
    # ═══════════════════════════════════════════════════════════════════

    if state.defect_status == StepStatus.DONE and state.defect_result is not None:
        logger.info("Defect already done, skipping.")
        return state

    # Collect all remaining VASP directories
    remaining_jobs: list[VaspJob] = []

    # Unitcell: band / dos / dielectric
    uc_tasks = _uc._get_task_dirs(uc_root, config)
    for d in uc_tasks:
        if _vasp_input_ready(d):
            logger.info("Queueing VASP: %s", d.name)
            remaining_jobs.append(submit_vasp(d.resolve(), nproc=64))
        else:
            logger.warning("Inputs not ready for %s, skipping.", d.name)

    # Defect: perfect + all charge states
    df_dirs = _df._get_calc_dirs(df_root)
    for d in df_dirs:
        if _vasp_input_ready(d):
            logger.info("Queueing VASP: %s", d.name)
            remaining_jobs.append(submit_vasp(d.resolve()))
        else:
            logger.warning("Inputs not ready for %s, skipping.", d.name)

    if remaining_jobs:
        state.defect_status = StepStatus.RUNNING
        StateStore.save(state)

        logger.info("Submitting %d remaining VASP jobs ...", len(remaining_jobs))
        wait_all(remaining_jobs)
        for j in remaining_jobs:
            move_crisp_outputs(j.work_dir)

    # ═══════════════════════════════════════════════════════════════════
    # Wave 3 — Post-processing
    # ═══════════════════════════════════════════════════════════════════

    # Unitcell post-processing
    if state.unitcell_status != StepStatus.DONE:
        _uc._run_post_processing(uc_root, config)
        state.unitcell_result = UnitcellResult(
            unitcell_yaml_path=(uc_root / _uc._UNITCELL_YAML).resolve(),
            band_path=(uc_root / "band").resolve(),
            dos_path=(uc_root / "dos").resolve(),
            dielectric_path=(uc_root / "dielectric").resolve(),
        )
        state.unitcell_status = StepStatus.DONE
        StateStore.save(state)
        logger.info("Unitcell stage complete.")

    # Defect post-processing
    if state.defect_status != StepStatus.DONE:
        _df._run_post_processing(df_root, root, config)
        state.defect_result = DefectResult(
            defect_energy_summary_path=(df_root / "defect_energy_summary.json").resolve(),
            calc_summary_path=(df_root / "calc_summary.json").resolve(),
        )
        state.defect_status = StepStatus.DONE
        StateStore.save(state)
        logger.info("Defect stage complete.")

    if state.is_terminal():
        logger.info("Pipeline complete.")
    return state
