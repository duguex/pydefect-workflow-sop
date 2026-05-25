import os
import shutil
from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists, flag_remove,
    vasp_input_check, vasp_done_check, encut_from_potcar,
)

CPD_DIR = "cpd"
FLAG_MP = ".cpd_mp_done"
FLAG_DONE = ".stage2_done"


def run(project_root, info, auto=False):
    root = Path(project_root)
    cpd_dir = root / CPD_DIR

    if flag_exists(FLAG_DONE, cpd_dir):
        logger.info("Stage 2 already complete")
        return True

    cpd_dir.mkdir(parents=True, exist_ok=True)

    obj = info["obj"]
    dopant = info.get("dopant_element", [])
    intrinsic = list(extract_elements(obj))
    all_elements = list(set(intrinsic + dopant))

    # ----- 2.1 Download competing phase structures -----
    if not flag_exists(FLAG_MP, cpd_dir):
        existing_phases = [p.name for p in cpd_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
        if not existing_phases:
            ele_str = " ".join(all_elements)
            run_command(
                f"pydefect_vasp mp -e {ele_str} --e_above_hull 0.0005",
                cwd=str(cpd_dir),
            )
        else:
            logger.info("CPD phases already exist, skipping download")
        # rename parens to brackets for filesystem safety
        for p in cpd_dir.iterdir():
            if p.is_dir() and ("(" in p.name or ")" in p.name):
                shutil.move(str(p), str(p.parent / p.name.replace("(", "[").replace(")", "]")))
        flag_write(FLAG_MP, cpd_dir)

    # Gather phase directories
    phases = _list_phases(cpd_dir, intrinsic)

    # ----- 2.2 VASP input setup + submit -----
    for name, phase_info in phases.items():
        phase_dir = cpd_dir / name
        if vasp_done_check(str(phase_dir)):
            logger.info("Phase %s: VASP done, skipping", name)
            continue

        # Fix O2 molecule special settings
        if name.startswith("mol_O2"):
            _setup_o2(phase_dir)

        if not vasp_input_check(str(phase_dir)):
            pp_args = _pp_flag(info)
            run_command(f'vise vasp_set -x pbesol {pp_args}', cwd=str(phase_dir))

        if not vasp_done_check(str(phase_dir)):
            if auto:
                _submit_to_crisp(str(phase_dir), f"cpd_{name}")
            logger.info("Phase %s VASP not finished", name)

    # Check if all phases are done
    all_done = all(vasp_done_check(str(cpd_dir / n)) for n in phases)
    if not all_done:
        logger.info("Some CPD phases not finished. Submit and rerun.")
        return False

    # ----- 2.3 Generate CPD -----
    if not (cpd_dir / "target_vertices.yaml").exists():
        _build_cpd(cpd_dir, phases, info)

    flag_write(FLAG_DONE, cpd_dir)
    logger.info("Stage 2 (CPD) complete")
    return True


def extract_elements(formula):
    """Extract element symbols from a chemical formula string."""
    import re
    return re.findall(r'[A-Z][a-z]?', formula)


def _list_phases(cpd_dir, intrinsic):
    """Return dict of {dirname: {formula, mpid}} for relevant phases."""
    result = {}
    for p in sorted(cpd_dir.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        formula = None
        if "_mp-" in name:
            formula, mpid = name.split("_mp-", 1)
        elif name.startswith("mol_"):
            formula = name.split("mol_", 1)[1]
            mpid = None
        else:
            continue
        if not formula:
            continue
        elem = extract_elements(formula)
        if len(elem) == 1 or any(e in intrinsic for e in elem):
            result[name] = {"formula": formula, "mpid": mpid}
    return result


def _build_cpd(cpd_dir, phases, info):
    dir_string = " ".join(phases.keys())
    run_command(f"pydefect_vasp mce -d {dir_string}", cwd=str(cpd_dir))

    # Apply gas corrections
    _apply_gas_corrections(cpd_dir, info.get("gas_corrections", {}))

    run_command("pydefect sre", cwd=str(cpd_dir))

    target = info["obj"]
    _adjust_vertices(cpd_dir, target)

    run_command("pydefect pc", cwd=str(cpd_dir))
    logger.info("CPD built: composition_energies.yaml + target_vertices.yaml + cpd.pdf")


def _apply_gas_corrections(cpd_dir, corrections):
    import yaml
    ce_path = cpd_dir / "composition_energies.yaml"
    if not ce_path.exists():
        return
    with open(ce_path) as f:
        data = yaml.safe_load(f)
    modified = False
    for formula, corr in corrections.items():
        if formula in data:
            data[formula]["energy"] += corr
            modified = True
            logger.info("Applied gas correction %s: +%.3f eV", formula, corr)
    if modified:
        with open(ce_path, "w") as f:
            yaml.dump(data, f, default_flow_style=None)


def _adjust_vertices(cpd_dir, target):
    import yaml
    from pymatgen.core import Composition

    re_path = cpd_dir / "relative_energies.yaml"
    if not re_path.exists():
        return

    with open(re_path) as f:
        re_data = yaml.safe_load(f)

    target_comp = Composition(target)
    target_str = None
    for comp_str in re_data:
        if Composition(comp_str) == target_comp:
            target_str = comp_str
            break
    if target_str is None:
        logger.warning("Target %s not found in relative_energies.yaml", target)
        return

    origin_energy = re_data[target_str]
    current_energy = origin_energy
    run_command(f'pydefect cv -t "{target_str}"', cwd=str(cpd_dir))

    max_iter = 50
    iter_count = 0
    while not (cpd_dir / "chem_pot_diag.json").exists() and iter_count < max_iter:
        current_energy -= 0.01
        re_data[target_str] = current_energy
        with open(re_path, "w") as f:
            yaml.dump(re_data, f, default_flow_style=None)
        run_command(f'pydefect cv -t "{target_str}"', cwd=str(cpd_dir))
        iter_count += 1

    if current_energy != origin_energy:
        logger.info("Energy of %s adjusted from %.3f to %.3f",
                     target_str, origin_energy, current_energy)


def _setup_o2(phase_dir):
    """O2 molecule needs special VASP settings."""
    incar_path = phase_dir / "INCAR"
    if incar_path.exists():
        return
    run_command("vise vasp_set -x pbesol -uis ISPIN 2 NUPDOWN 2", cwd=str(phase_dir))


def _pp_flag(info):
    pp = info.get("pp", [])
    if pp:
        return " " + " ".join(f"--potcar {p}" for p in pp)
    return ""


def _submit_to_crisp(local_dir, task_name):
    try:
        from ..crisp_utils import submit_job
        submit_job(local_dir, task_name)
    except ImportError:
        logger.warning("crisp not available, submit manually: %s", local_dir)
