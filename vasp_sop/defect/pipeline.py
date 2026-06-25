"""Pipeline orchestration — two-wave VASP submission.

Wave 1: submit structure_opt (= CPD target phase)
        └── while it runs, generate all other VASP inputs locally
Wave 2: submit ALL remaining VASP (competing phases + band + dos +
        dielectric + perfect + all defects) in one batch
Wave 3: wait for all → post-process everything
"""

from __future__ import annotations

import logging
import json
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.io import input_ready, prepare_inputs
from vasp_sop.core.jobs import (
    CrispVaspJob,
    VaspJob,
    move_crisp_outputs,
    submit_vasp,
    wait_all,
    run_local,
)
from vasp_sop.core.state import (
    CpdResult,
    DefectResult,
    PipelineState,
    StateStore,
    StepStatus,
    UnitcellResult,
)
from vasp_sop.materials import get_intrinsic_elements
from vasp_sop.defect import cpd as _cpd
from vasp_sop.defect import unitcell as _uc
from vasp_sop.defect.builder import build_all as _build_defects
from vasp_sop.defect.compute import run_vasp as _run_defect_vasp
from vasp_sop.defect.analysis import analyze as _analyze_defects

logger = logging.getLogger(__name__)

_CPD_DIR = "cpd"
_UC_DIR = "unitcell"
_DF_DIR = "defect"


def _resolve_target_job(target_dir: Path) -> VaspJob | None:
    """Check for pre-submitted structure_opt (from init)."""
    import json
    submit_file = target_dir.parent / ".target_submit.json"
    if not submit_file.is_file():
        return None
    try:
        with open(submit_file) as f:
            info = json.load(f)
        task_name = info.get("task_name")
        if not task_name:
            return None
        return CrispVaspJob(Path(info.get("work_dir", str(target_dir))), task_name)
    except Exception:
        return None




def _check_calc_cache(target_dir: Path) -> bool:
    """Restore target_dir's VASP outputs from global calc cache.

    Returns True if cache hit and OUTCAR/CONTCAR were restored.
    """
    name = target_dir.name
    if "_mp-" not in name:
        return False
    formula_pt, mpid = name.split("_mp-", 1)

    from vasp_sop.core.cache import vasp_results_get
    cached = vasp_results_get(formula_pt, mpid)
    if cached is None:
        return False

    logger.info("Calc cache HIT for %s (mp-%s), restoring ...", formula_pt, mpid)
    import shutil
    for f in ("OUTCAR", "CONTCAR", "vasprun.xml"):
        src = cached / f
        if src.is_file():
            shutil.copy2(str(src), str(target_dir / f))

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
    # Wave 1 — structure_opt (=CPD target phase)
    #           generate ALL other VASP inputs locally while it runs
    # ═══════════════════════════════════════════════════════════════════

    if state.cpd_status == StepStatus.DONE and state.cpd_result is not None:
        logger.info("CPD already done, using cached result.")
        cpd_result = state.cpd_result
    else:
        state.cpd_status = StepStatus.RUNNING
        StateStore.save(state)

        intrinsic_elements = get_intrinsic_elements(config.formula)
        logger.info("Intrinsic elements: %s", intrinsic_elements)

        # Determine target and competing-phase directories
        cpd_info = _cpd._get_cpd_info(cpd_root, intrinsic_elements)
        target_dir, other_dirs = _cpd._split_target(
            cpd_root, cpd_info, config.formula)

        # --- Check global calc cache (skip structure_opt VASP if cached) ---
        cache_hit = _check_calc_cache(target_dir)

        if not cache_hit:
            # --- Check pre-submitted structure_opt (from init) ---
            opt_job = _resolve_target_job(target_dir)

            if opt_job is None:
                prepare_inputs(target_dir, config)
                opt_job = submit_vasp(target_dir.resolve())
            elif opt_job.done and opt_job.poll() != 0:
                logger.warning("Pre-submitted structure_opt failed; re-submitting.")
                prepare_inputs(target_dir, config)
                opt_job = submit_vasp(target_dir.resolve())
            elif opt_job.done and opt_job.poll() == 0:
                logger.info("Structure_opt already finished (pre-submitted).")
            else:
                logger.info("Structure_opt pre-submitted (task %s), waiting ...",
                            opt_job.task_name)

        # Generate ALL non-target VASP inputs (always — even if cache hit)
        for d in other_dirs:
            prepare_inputs(d, config)
        _build_defects(df_root, target_dir, config)
        _uc._prepare_all_inputs(uc_root, target_dir, config)

        # --- Submit competing phases + wait for ALL wave-1 VASP ---
        comp_jobs: list = []
        if not cache_hit:
            comp_jobs = _cpd._submit_remaining(cpd_root, other_dirs, config)
            if not (opt_job and opt_job.done and opt_job.poll() == 0):
                logger.info("Waiting for structure_opt ...")
                if opt_job:
                    wait_all([opt_job])
                move_crisp_outputs(target_dir)
            if comp_jobs:
                logger.info("Waiting for %d competing-phase VASP jobs ...", len(comp_jobs))
                wait_all(comp_jobs)
            for d in other_dirs:
                move_crisp_outputs(d)

        # CPD post-processing (same for cache hit or miss)
        target_composition = _cpd._get_target_composition(config.formula)
        _cpd.compute_chemical_potentials(cpd_root, config, target_composition)

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
    # Wave 2 — Submit + auto-restart all remaining VASP
    # ═══════════════════════════════════════════════════════════════════

    if state.defect_status == StepStatus.DONE and state.defect_result is not None:
        logger.info("Defect already done, skipping.")
        return state

    # Move crisp outputs (resume safety)
    for d in ([uc_root] if uc_root.is_dir() else []) + ([df_root] if df_root.is_dir() else []):
        for child in list(d.iterdir()):
            if child.is_dir():
                move_crisp_outputs(child)

    # Unitcell: band / dos / dielectric (single batch, no restart needed)
    uc_jobs = []
    for d in _uc._get_task_dirs(uc_root, config):
        if not input_ready(d):
            logger.warning("Inputs not ready for %s, skipping.", d.name)
            continue
        outcar = d / "OUTCAR"
        if outcar.is_file() or (d / "output" / "OUTCAR").is_file():
            logger.info("Skipping %s: already computed", d.name)
            continue
        uc_jobs.append(submit_vasp(d.resolve(), nproc=64))

    if uc_jobs:
        logger.info("Submitting %d unitcell tasks", len(uc_jobs))
        wait_all(uc_jobs)
        for j in uc_jobs:
            move_crisp_outputs(j.work_dir)

    # Defect: submit with auto-restart (up to 20x, progress tracking)
    state.defect_status = StepStatus.RUNNING
    StateStore.save(state)
    if df_root.is_dir():
        _run_defect_vasp(df_root)
    # ═══════════════════════════════════════════════════════════════════
    # Wave 3 — Post-processing
    # ═══════════════════════════════════════════════════════════════════

    # Unitcell post-processing
    if state.unitcell_status != StepStatus.DONE:
        _uc.build_unitcell_yaml(uc_root, config)
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
        _analyze_defects(
            df_root, root, config,
            unitcell_yaml=root / "unitcell" / "unitcell.yaml",
            standard_energies=root / "cpd" / "standard_energies.yaml",
            target_vertices=root / "cpd" / "target_vertices.yaml",
        )
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
