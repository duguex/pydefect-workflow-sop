import json
import os
from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists,
    vasp_input_check, vasp_done_check,
)

DEFECT_DIR = "defect"
FLAG_SUPERCELL = ".defect_supercell_done"
FLAG_DEFECT_SET = ".defect_set_done"
FLAG_ENTRIES = ".defect_entries_done"
FLAG_DONE = ".stage3_done"


def run(project_root, info, auto=False):
    root = Path(project_root)
    def_dir = root / DEFECT_DIR

    if flag_exists(FLAG_DONE, def_dir):
        logger.info("Stage 3 already complete")
        return True

    def_dir.mkdir(parents=True, exist_ok=True)

    # Find primitive POSCAR
    prim_path = _find_primitive(root, info)

    # ----- 3.1 Supercell -----
    if not flag_exists(FLAG_SUPERCELL, def_dir):
        sc_cfg = info.get("supercell", {})
        max_atoms = sc_cfg.get("max_atoms", 600)
        min_atoms = sc_cfg.get("min_atoms", 200)
        run_command(
            f"pydefect s -p {prim_path} --max_atoms {max_atoms} --min_atoms {min_atoms}",
            cwd=str(def_dir),
        )
        flag_write(FLAG_SUPERCELL, def_dir)

    # ----- 3.2 Defect set -----
    if not flag_exists(FLAG_DEFECT_SET, def_dir):
        dopant = info.get("dopant_element", [])
        if dopant:
            run_command(f"pydefect ds -d {' '.join(dopant)}", cwd=str(def_dir))
        else:
            run_command("pydefect ds", cwd=str(def_dir))
        flag_write(FLAG_DEFECT_SET, def_dir)

    # ----- 3.3 Interstitial (optional) -----
    if info.get("interstitial"):
        _handle_interstitial(def_dir, info.get("iindex", []))

    # ----- 3.4 Defect entries -----
    if not flag_exists(FLAG_ENTRIES, def_dir):
        run_command("pydefect_vasp de", cwd=str(def_dir))
        flag_write(FLAG_ENTRIES, def_dir)

    # ----- 3.5 VASP input for each defect dir -----
    defect_dirs = sorted(d for d in def_dir.iterdir()
                         if d.is_dir() and d.name != "perfect" and not d.name.startswith("."))
    pp_args = _vise_flags(info)

    for dd in defect_dirs:
        if vasp_input_check(str(dd)):
            continue
        run_command(
            f'vise vasp_set -x pbesol -t defect {pp_args}',
            cwd=str(dd),
        )

    # ----- 3.6 Perfect dir VASP input -----
    perfect_dir = def_dir / "perfect"
    if perfect_dir.is_dir() and not vasp_input_check(str(perfect_dir)):
        run_command(
            f'vise vasp_set -x pbesol -t defect {pp_args}',
            cwd=str(perfect_dir),
        )

    flag_write(FLAG_DONE, def_dir)
    logger.info("Stage 3 (defect generation) complete")
    return True


def _find_primitive(root, info):
    candidates = [
        root / "unitcell" / "structure_opt" / "CONTCAR",
        root / "unitcell" / "structure_opt" / "POSCAR",
        root / "cpd" / f"{info['obj']}_mp-" / "CONTCAR",
        root / "cpd" / f"{info['obj']}_mp-" / "POSCAR",
    ]
    for c in candidates:
        if c.exists():
            logger.info("Using primitive: %s", c)
            return str(c.resolve())
    logger.error("Cannot find primitive POSCAR. Running pydefect s requires -p argument.")
    raise FileNotFoundError("No primitive POSCAR found")


def _handle_interstitial(def_dir, iindex):
    dos_extrema = def_dir.parent / "unitcell" / "dos" / "volumetric_data_local_extrema.json"
    if not dos_extrema.exists():
        # try running local_extrema
        dos_dir = def_dir.parent / "unitcell" / "dos"
        if (dos_dir / "AECCAR0").exists():
            run_command(
                "pydefect_vasp le -v AECCAR0 AECCAR1 AECCAR2 -i all_electron_charge",
                cwd=str(dos_dir),
            )
        else:
            logger.warning("AECCAR files not found, skipping interstitial")
            return

    if not dos_extrema.exists():
        logger.warning("No local extrema found, skipping interstitial")
        return

    if not iindex:
        logger.info("Interstitial candidates found. Set 'iindex' in info.json to add them.")
        run_command(f"pydefect_print {dos_extrema}", cwd=str(def_dir))
        return

    interstitial_sites = " ".join(str(i) for i in iindex)
    run_command(
        f"pydefect_util ai --local_extrema {dos_extrema} -i {interstitial_sites}",
        cwd=str(def_dir),
    )
    logger.info("Interstitials added: %s", iindex)


def _vise_flags(info):
    parts = ["--options set_hubbard_u True" if info.get("hubbard_u") else ""]
    pp = info.get("pp", [])
    if pp:
        parts.append(" ".join(f"--potcar {p}" for p in pp))
    encut = info.get("encut")
    if encut:
        parts.append(f"-uis ENCUT {encut}")
    return " ".join(p for p in parts if p)
