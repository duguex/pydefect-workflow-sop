"""Defect stage.

Supercell construction, defect enumeration, generation, VASP input
preparation, complex defect combination, and post-processing.

This is the most complex stage, ported from ``pydefect_logic.py:352-637``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

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
    DefectResult,
    PipelineState,
    StateStore,
    StepStatus,
)

logger = logging.getLogger(__name__)

_DEFECT_DIR = "defect"
_DOS_EXTREMA = "../unitcell/dos/volumetric_data_local_extrema.json"
_CHEM_POT_PATH = "../cpd/target_vertices.yaml"



def _prepare_all_inputs(
    defect_root: Path,
    target_dir: Path,
    config: PipelineConfig,
) -> None:
    """Build supercell, enumerate defects, generate VASP inputs (all local).

    Uses POSCAR (not CONTCAR) from *target_dir* — VASP hasn't run yet
    at this point in the pipeline.  The unrelaxed lattice is fine for
    supercell sizing; the defect VASP calculations will relax the atoms.
    """
    defect_root.mkdir(parents=True, exist_ok=True)

    poscar = target_dir / "POSCAR"
    if not poscar.is_file():
        raise FileNotFoundError(f"Target POSCAR not found at {poscar}.")
    uc_contcar = poscar  # use POSCAR as stand-in for CONTCAR

    _build_supercell(defect_root, uc_contcar, config)
    _handle_interstitials(defect_root, config)
    _generate_defect_list(defect_root, config)
    _generate_structures(defect_root)
    _generate_vasp_inputs(defect_root, config)



def _get_calc_dirs(defect_root: Path) -> list[Path]:
    """Return [perfect, Va_Si1_0, Va_Si1_-1, ...] for VASP submission."""
    dirs: list[Path] = []
    perfect_dir = defect_root / "perfect"
    if perfect_dir.is_dir():
        dirs.append(perfect_dir)
    for child in sorted(defect_root.iterdir()):
        if child.is_dir() and child.name != "perfect" and _vasp_input_ready(child):
            dirs.append(child)
    return dirs
def run_defect(
    config: PipelineConfig,
    state: PipelineState,
) -> DefectResult:
    """Execute (or skip) the Defect stage.

    Requires completed CPD and Unitcell stages.
    """
    if state.defect_status == StepStatus.DONE and state.defect_result is not None:
        logger.info("Defect stage already complete, skipping.")
        return state.defect_result

    if state.cpd_result is None or state.unitcell_result is None:
        raise RuntimeError("CPD and Unitcell stages must complete before defect stage.")

    root = config.root
    defect_root = root / _DEFECT_DIR
    defect_root.mkdir(parents=True, exist_ok=True)

    state.defect_status = StepStatus.RUNNING
    StateStore.save(state)

    # Path to the fully-relaxed unitcell CONTCAR
    uc_contcar = (
        root / "unitcell" / "structure_opt" / "CONTCAR"
    )
    if not uc_contcar.is_file():
        raise FileNotFoundError(
            f"Unitcell CONTCAR not found at {uc_contcar}. "
            "Run the unitcell stage first."
        )

    # ── 1. Build supercell ──────────────────────────────────────────
    _build_supercell(defect_root, uc_contcar, config)

    # ── 2. Handle interstitials ─────────────────────────────────────
    _handle_interstitials(defect_root, config)

    # ── 3. Generate defect list ─────────────────────────────────────
    _generate_defect_list(defect_root, config)

    # ── 4. Generate defect structures ───────────────────────────────
    _generate_structures(defect_root)

    # ── 5. Generate VASP inputs for each defect ─────────────────────
    _generate_vasp_inputs(defect_root, config)

    # ── 6. Complex defect construction (order >= 2) ──────────────────
    if config.complex_defect_order >= 2:
        _construct_complex_defects(defect_root, config)

    # ── 7. Run VASP: perfect first, then all defects ────────────────
    _run_vasp_calculations(defect_root)

    # ── 8. Post-processing ──────────────────────────────────────────
    _run_post_processing(defect_root, root, config)

    result = DefectResult(
        defect_energy_summary_path=(defect_root / "defect_energy_summary.json").resolve(),
        calc_summary_path=(defect_root / "calc_summary.json").resolve(),
    )

    state.defect_result = result
    state.defect_status = StepStatus.DONE
    StateStore.save(state)
    logger.info("Defect stage complete.")
    return result


# ══════════════════════════════════════════════════════════════════════════
# Stage steps
# ══════════════════════════════════════════════════════════════════════════


def _build_supercell(defect_root: Path, uc_contcar: Path, config: PipelineConfig) -> None:
    """Construct the supercell via pydefect."""
    sc_info = defect_root / "supercell_info.json"
    if sc_info.is_file():
        logger.info("Supercell info already exists, skipping supercell construction.")
        return

    cmd = (
        f"pydefect s -p {uc_contcar} "
        f"--max_atoms {config.supercell_max_atoms} "
        f"--min_atoms {config.supercell_min_atoms}"
    )
    run_local(cmd, cwd=defect_root, timeout=600)


def _handle_interstitials(defect_root: Path, config: PipelineConfig) -> None:
    """Handle interstitial site placement if enabled."""
    if not config.interstitial:
        return

    sc_info = defect_root / "supercell_info.json"
    if not sc_info.is_file():
        return

    with open(sc_info) as f:
        sc_data = json.load(f)

    interstitials = sc_data.get("interstitials", [])
    if interstitials:
        logger.info("Interstitials already defined in supercell_info.json, skipping.")
        return

    dos_extrema = (config.root / _DOS_EXTREMA).resolve()
    if not dos_extrema.is_file():
        logger.warning("DOS extrema file not found at %s, skipping interstitial placement.", dos_extrema)
        return

    logger.info("Candidates for interstitials (from %s):", dos_extrema)
    run_local(f"pydefect_print {dos_extrema}", cwd=defect_root)

    if not config.interstitial_indices:
        raise RuntimeError(
            "Interstitials requested but no `interstitial_indices` provided. "
            "Check the candidate list above and set "
            "`defects.interstitial_indices` in plan.yaml."
        )

    interstitial_sites = " ".join(config.interstitial_indices)
    run_local(
        f"pydefect_util ai --local_extrema {dos_extrema} -i {interstitial_sites}",
        cwd=defect_root,
    )


def _generate_defect_list(defect_root: Path, config: PipelineConfig) -> None:
    """Run ``pydefect ds`` to produce ``defect_in.yaml``."""
    defect_in = defect_root / "defect_in.yaml"
    if defect_in.is_file():
        logger.info("defect_in.yaml already exists, skipping defect list generation.")
        return

    if config.dopant_elements:
        cmd = f"pydefect ds -d {' '.join(config.dopant_elements)}"
    else:
        cmd = "pydefect ds"
    run_local(cmd, cwd=defect_root)

    if defect_in.is_file():
        with open(defect_in) as f:
            data = yaml.safe_load(f)
        logger.info("Defect list:")
        for defect, valence in (data or {}).items():
            logger.info("  %s: %s", defect, valence)


def _generate_structures(defect_root: Path) -> None:
    """Run ``pydefect_vasp de`` to generate individual defect structures."""
    flag = defect_root / "defect_generate_flag"
    if flag.is_file():
        logger.info("Defect structures already generated, skipping.")
        return

    run_local("pydefect_vasp de", cwd=defect_root)
    flag.touch()


def _generate_vasp_inputs(defect_root: Path, config: PipelineConfig) -> None:
    """Generate VASP inputs for every defect directory (including perfect)."""
    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    encut_opt = f"ENCUT {config.encut}" if config.encut else ""
    cmd = (
        f"vise vs -x {config.functional} -t defect -k 0.1 "
        f"--options set_hubbard_u True -uis NSW 50 SIGMA 0.02 LORBIT 11 "
        f"{encut_opt} {pp_opt}"
    )

    for child in defect_root.iterdir():
        if not child.is_dir():
            continue
        if _vasp_input_ready(child):
            continue
        logger.debug("Generating VASP inputs for %s", child.name)
        run_local(cmd, cwd=child, timeout=300)


def _construct_complex_defects(defect_root: Path, config: PipelineConfig) -> None:
    """Build combined defects via ``pydefect.complex.ComplexDefectMaker``.

    Delegates to the library for geometry enumeration, composition
    assignment, structure generation, and deduplication.
    """
    complex_flag = defect_root / "complex_flag"
    if complex_flag.is_file():
        logger.info("Complex defects already constructed, skipping.")
        return

    sc_info = defect_root / "supercell_info.json"
    if not sc_info.is_file():
        logger.warning("supercell_info.json not found at %s, skipping complex defects.", sc_info)
        return

    from pydefect.complex import ComplexDefectMaker

    maker = ComplexDefectMaker.from_supercell_info(
        str(sc_info),
        dopants=config.dopant_elements or None,
        max_distance=config.remote_cutoff,
    )

    for order in range(2, config.complex_defect_order + 1):
        logger.info("Generating complex defects of order %d", order)
        geoms = maker.make_all_n_body(n=order)
        entries = maker.generate_entries(order, dopants=config.dopant_elements or None)
        maker.write(entries, str(defect_root), merge=True)

def _vasp_completed(path: Path) -> bool:
    """Check OUTCAR in the work directory or crisp's output/ subdir."""
    return (path / "OUTCAR").is_file() or (path / "output" / "OUTCAR").is_file()


