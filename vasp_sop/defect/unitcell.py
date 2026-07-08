"""Unitcell stage.

Runs structure optimisation for the perfect unit cell, followed by
band-structure, DOS, and dielectric-response calculations.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.io import input_ready, prepare_inputs
from vasp_sop.core.jobs import (
    VaspJob,
    move_crisp_outputs,
    submit_vasp,
    wait_all,
    run_local,
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


def _prepare_all_inputs(uc_root: Path, target_dir: Path, config: PipelineConfig) -> None:
    """Create unitcell dirs and generate VASP inputs (no VASP needed)."""
    uc_root.mkdir(parents=True, exist_ok=True)
    structure_opt_dir = uc_root / _STRUCTURE_OPT

    # Copy POSCAR from CPD target dir (CONTCAR doesn't exist yet — VASP hasn't run)
    poscar_src = target_dir / "POSCAR"
    structure_opt_dir.mkdir(parents=True, exist_ok=True)
    if poscar_src.exists() and not (structure_opt_dir / "POSCAR").exists():
        shutil.copy2(str(poscar_src), str(structure_opt_dir / "POSCAR"))

    prepare_inputs(structure_opt_dir, config)
    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    encut_opt = (
        f"-uis ENCUT {config.encut}" if config.encut else ""
    )
    # ``functional`` and ENCUT both come from config so all unitcell tasks
    # use the same setup as the CPD and defect stages.
    task_cmd_overrides = f" -x {config.functional} {encut_opt}"
    pp_suffix = f" --options set_hubbard_u True {pp_opt}"

    for task_name in _VISE_TASKS:
        task_dir = uc_root / task_name
        task_dir.mkdir(exist_ok=True)
        if not input_ready(task_dir):
            _copy_input_from_opt(structure_opt_dir, task_dir)
        # Replace the hardcoded ``-x pbesol`` baked into the template with the
        # config's functional, and inject ENCUT after the existing -uis flags
        # (or as the first -uis token if none present).
        base = _VISE_TASKS[task_name].replace("-x pbesol", task_cmd_overrides, 1)
        if config.encut and "ENCUT" not in base:
            base = base + f" -uis ENCUT {config.encut}"
        cmd = base + pp_suffix
        if not input_ready(task_dir):
            run_local(cmd, cwd=task_dir, timeout=300)


def _get_task_dirs(uc_root: Path, config: PipelineConfig) -> list[Path]:
    """Return [band_dir, dos_dir, dielectric_dir] for submission."""
    return [uc_root / t for t in _VISE_TASKS]





# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════




def _copy_input_from_opt(src: Path, dst: Path) -> None:
    """Copy POSCAR and prior_info.yaml from structure_opt to a sub-task dir."""
    poscar_src = src / "POSCAR"
    if poscar_src.is_file():
        shutil.copy(str(poscar_src), str(dst / "POSCAR"))

    prior_src = src / "prior_info.yaml"
    if prior_src.is_file():
        shutil.copy(str(prior_src), str(dst / "prior_info.yaml"))


def build_unitcell_yaml(uc_root: Path, config: PipelineConfig) -> None:
    """Run post-processing visualisation and unitcell.yaml generation."""
    uc_yaml = uc_root / _UNITCELL_YAML
    if uc_yaml.is_file():
        logger.info("Unitcell yaml already exists, skipping post-processing.")
        return

    band_dir = uc_root / "band"
    dos_dir = uc_root / "dos"
    dielectric_dir = uc_root / "dielectric"

    band_vasprun_candidates = [
        (band_dir / "vasprun.xml").resolve(),
        (band_dir / "output" / "vasprun.xml").resolve(),
    ]
    band_vasprun = next((p for p in band_vasprun_candidates if p.is_file()),
                         band_vasprun_candidates[0])
    band_outcar = (band_dir / "OUTCAR").resolve()
    dielectric_outcar = (dielectric_dir / "OUTCAR").resolve()

    if band_vasprun.is_file():
        try:
            run_local("cd band && vise pb", cwd=uc_root)
        except Exception:
            logger.warning("vise pb failed (likely no band structure to plot), skipping band plot.")

    if dos_dir.is_dir():
        try:
            run_local("cd dos && vise pd", cwd=uc_root)
        except Exception:
            logger.warning("vise pd failed (likely missing vasprun.xml), skipping DOS plot.")
        try:
            run_local(
                "cd dos && pydefect_vasp le -v AECCAR0 AECCAR2 "
                "-i all_electron_charge",
                cwd=uc_root,
            )
        except Exception:
            logger.warning("pydefect_vasp le failed (AECCAR missing), skipping local-extrema.")

    if dielectric_dir.is_dir():
        try:
            run_local("cd dielectric && vise pdf", cwd=uc_root)
        except Exception:
            logger.warning("vise pdf failed (likely no band gap), skipping dielectric plot.")

    cmd = (
        f"pydefect_vasp u -vb {band_vasprun} -ob {band_outcar} "
        f"-odc {dielectric_outcar} -odi {dielectric_outcar} "
        f"-n '{config.formula}'"
    )
    try:
        run_local(cmd, cwd=uc_root)
    except Exception:
        logger.warning("pydefect_vasp u failed (likely zero band gap), skipping unitcell.yaml.")
