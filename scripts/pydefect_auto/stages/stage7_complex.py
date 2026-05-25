from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists,
    vasp_input_check, vasp_done_check,
)

FLAG_GENERATED = ".complex_generated"
FLAG_VASP_INPUT = ".complex_vasp_input"
FLAG_ENTRY_FIX = ".complex_entry_fix"
FLAG_DONE = ".stage7_done"


def run(project_root, info, auto=False):
    root = Path(project_root)
    def_dir = root / "defect"

    if flag_exists(FLAG_DONE, def_dir):
        logger.info("Stage 7 already complete")
        return True

    complex_n = info.get("complex_defect", 1)
    if complex_n <= 1:
        logger.info("No complex defects requested (complex_defect <= 1)")
        return True

    si_path = def_dir / "supercell_info.json"
    if not si_path.exists():
        logger.error("supercell_info.json not found. Run Stage 3 first.")
        return False

    dopants = info.get("dopant_element", [])
    charges = info.get("charges", [0])

    # ----- 7.1 Generate complex defect entries -----
    if not flag_exists(FLAG_GENERATED, def_dir):
        _generate_complex(def_dir, si_path, dopants, charges, complex_n, info)
        flag_write(FLAG_GENERATED, def_dir)

    # ----- 7.2 VASP input for complex defect dirs -----
    if not flag_exists(FLAG_VASP_INPUT, def_dir):
        _setup_vasp_input(def_dir, info)
        flag_write(FLAG_VASP_INPUT, def_dir)

    # ----- 7.3 Fix defect_entry.json monty serialization -----
    if not flag_exists(FLAG_ENTRY_FIX, def_dir):
        _fix_defect_entries(def_dir)
        flag_write(FLAG_ENTRY_FIX, def_dir)

    flag_write(FLAG_DONE, def_dir)
    logger.info("Stage 7 (complex defects) complete")
    return True


def _generate_complex(def_dir, si_path, dopants, charges, complex_n, info):
    try:
        from pydefect_complex import ComplexDefectMaker
    except ImportError:
        logger.error("pydefect_complex not installed. Run: pip install -e ~/pydefect-complex")
        return

    maker = ComplexDefectMaker.from_supercell_info(
        str(si_path), dopants=dopants,
        max_distance=info.get("remote", 5.0), charges=charges,
    )

    for n in range(2, complex_n + 1):
        logger.info("Generating N=%d complex defects", n)
        if n >= 4:
            maker.enumerate_geometries(N_max=n)
        else:
            maker.make_all_n_body(n)
        entries = maker.generate_entries(n_or_geometries=n)
        entries = [e for e in entries if e.point_group != "C1"]
        entries = [
            e for e in entries
            if sum(1 for a in e.complex_defect.in_elements if a) <= 2
        ]
        maker.write(entries, str(def_dir.resolve()), merge=True)
        logger.info("N=%d: %d entries written", n, len(entries))


def _setup_vasp_input(def_dir, info):
    complex_dirs = [d for d in def_dir.iterdir()
                    if d.is_dir() and "+" in d.name and not d.name.startswith(".")]
    if not complex_dirs:
        logger.warning("No complex defect directories found")
        return

    # Find a reference single defect for KPOINTS copying
    ref_dir = _find_ref_dir(def_dir)
    pp_args = _vise_flags(info)

    for d in complex_dirs:
        if vasp_input_check(str(d)):
            logger.info("  %s: inputs ready", d.name)
            continue
        run_command(
            f'vise vasp_set -x pbesol -t defect {pp_args}',
            cwd=str(d),
        )
        if ref_dir:
            kpoints_src = ref_dir / "KPOINTS"
            if kpoints_src.exists():
                from shutil import copy
                copy(str(kpoints_src), str(d / "KPOINTS"))
                logger.info("  %s: KPOINTS copied from %s", d.name, ref_dir.name)

    logger.info("VASP inputs set for %d complex defect dirs", len(complex_dirs))


def _fix_defect_entries(def_dir):
    try:
        from pydefect.input_maker.defect_entry import DefectEntry
        from monty.serialization import dumpfn
    except ImportError:
        logger.warning("pydefect or monty not available, skipping defect_entry fix")
        return

    fixed = 0
    for d in sorted(def_dir.iterdir()):
        if not d.is_dir() or "+" not in d.name:
            continue
        de_path = d / "defect_entry.json"
        if not de_path.exists():
            continue
        try:
            import json
            with open(de_path) as f:
                data = json.load(f)
            if "@module" in data and "@class" in data:
                continue
            name = data.get("name", d.name.split("_")[0])
            charge = int(d.name.split("_")[-1]) if "_" in d.name else 0
            de = DefectEntry(
                name=name, charge=charge,
                structure=None, site_symmetry="1",
                defect_center=(0.0, 0.0, 0.0),
                perturbed_sites=[], perturbed_site_symmetry="1",
            )
            dumpfn(de, str(de_path))
            fixed += 1
            logger.info("  Fixed defect_entry.json: %s", d.name)
        except Exception as e:
            logger.error("  Failed %s: %s", d.name, e)

    logger.info("Fixed %d defect_entry.json files", fixed)


def _find_ref_dir(def_dir):
    for d in sorted(def_dir.iterdir()):
        if d.is_dir() and "_" in d.name and "+" not in d.name and d.name != "perfect":
            return d
    return None


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