def _vasp_job_done(path: Path) -> bool:
    """OUTCAR-based ionic convergence check (head + tail, ~96 KB)."""
    import re as _re

    outcar: Path | None = None
    for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
        if cand.is_file():
            outcar = cand
            break
    if outcar is None:
        return False

    try:
        text = outcar.read_text()
    except Exception:
        return False

    if "General timing and accounting" not in text[-4096:]:
        return False

    # Parse forces from last TOTAL-FORCE block
    idx = text.rfind("TOTAL-FORCE (eV/Angst)")
    if idx < 0:
        return False
    head = text[:16384]
    m_efg = _re.search(r"EDIFFG\s*=\s*([-\d.]+)", head)
    efg = abs(float(m_efg.group(1))) if m_efg else 0.03
    max_f = 0.0
    for line in text[idx:].splitlines()[2:]:
        parts = line.strip().split()
        if len(parts) < 6:
            break
        try:
            max_f = max(max_f, abs(float(parts[3])), abs(float(parts[4])), abs(float(parts[5])))
        except ValueError:
            break
    return max_f < efg


def _vasp_restart_from_contcar(path: Path) -> None:
    """Copy CONTCAR → POSCAR, set ISTART=1, increase NSW for restart."""
    contcar = path / "CONTCAR"
    if not contcar.is_file():
        return
    import shutil
    shutil.copy2(str(contcar), str(path / "POSCAR"))

    incar = path / "INCAR"
    if not incar.is_file():
        return
    text = incar.read_text()
    lines = text.splitlines()
    new_lines = []
    has_istart = False
    for line in lines:
        if line.strip().startswith("ISTART"):
            new_lines.append("ISTART = 1")
            has_istart = True
        elif line.strip().startswith("NSW"):
            import re as _re
            nsw_val = 50
            m = _re.search(r"\d+", line)
            if m:
                nsw_val = int(m.group()) * 2
            new_lines.append(f"NSW = {nsw_val}")
        else:
            new_lines.append(line)
    if not has_istart:
        new_lines.append("ISTART = 1")
    incar.write_text("\n".join(new_lines) + "\n")

