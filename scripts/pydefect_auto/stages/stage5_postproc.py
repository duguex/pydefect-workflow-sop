from pathlib import Path

from ..utils import (
    logger, run_command, flag_write, flag_exists,
    sync_output, vasp_done_check,
)

FLAG_DONE = ".stage5_done"


def run(project_root, info, auto=False):
    root = Path(project_root)
    def_dir = root / "defect"
    uc_dir = root / "unitcell"
    cpd_dir = root / "cpd"

    if flag_exists(FLAG_DONE, def_dir):
        logger.info("Stage 5 already complete")
        return True

    if not def_dir.is_dir():
        logger.error("defect/ not found")
        return False

    defect_dirs = _list_defect_dirs(def_dir)
    perfect_dir = def_dir / "perfect"

    if not defect_dirs:
        logger.warning("No defect directories with VASP results found")
        return False

    # ----- 5.1 Sync output/ for each defect dir -----
    for d in defect_dirs:
        sync_output(str(d))
    sync_output(str(perfect_dir))

    # ----- 5.2(a) calc_results -----
    if not _calc_results_done(defect_dirs, perfect_dir):
        _calc_results(defect_dirs, perfect_dir)
    if not _calc_results_done(defect_dirs, perfect_dir):
        logger.error("calc_results generation failed")
        return False

    # ----- 5.2(b) eFNV -----
    if not _efnv_done(def_dir):
        _efnv(def_dir, defect_dirs, uc_dir)
        logger.info("eFNV correction done")

    # ----- 5.2(c) defect_structure_info -----
    dsi_path = defect_dirs[0] / "defect_structure_info.json"
    if not dsi_path.exists():
        run_command(
            f"pydefect dsi -d {' '.join(d.name for d in defect_dirs)}",
            cwd=str(def_dir),
        )

    # ----- 5.2(d) Band edge analysis -----
    bes_path = def_dir / "perfect" / "perfect_band_edge_state.json"
    if not bes_path.exists() and perfect_dir.is_dir():
        run_command("pydefect_vasp pbes -d perfect", cwd=str(def_dir))
    if bes_path.exists():
        dir_names = " ".join(d.name for d in defect_dirs)
        beoi_path = defect_dirs[0] / "band_edge_orbital_infos.json"
        if not beoi_path.exists():
            run_command(
                f"pydefect_vasp beoi -d {dir_names} "
                "-pbes perfect/perfect_band_edge_state.json",
                cwd=str(def_dir),
            )
            run_command(
                f"pydefect bes -d {dir_names} "
                "-pbes perfect/perfect_band_edge_state.json",
                cwd=str(def_dir),
            )

    # ----- 5.2(e) defect_energy_infos -----
    ei_path = defect_dirs[0] / "defect_energy_infos.json"
    if not ei_path.exists():
        _energy_infos(def_dir, defect_dirs, uc_dir, cpd_dir)

    # ----- 5.2(f) Energy summary -----
    summary_path = def_dir / "defect_energy_summary.json"
    if not summary_path.exists():
        _energy_summary(def_dir, defect_dirs, uc_dir, cpd_dir)

    # ----- Plot -----
    _plot(def_dir, cpd_dir)

    flag_write(FLAG_DONE, def_dir)
    logger.info("Stage 5 (post-processing) complete")
    return True


