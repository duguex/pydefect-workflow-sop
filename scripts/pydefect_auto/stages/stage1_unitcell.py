import os
import shutil
from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists,
    vasp_input_check, vasp_done_check,
)

UNITCELL_DIR = "unitcell"
SUB_DIRS = ["structure_opt", "band", "dos", "dielectric"]
FLAG_DONE = ".stage1_done"

VISE_TASKS = {
    "band": "vise vasp_set -x pbesol -t band -pd ../structure_opt",
    "dos": "vise vasp_set -x pbesol -t dos -pd ../structure_opt -uis LVTOT True LAECHG True KPAR 1",
    "dielectric": "vise vasp_set -x pbesol -t dielectric_dfpt -pd ../structure_opt",
}

DEFAULT_KPOINTS = """Automatic generation
0
Gamma
1 1 1
0 0 0
"""


def run(project_root, info, auto=False):
    root = Path(project_root)
    uc_dir = root / UNITCELL_DIR
    so_dir = uc_dir / "structure_opt"

    if flag_exists(FLAG_DONE, uc_dir):
        logger.info("Stage 1 already complete")
        return True

    # ----- 1.1 Structure optimization -----
    uc_dir.mkdir(parents=True, exist_ok=True)

    if so_dir.is_dir():
        logger.info("structure_opt/ exists, checking completion")
    else:
        logger.error("No structure_opt/ directory. Place POSCAR in unitcell/structure_opt/ first")
        return False

    poscar = so_dir / "POSCAR"
    if not poscar.exists():
        logger.error("No POSCAR in %s", so_dir)
        return False

    if not vasp_input_check(str(so_dir)):
        pp_args = _pp_flag(info)
        run_command(f'vise vasp_set -x pbesol {pp_args}', cwd=str(so_dir))

    if not vasp_done_check(str(so_dir)):
        if auto:
            _submit_to_crisp(str(so_dir), "unitcell_structure_opt")
        logger.info("structure_opt not finished. Submit VASP and rerun.")
        return False

    # structure_opt done: refresh POSCAR from CONTCAR
    contcar = so_dir / "CONTCAR"
    if contcar.exists():
        shutil.copy(str(contcar), str(poscar))
        logger.info("Copied CONTCAR -> POSCAR in structure_opt/")

    # ----- 1.2 Band / DOS / Dielectric -----
    for task in ["band", "dos", "dielectric"]:
        task_dir = uc_dir / task
        task_dir.mkdir(exist_ok=True)

        if vasp_input_check(str(task_dir)):
            logger.info("%s/ inputs ready, skipping", task)
            continue

        shutil.copy(str(poscar), str(task_dir / "POSCAR"))
        prior_src = so_dir / "prior_info.yaml"
        if prior_src.exists():
            shutil.copy(str(prior_src), str(task_dir / "prior_info.yaml"))

        pp_args = _pp_flag(info)
        cmd = VISE_TASKS[task] + pp_args
        run_command(cmd, cwd=str(task_dir))

    # Check if any task is still running
    all_done = all(vasp_done_check(str(uc_dir / t)) for t in ["band", "dos", "dielectric"])
    if not all_done:
        if auto:
            for t in ["band", "dos", "dielectric"]:
                td = uc_dir / t
                if not vasp_done_check(str(td)):
                    _submit_to_crisp(str(td), f"unitcell_{t}")
        logger.info("Band/DOS/Dielectric not all finished. Submit and rerun.")
        return False

    # ----- 1.3 Collect unitcell.yaml -----
    _unitcell_yaml_exists = (uc_dir / "unitcell.yaml").exists() or (root / "unitcell.yaml").exists()
    if not _unitcell_yaml_exists:
        _collect_unitcell(str(uc_dir), info["obj"])

    flag_write(FLAG_DONE, uc_dir)
    logger.info("Stage 1 (unitcell) complete")
    return True


def _pp_flag(info):
    pp = info.get("pp", [])
    if pp:
        return " " + " ".join(f"--potcar {p}" for p in pp)
    return ""


def _hubbard_flag(info):
    return " --options set_hubbard_u True" if info.get("hubbard_u") else ""


def _submit_to_crisp(local_dir, task_name):
    try:
        from ..crisp_utils import submit_job
        submit_job(local_dir, task_name)
        logger.info("Submitted %s to crisp", task_name)
    except ImportError:
        logger.warning("crisp not available, submit manually: %s", local_dir)


def _collect_unitcell(uc_dir, obj_name):
    # Sync output/ for each subdir before collection
    for task in ["band", "dos", "dielectric"]:
        out_dir = os.path.join(uc_dir, task, "output")
        for f in ["vasprun.xml", "OUTCAR", "CONTCAR", "EIGENVAL", "PROCAR"]:
            src = os.path.join(out_dir, f)
            dst = os.path.join(uc_dir, task, f)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
                logger.debug("  %s/%s: synced from output/", task, f)
    run_command("cd band; vise pb", cwd=uc_dir)
    run_command("cd dos; vise pd", cwd=uc_dir)
    run_command("cd dielectric; vise pdf", cwd=uc_dir)
    run_command(
        "cd dos; pydefect_vasp le -v AECCAR0 AECCAR1 AECCAR2 -i all_electron_charge",
        cwd=uc_dir,
    )
    run_command(
        f"pydefect_vasp u -vb band/vasprun.xml -ob band/OUTCAR "
        f"-odc dielectric/OUTCAR -odi dielectric/OUTCAR -n '{obj_name}'",
        cwd=uc_dir,
    )
    logger.info("unitcell.yaml created")