def _run_vasp_calculations(defect_root: Path) -> None:
    """Submit perfect + all defect VASP jobs, with CONTCAR restart for timeouts.

    Loops until all jobs converge or no more progress (max_f stops decreasing).
    """
    import re as _re

    perfect_dir = defect_root / "perfect"
    if not perfect_dir.is_dir():
        raise RuntimeError(
            f"Perfect supercell directory not found at {perfect_dir}."
        )

    def _collect_jobs() -> list[Path]:
        result = []
        if not _vasp_job_done(perfect_dir):
            result.append(perfect_dir)
        for child in sorted(defect_root.iterdir()):
            if not child.is_dir() or child.name == "perfect":
                continue
            if not _vasp_input_ready(child):
                continue
            if not _vasp_job_done(child):
                result.append(child)
        return result

    def _max_f(path: Path) -> float:
        """Extract max force from OUTCAR (0 if unavailable)."""
        for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
            if cand.is_file():
                text = cand.read_text()
                idx = text.rfind("TOTAL-FORCE (eV/Angst)")
                if idx < 0:
                    return 0.0
                mf = 0.0
                for line in text[idx:].splitlines()[2:]:
                    p = line.strip().split()
                    if len(p) < 6:
                        break
                    try:
                        mf = max(mf, abs(float(p[3])), abs(float(p[4])), abs(float(p[5])))
                    except ValueError:
                        break
                return mf
        return 0.0

    prev_forces: dict[str, float] = {}
    stalled: set[str] = set()

    for attempt in range(20):
        dirs = _collect_jobs()
        if not dirs:
            break

        for d in dirs:
            if (d / "CONTCAR").is_file() and not _vasp_job_done(d):
                dirname = d.name
                old_f = prev_forces.get(dirname, 999.0)
                cur_f = _max_f(d)
                if cur_f > 0 and cur_f >= old_f * 0.95:
                    stalled.add(dirname)
                    logger.info(
                        "No progress for %s (max_f %.4f -> %.4f), marking stalled",
                        dirname, old_f, cur_f,
                    )
                else:
                    stalled.discard(dirname)
                prev_forces[dirname] = cur_f

                if dirname not in stalled:
                    logger.info(
                        "Restarting %s from CONTCAR (attempt %d, max_f=%.4f)",
                        dirname, attempt + 1, cur_f,
                    )
                    _vasp_restart_from_contcar(d)
                else:
                    logger.warning("Skipping %s: stalled (max_f=%.4f)", dirname, cur_f)

        # Only submit non-stalled jobs
        active = [d for d in dirs if d.name not in stalled]
        if not active:
            logger.info("All remaining jobs stalled. Giving up.")
            break
        logger.info("Submitting %d VASP job(s) (attempt %d)", len(active), attempt + 1)
        jobs = [submit_vasp(d.resolve()) for d in active]

        # 并行等待全部完成，不抛异常
        from vasp_sop.core.jobs import wait_all as _wait_all
        pending = list(jobs)
        while pending:
            for j in list(pending):
                rc = j.poll()
                if rc is not None:
                    pending.remove(j)
                    if rc != 0:
                        logger.warning("VASP failed in %s (exit %d)", j.work_dir.name, rc)
                    else:
                        move_crisp_outputs(j.work_dir)
            if pending:
                import time
                time.sleep(60)

    still_incomplete = [d.name for d in _collect_jobs()]
    if still_incomplete:
        logger.warning(
            "Defect VASP still incomplete after %d attempts: %s",
            attempt + 1, ", ".join(still_incomplete),
        )