def _list_defect_dirs(def_dir):
    dirs = []
    for d in sorted(def_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name == "perfect":
            continue
        if vasp_done_check(str(d)):
            dirs.append(d)
    return dirs


def _calc_results_done(defect_dirs, perfect_dir):
    for d in list(defect_dirs) + [perfect_dir]:
        if not (d / "calc_results.json").exists():
            return False
    return True


def _calc_results(defect_dirs, perfect_dir):
    all_dirs = defect_dirs + ([perfect_dir] if perfect_dir.is_dir() else [])
    dir_names = " ".join(d.name for d in all_dirs)
    result = run_command(
        f"pydefect_vasp cr -d {dir_names}",
        cwd=str(perfect_dir.parent),
        capture=True,
    )
    if result.returncode == 0:
        logger.info("calc_results: CLI succeeded")
        return

    logger.warning("pydefect_vasp cr failed, trying Python fallback")
    _calc_results_fallback(all_dirs)


def _calc_results_fallback(all_dirs):
    from pymatgen.io.vasp import Vasprun, Outcar
    from pydefect.cli.vasp.make_calc_results import make_calc_results_from_vasp

    ok = 0
    fail = 0
    for d in all_dirs:
        v = d / "vasprun.xml"
        o = d / "OUTCAR"
        if not (v.exists() and o.exists()):
            v2 = d / "output" / "vasprun.xml"
            o2 = d / "output" / "OUTCAR"
            if v2.exists() and o2.exists():
                v, o = v2, o2
            else:
                logger.warning("  %s: no vasprun.xml/OUTCAR found", d.name)
                fail += 1
                continue
        try:
            cr = make_calc_results_from_vasp(Vasprun(str(v)), Outcar(str(o)))
            cr.to_json_file(str(d / "calc_results.json"))
            ok += 1
        except Exception as e:
            logger.error("  Failed %s: %s", d.name, e)
            fail += 1
    logger.info("calc_results: %d ok, %d failed", ok, fail)


def _efnv_done(def_dir):
    return (def_dir / "efnv_correction.json").exists()


def _efnv(def_dir, defect_dirs, uc_dir):
    dir_names = " ".join(d.name for d in defect_dirs)
    pcr = "perfect/calc_results.json"
    u_path = _find_unitcell_yaml(uc_dir)

    result = run_command(
        f"pydefect efnv -d {dir_names} -pcr {pcr} -u {u_path}",
        cwd=str(def_dir),
        capture=True,
    )
    if result.returncode != 0:
        logger.warning("eFNV failed (may be due to missing perfect/calc_results.json)")


def _energy_infos(def_dir, defect_dirs, uc_dir, cpd_dir):
    dir_names = " ".join(d.name for d in defect_dirs)
    u_path = _find_unitcell_yaml(uc_dir)
    s_path = cpd_dir / "standard_energies.yaml"
    if not s_path.exists():
        logger.warning("standard_energies.yaml not found, skipping energy_infos")
        return
    run_command(
        f"pydefect dei -d {dir_names} -pcr perfect/calc_results.json "
        f"-u {u_path} -s {s_path}",
        cwd=str(def_dir),
    )


def _energy_summary(def_dir, defect_dirs, uc_dir, cpd_dir):
    u_path = _find_unitcell_yaml(uc_dir)
    t_path = cpd_dir / "target_vertices.yaml"
    bes_path = def_dir / "perfect" / "perfect_band_edge_state.json"
    if not bes_path.exists():
        logger.warning("perfect_band_edge_state.json not found, using --no-bes")
        run_command(
            f"pydefect des -d *_* -u {u_path} -t {t_path}",
            cwd=str(def_dir),
        )
    else:
        run_command(
            f"pydefect des -d *_* -u {u_path} -pbes {bes_path} -t {t_path}",
            cwd=str(def_dir),
        )
    run_command(
        "pydefect cs -d *_* -pcr perfect/calc_results.json",
        cwd=str(def_dir),
    )


def _plot(def_dir, cpd_dir):
    summary_path = def_dir / "defect_energy_summary.json"
    if not summary_path.exists():
        logger.warning("defect_energy_summary.json not found, skipping plot")
        return
    t_path = cpd_dir / "target_vertices.yaml"
    if not t_path.exists():
        logger.warning("target_vertices.yaml not found, skipping plot")
        return
    import yaml
    with open(t_path) as f:
        tv = yaml.safe_load(f)
    skip_keys = {"Fermi level", "target"}
    vertices = [k for k in tv if k not in skip_keys]
    for vertex in vertices:
        run_command(
            f"pydefect pe -d {summary_path} -l {vertex}",
            cwd=str(def_dir),
        )


def _find_unitcell_yaml(uc_dir):
    candidates = [
        uc_dir / "unitcell.yaml",
        uc_dir.parent / "unitcell.yaml",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    logger.warning("unitcell.yaml not found")
    return "unitcell.yaml"
