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
            "Check the above candidate list and set `interstitial_indices` in config."
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
    cmd = (
        f"vise vs -x {config.functional} -t defect -k 0.1 "
        f"--options set_hubbard_u True -uis NSW 50 SIGMA 0.02 LORBIT 11 {pp_opt}"
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

    complex_flag.touch()
    logger.info("Complex defects constructed.")


def _run_vasp_calculations(defect_root: Path) -> None:
    """Submit perfect + all defect VASP jobs in one batch.

    Perfect and defects have no cross-dependency for the VASP run itself;
    all are submitted simultaneously.
    """
    perfect_dir = defect_root / "perfect"
    if not perfect_dir.is_dir():
        raise RuntimeError(
            f"Perfect supercell directory not found at {perfect_dir}."
        )

    jobs = [submit_vasp(perfect_dir.resolve())]

    for child in sorted(defect_root.iterdir()):
        if not child.is_dir() or child.name == "perfect":
            continue
        if not _vasp_input_ready(child):
            logger.warning("Skipping %s: VASP inputs not ready", child.name)
            continue
        logger.info("Defect: submitting VASP for %s", child.name)
        jobs.append(submit_vasp(child.resolve()))

    logger.info("Defect: waiting for %d VASP jobs", len(jobs))
    wait_all(jobs)
    for j in jobs:
        move_crisp_outputs(j.work_dir)


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
        run_local(
            f"pydefect dei -d *_* -pcr {perfect_cr} "
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
        # Extract vertex composition keys (skip the "target" metadata key)
        vertices = [k for k in tv_data if k != "target"]
        # Skip plotting for single-element systems (pydefect expects
        # element-wise chem_pot dict, but TargetVertex stores a scalar)
        if len(vertices) == 1:
            logger.info("Single-element system: skipping pydefect pe plot.")
        else:
            for vertex in vertices:
                run_local(
                    f"pydefect pe -d defect_energy_summary.json -l {vertex}",
                    cwd=defect_root,
                )