def _run_post_processing(
    defect_root: Path,
    project_root: Path,
    config: PipelineConfig,
) -> None:
    """Run the defect energetics post-processing pipeline.

    Ported from ``pydefect_logic.py:583-637``.
    """
    summary_json = defect_root / "defect_energy_summary.json"
    if summary_json.is_file():
        logger.info("Defect energy summary already exists, skipping post-processing.")
        return

    perfect_dir = defect_root / "perfect"
    unitcell_dir = project_root / "unitcell"
    cpd_dir = project_root / "cpd"

    unitcell_yaml = unitcell_dir / "unitcell.yaml"
    standard_energies = cpd_dir / "standard_energies.yaml"
    target_vertices = cpd_dir / "target_vertices.yaml"

    # ── cr (calc_results) ───────────────────────────────────────────
    run_local("pydefect_vasp cr -d *_* perfect", cwd=defect_root)

    # ── efnv (energy-free NV) correction ────────────────────────────
    if perfect_dir.is_dir():
        perfect_cr = perfect_dir / "calc_results.json"
        if perfect_cr.is_file():
            run_local(
                f"pydefect efnv -d *_* -pcr {perfect_cr} -u {unitcell_yaml}",
                cwd=defect_root,
            )

    # ── dsi (defect structure info) ─────────────────────────────────
    run_local("pydefect dsi -d *_*", cwd=defect_root)

    # ── dvf (defect volume fraction) ────────────────────────────────
    run_local("pydefect_util dvf -d *_*", cwd=defect_root)

    # ── pbes (perfect band-edge state) ──────────────────────────────
    run_local("pydefect_vasp pbes -d perfect", cwd=defect_root)

    # ── beoi (band-edge orbital info) ───────────────────────────────
    pbes_json = perfect_dir / "perfect_band_edge_state.json"
    if pbes_json.is_file():
        run_local(
            f"pydefect_vasp beoi -d *_* -pbes {pbes_json}",
            cwd=defect_root,
        )

    # ── bes (band-edge state) ───────────────────────────────────────
    if pbes_json.is_file():
        run_local(
            f"pydefect bes -d *_* -pbes {pbes_json}",
            cwd=defect_root,
        )

    # ── dei (defect energy info) ───────────────────────────────────
    perfect_cr = perfect_dir / "calc_results.json"
    if perfect_cr.is_file() and unitcell_yaml.is_file() and standard_energies.is_file():
        defect_dirs = sorted(
            d for d in defect_root.iterdir()
            if d.is_dir() and "_" in d.name and d.name != "perfect"
        )
        corrected = [d for d in defect_dirs if (d / "correction.json").is_file()]
        missing = [d for d in defect_dirs if not (d / "correction.json").is_file()]
        if missing:
            logger.warning(
                "Skipping %d defect(s) missing correction.json: %s",
                len(missing), ", ".join(d.name for d in missing),
            )
        if corrected:
            targets = " ".join(d.name for d in corrected)
            run_local(
                f"pydefect dei -d {targets} -pcr {perfect_cr} "
                f"-u {unitcell_yaml} -s {standard_energies}",
                cwd=defect_root,
            )

    # ── des (defect energy summary) ─────────────────────────────────
    if unitcell_yaml.is_file() and pbes_json.is_file() and target_vertices.is_file():
        run_local(
            f"pydefect des -d *_* -u {unitcell_yaml} "
            f"-pbes {pbes_json} -t {target_vertices}",
            cwd=defect_root,
        )

    # ── cs (calc summary) ───────────────────────────────────────────
    if perfect_cr.is_file():
        run_local(
            f"pydefect cs -d *_* -pcr {perfect_cr}",
            cwd=defect_root,
        )

    # ── pe (phase equilibrium) for each vertex ──────────────────────
    if target_vertices.is_file():
        with open(target_vertices) as f:
            tv_data = yaml.safe_load(f) or {}
        vertices = [k for k in tv_data if k != "target"]
        if len(vertices) == 1:
            logger.info("Single-element system: skipping pydefect pe plot.")
        else:
            for vertex in vertices:
                run_local(
                    f"pydefect pe -d defect_energy_summary.json -l {vertex}",
                    cwd=defect_root,
                )
