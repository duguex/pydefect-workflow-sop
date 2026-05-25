import shutil
from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists, flag_remove,
    vasp_input_check, vasp_done_check,
)

FLAG_CLEAN = ".doping_clean_done"
FLAG_CPD = ".doping_cpd_done"
FLAG_DONE = ".stage6_done"
CPD_DIR = "cpd"
DEFECT_DIR = "defect"


def run(project_root, info, auto=False):
    root = Path(project_root)
    def_dir = root / DEFECT_DIR
    cpd_dir = root / CPD_DIR

    if flag_exists(FLAG_DONE, def_dir):
        logger.info("Stage 6 already complete")
        return True

    dopant = info.get("dopant_element", [])
    if not dopant:
        logger.error("No dopant_element specified in info.json")
        return False

    # Only process the dopant that's new — assume at least one is new
    dopant_str = " ".join(dopant)

    # ----- 6.1 Backup defect_in.yaml -----
    di_path = def_dir / "defect_in.yaml"
    if di_path.exists():
        bak = def_dir / "defect_in.yaml.bak"
        if not bak.exists():
            shutil.copy(str(di_path), str(bak))

    # ----- 6.2 Re-run defect_set with new dopant -----
    run_command(f"pydefect ds -d {dopant_str}", cwd=str(def_dir))

    # ----- 6.3 Create new defect entries -----
    run_command("pydefect_vasp de", cwd=str(def_dir))

    # ----- 6.4 VASP input for new defect dirs -----
    pp_args = _vise_flags(info)
    for d in sorted(def_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name == "perfect":
            continue
        if vasp_input_check(str(d)):
            continue
        run_command(f'vise vasp_set -x pbesol -t defect {pp_args}', cwd=str(d))

    # ----- 6.5 Copy submit scripts from existing dirs (template) -----
    existing = [d for d in def_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    template_slurm = None
    for d in existing:
        slurm = list(d.glob("submit*"))
        if slurm:
            template_slurm = slurm[0]
            break
    if template_slurm:
        for d in def_dir.iterdir():
            if d.is_dir() and not list(d.glob("submit*")):
                shutil.copy(str(template_slurm), str(d / template_slurm.name))
                logger.info("Copied %s to %s", template_slurm.name, d.name)

    # ----- 6.6 Clean incomplete CPD phase dirs -----
    if cpd_dir.is_dir() and not flag_exists(FLAG_CLEAN, cpd_dir):
        _clean_cpd_incomplete(cpd_dir)
        flag_write(FLAG_CLEAN, cpd_dir)

    # ----- 6.7 Download new competing phases -----
    run_command(
        f"pydefect_vasp mp -e {dopant_str} --e_above_hull 0.0005",
        cwd=str(cpd_dir),
    )

    # ----- 6.8 Set up + submit VASP for new phases -----
    for d in sorted(cpd_dir.iterdir()):
        if not d.is_dir():
            continue
        if vasp_done_check(str(d)):
            continue
        if not vasp_input_check(str(d)):
            pp_args = _vise_flags(info)
            run_command(f'vise vasp_set -x pbesol {pp_args}', cwd=str(d))
        if not vasp_done_check(str(d)):
            logger.info("Phase %s needs VASP: submit and rerun", d.name)

    # Check new phases
    new_phases = [d for d in cpd_dir.iterdir() if d.is_dir()]
    all_done = all(vasp_done_check(str(d)) for d in new_phases)
    if not all_done:
        logger.info("Some CPD phases not done. Submit and rerun.")
        return False

    # ----- 6.9 Rebuild CPD -----
    if not flag_exists(FLAG_CPD, cpd_dir):
        _rebuild_cpd(cpd_dir, info)
        flag_write(FLAG_CPD, cpd_dir)

    flag_write(FLAG_DONE, def_dir)
    logger.info("Stage 6 (doping) complete")
    return True


def _clean_cpd_incomplete(cpd_dir):
    for d in cpd_dir.iterdir():
        if not d.is_dir():
            continue
        if d.name.startswith("."):
            continue
        has_contcar = (d / "CONTCAR").exists()
        has_outcar = (d / "OUTCAR").exists()
        has_done = (d / ".completed").exists()
        if not (has_contcar and has_outcar) and not has_done:
            shutil.rmtree(str(d))
            logger.info("Removed incomplete phase: %s", d.name)


def _rebuild_cpd(cpd_dir, info):
    phases = sorted(d.name for d in cpd_dir.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not phases:
        logger.warning("No phases in cpd/ to rebuild")
        return
    dir_string = " ".join(phases)
    run_command(f"pydefect_vasp mce -d {dir_string}", cwd=str(cpd_dir))

    corr = info.get("gas_corrections", {})
    if corr:
        import yaml
        ce_path = cpd_dir / "composition_energies.yaml"
        if ce_path.exists():
            with open(ce_path) as f:
                data = yaml.safe_load(f)
            for formula, corr_val in corr.items():
                if formula in data:
                    data[formula]["energy"] += corr_val
            with open(ce_path, "w") as f:
                yaml.dump(data, f, default_flow_style=None)

    run_command("pydefect sre", cwd=str(cpd_dir))
    run_command(f'pydefect cv -t "{info["obj"]}"', cwd=str(cpd_dir))
    run_command("pydefect pc", cwd=str(cpd_dir))


def _vise_flags(info):
    parts = []
    if info.get("hubbard_u"):
        parts.append("--options set_hubbard_u True")
    pp = info.get("pp", [])
    if pp:
        parts.append(" ".join(f"--potcar {p}" for p in pp))
    encut = info.get("encut")
    if encut:
        parts.append(f"-uis ENCUT {encut}")
    return " ".join(p for p in parts if p)
