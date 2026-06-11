"""Unitcell stage.

Runs structure optimisation for the perfect unit cell, followed by
band-structure, DOS, and dielectric-response calculations.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.jobs import (
    submit_vasp,
    wait_all,
    run_local,
    _vasp_input_ready,
)
from vasp_sop.core.state import (
    PipelineState,
    StateStore,
    StepStatus,
    UnitcellResult,
)

logger = logging.getLogger(__name__)

_UNITCELL_DIR = "unitcell"
_STRUCTURE_OPT = "structure_opt"
_UNITCELL_YAML = "unitcell.yaml"

_VISE_TASKS: dict[str, str] = {
    "band": "vise vs -x pbesol -t band",
    "dos": "vise vs -x pbesol -t dos -k 2 -uis LVTOT True LAECHG True KPAR 1",
    "dielectric": "vise vs -x pbesol -t dielectric_dfpt -k 2",
}


def run_unitcell(
    config: PipelineConfig,
    state: PipelineState,
) -> UnitcellResult:
    """Execute (or skip) the Unitcell stage.

    Returns the result from the state if already done.
    """
    if state.unitcell_status == StepStatus.DONE and state.unitcell_result is not None:
        logger.info("Unitcell stage already complete, skipping.")
        return state.unitcell_result

    if state.cpd_result is None:
        raise RuntimeError("CPD stage must complete before unitcell stage.")

    root = config.root
    uc_root = root / _UNITCELL_DIR
    uc_root.mkdir(parents=True, exist_ok=True)

    state.unitcell_status = StepStatus.RUNNING
    StateStore.save(state)

    # ── 1. Copy structure from CPD result or custom path ─────────────
    src_structure = config.custom_poscar_path or state.cpd_result.unitcell_path
    structure_opt_dir = uc_root / _STRUCTURE_OPT

    if not structure_opt_dir.is_dir():
        logger.info("Unitcell: copying structure from %s", src_structure)
        shutil.copytree(str(src_structure), str(structure_opt_dir))

    # ── 2. Structure optimisation ─────────────────────────────────────
    _prepare_vasp_input(structure_opt_dir, config)
    logger.info("Unitcell: submitting structure optimisation")
    wait_all([submit_vasp(structure_opt_dir.resolve(), nproc=64)])

    # Copy CONTCAR → POSCAR for subsequent calculations
    contcar = structure_opt_dir / "CONTCAR"
    if contcar.is_file():
        shutil.copy(str(contcar), str(structure_opt_dir / "POSCAR"))

    # ── 3. Band / DOS / dielectric (parallel batch) ───────────────────
    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    pp_suffix = f" --options set_hubbard_u True {pp_opt}"

    uc_jobs = []
    for task_name in _VISE_TASKS:
        task_dir = uc_root / task_name
        task_dir.mkdir(exist_ok=True)

        if not _vasp_input_ready(task_dir):
            _copy_input_from_opt(structure_opt_dir, task_dir)

        cmd = _VISE_TASKS[task_name] + pp_suffix
        if not _vasp_input_ready(task_dir):
            run_local(cmd, cwd=task_dir, timeout=300)

        logger.info("Unitcell: submitting %s", task_name)
        uc_jobs.append(submit_vasp(task_dir.resolve(), nproc=64))

    logger.info("Unitcell: waiting for band/dos/dielectric")
    wait_all(uc_jobs)

    # ── 4. Post-processing ───────────────────────────────────────────
    _run_post_processing(uc_root, config)

    result = UnitcellResult(
        unitcell_yaml_path=(uc_root / _UNITCELL_YAML).resolve(),
        band_path=(uc_root / "band").resolve(),
        dos_path=(uc_root / "dos").resolve(),
        dielectric_path=(uc_root / "dielectric").resolve(),
    )

    state.unitcell_result = result
    state.unitcell_status = StepStatus.DONE
    StateStore.save(state)
    logger.info("Unitcell stage complete.")
    return result


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════


def _prepare_vasp_input(work_dir: Path, config: PipelineConfig) -> None:
    """Generate VASP inputs via vise if missing."""
    if _vasp_input_ready(work_dir):
        return

    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    cmd = (
        f"vise vs -x {config.functional} -k 2 "
        f"--options set_hubbard_u True -uis NSW 50 {pp_opt}"
    )
    run_local(cmd, cwd=work_dir, timeout=300)


def _copy_input_from_opt(src: Path, dst: Path) -> None:
    """Copy POSCAR and prior_info.yaml from structure_opt to a sub-task dir."""
    poscar_src = src / "POSCAR"
    if poscar_src.is_file():
        shutil.copy(str(poscar_src), str(dst / "POSCAR"))

    prior_src = src / "prior_info.yaml"
    if prior_src.is_file():
        shutil.copy(str(prior_src), str(dst / "prior_info.yaml"))


def _run_post_processing(uc_root: Path, config: PipelineConfig) -> None:
    """Run post-processing visualisation and unitcell.yaml generation."""
    uc_yaml = uc_root / _UNITCELL_YAML
    if uc_yaml.is_file():
        logger.info("Unitcell yaml already exists, skipping post-processing.")
        return

    band_dir = uc_root / "band"
    dos_dir = uc_root / "dos"
    dielectric_dir = uc_root / "dielectric"

    band_vasprun = band_dir / "vasprun.xml"
    band_outcar = band_dir / "OUTCAR"
    dielectric_outcar = dielectric_dir / "OUTCAR"

    if band_vasprun.is_file():
        run_local("cd band && vise pb", cwd=uc_root)

    if dos_dir.is_dir():
        run_local("cd dos && vise pd", cwd=uc_root)
        run_local(
            "cd dos && pydefect_vasp le -v AECCAR0 AECCAR1 AECCAR2 "
            "-i all_electron_charge",
            cwd=uc_root,
        )

    if dielectric_dir.is_dir():
        run_local("cd dielectric && vise pdf", cwd=uc_root)

    # Generate unitcell.yaml
    cmd = (
        f"pydefect_vasp u -vb {band_vasprun} -ob {band_outcar} "
        f"-odc {dielectric_outcar} -odi {dielectric_outcar} "
        f"-n '{config.formula}'"
    )
    run_local(cmd, cwd=uc_root)
