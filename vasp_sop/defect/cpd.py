"""CPD (chemical-potential diagram) stage.

Fetches competing phases from Materials Project, runs VASP calculations
for each, and constructs the chemical-potential diagram with pydefect.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pymatgen.core import Composition

from vasp_sop.materials import (
    fetch_candidate_phases,
    list_phases,
    get_intrinsic_elements,
)
from vasp_sop.vasp.io import check_complete, prepare_inputs
from vasp_sop.core.jobs import (
    VaspJob,
    move_crisp_outputs,
    submit_vasp,
    wait_all,
    run_local,
)
from vasp_sop.core.state import (
    CpdResult,
    PipelineState,
    StateStore,
    StepStatus,
)

logger = logging.getLogger(__name__)

_CPD_DIR = "cpd"
_MP_FLAG = "mp_flag"
_TARGET_VERTICES = "target_vertices.yaml"
_COMPOSITION_ENERGIES = "composition_energies.yaml"
_RELATIVE_ENERGIES = "relative_energies.yaml"
_STANDARD_ENERGIES = "standard_energies.yaml"
_CHEM_POT_DIAG = "chem_pot_diag.json"




def _get_target_composition(formula: str):
    from pymatgen.core import Composition
    return Composition(formula)

def _get_cpd_info(cpd_root: Path, intrinsic_elements: list[str]) -> dict[str, dict]:
    return list_phases(cpd_root, intrinsic_elements)


def _split_target(
    cpd_root: Path,
    cpd_info: dict[str, dict],
    formula: str,
) -> tuple[Path, list[Path]]:
    """Return (target_dir, other_dirs)."""
    from pymatgen.core import Composition
    target_comp = Composition(formula)
    target: Path | None = None
    others: list[Path] = []
    for dirname, info in cpd_info.items():
        p = (cpd_root / dirname).resolve()
        if Composition(info["formula"]) == target_comp:
            target = p
        else:
            others.append(p)
    if target is None:
        raise ValueError(f"Target {formula} not found in CPD dirs: {list(cpd_info)}")
    return target, others


def _submit_remaining(
    cpd_root: Path,
    dirs: list[Path],
    config: PipelineConfig,
) -> list[VaspJob]:
    """Submit VASP for competing phases (not target — already submitted)."""
    jobs: list[VaspJob] = []
    for d in dirs:
        if check_complete(d):
            logger.info("Skipping %s: OUTCAR exists", d.name)
            continue
        logger.info("CPD: submitting VASP for %s", d.name)
        jobs.append(submit_vasp(d))
    return jobs


def run_cpd(
    config: PipelineConfig,
    state: PipelineState,
) -> CpdResult:
    """Execute (or skip) the CPD stage.

    Returns the result from the state if already done, otherwise runs
    the full CPD workflow and persists the updated state.
    """
    if state.cpd_status == StepStatus.DONE and state.cpd_result is not None:
        logger.info("CPD stage already complete, skipping.")
        return state.cpd_result

    root = config.root
    cpd_root = root / _CPD_DIR
    cpd_root.mkdir(parents=True, exist_ok=True)

    state.cpd_status = StepStatus.RUNNING
    StateStore.save(state)

    intrinsic_elements = get_intrinsic_elements(config.formula)
    logger.info("CPD: intrinsic elements = %s", intrinsic_elements)

    # ── 1. Fetch competing phases from Materials Project ─────────────
    elements = intrinsic_elements + config.dopant_elements
    fetch_candidate_phases(elements, cpd_root)

    # Build cpd_info dict {dirname: {formula, mpid}}
    cpd_info = list_phases(cpd_root, intrinsic_elements)

    target_composition = Composition(config.formula)
    unitcell_path: Optional[Path] = None
    for dirname, info in cpd_info.items():
        comp = Composition(info["formula"])
        if comp == target_composition:
            unitcell_path = (cpd_root / dirname).resolve()
            break
    if unitcell_path is None:
        raise ValueError(
            f"Cannot find CPD directory for formula {config.formula} "
            f"in {cpd_root}. Available: {list(cpd_info.keys())}"
        )
    logger.info("CPD: target unitcell path = %s", unitcell_path)

    to_be_calculated = list(cpd_info.keys())
    logger.info("CPD: %d phases to calculate", len(to_be_calculated))
    # ── 2. Generate VASP inputs and run calculations (parallel) ──────
    jobs = _submit_cpd_batch(cpd_root, to_be_calculated, config)
    if jobs:
        logger.info("CPD: waiting for %d VASP jobs", len(jobs))
        wait_all(jobs)
        for j in jobs:
            move_crisp_outputs(j.work_dir)

    # ── 3. Post-processing: composition energies + corrections ───────
    compute_chemical_potentials(cpd_root, config, target_composition)

    # ── 4. Build result ──────────────────────────────────────────────
    result = CpdResult(
        unitcell_path=unitcell_path,
        chem_pot_path=(cpd_root / _TARGET_VERTICES).resolve(),
        standard_energies_path=(cpd_root / _STANDARD_ENERGIES).resolve(),
    )

    state.cpd_result = result
    state.cpd_status = StepStatus.DONE
    StateStore.save(state)
    logger.info("CPD stage complete.")
    return result


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════



def _submit_cpd_batch(
    cpd_root: Path,
    dirnames: list[str],
    config: PipelineConfig,
) -> list[VaspJob]:
    """Generate VASP inputs and submit all CPD phases as a parallel batch.

    Skips directories where OUTCAR already exists (resume safety).
    """
    jobs: list[VaspJob] = []
    for d in dirnames:
        work_dir = cpd_root / d
        prepare_inputs(work_dir, config)
        outcar = work_dir / "OUTCAR"
        if outcar.is_file():
            logger.info("Skipping %s: OUTCAR exists", d)
            continue
        logger.info("CPD: submitting VASP for %s", d)
        jobs.append(submit_vasp(work_dir.resolve()))
    return jobs

def compute_chemical_potentials(
    cpd_root: Path,
    config: PipelineConfig,
    target_composition: Composition,
) -> None:
    """Run pydefect post-processing steps for the CPD stage."""
    target_vertices = cpd_root / _TARGET_VERTICES
    composition_energies = cpd_root / _COMPOSITION_ENERGIES
    relative_energies = cpd_root / _RELATIVE_ENERGIES
    chem_pot_diag = cpd_root / _CHEM_POT_DIAG

    # ── composition_energies.yaml ────────────────────────────────────
    if not target_vertices.is_file():
        # Collect all cpd_info directories into a space-separated string
        dirs = " ".join(
            p.name for p in cpd_root.iterdir()
            if p.is_dir()
        )
        # Escape parentheses for shell
        escaped = dirs.replace("(", r"\(").replace(")", r"\)")
        run_local(f"pydefect_vasp mce -d {escaped}", cwd=cpd_root)

        # Apply molecule corrections
        if composition_energies.is_file():
            apply_molecule_corrections(
                composition_energies, config.molecule_corrections
            )

    # ── relative_energies.yaml / standard_energies.yaml ──────────────
    if not target_vertices.is_file():
        run_local("pydefect sre", cwd=cpd_root)

    # ── Chem-pot diagram (energy adjustment for unstable phases) ─────
    if not target_vertices.is_file():
        adjust_unstable_phase(
            cpd_root, relative_energies, target_composition, config
        )

    # ── Phase-diagram plot (skip for single-element — nothing to plot) ─
    if len(target_composition.as_dict()) > 1 and not (cpd_root / "cpd.pdf").is_file():
        n_elements = len(target_composition.elements)
        if n_elements > 3:
            # pydefect's pc command only supports 2D / 3D chem-pot diagrams.
            # For 4+ element systems the chem-pot polytope is > 3D and cannot
            # be plotted with the current matplotlib plotters. Skip the plot
            # but leave the rest of the CPD artefacts intact.
            # See issues/0002-skip-4d-cpd-diagram.md.
            logger.warning(
                "%s: %d-element system, skipping pydefect pc "
                "(only 2D/3D chem-pot diagrams are supported).",
                cpd_root.name, n_elements,
            )
        else:
            # Plotting is a diagnostic only — a failure here must NOT block
            # the rest of the pipeline. See issues/0002-skip-4d-cpd-diagram.md.
            try:
                run_local("pydefect pc", cwd=cpd_root)
            except Exception as exc:
                logger.warning(
                    "pydefect pc failed for %s (non-fatal): %s",
                    cpd_root.name, exc,
                )


def apply_molecule_corrections(
    comp_energies_path: Path,
    corrections: dict[str, float],
) -> None:
    """Apply empirical energy corrections to diatomic gas molecules."""
    with open(comp_energies_path) as f:
        data = yaml.safe_load(f) or {}

    changed = False
    for formula, correction in corrections.items():
        if formula in data:
            old = data[formula].get("energy", 0.0)
            data[formula]["energy"] = old + correction
            logger.info(
                "Corrected %s energy: %.4f -> %.4f", formula, old, old + correction
            )
            changed = True

    if changed:
        with open(comp_energies_path, "w") as f:
            yaml.dump(data, f, default_flow_style=None)


def adjust_unstable_phase(
    cpd_root: Path,
    relative_energies_path: Path,
    target_composition: Composition,
    config: PipelineConfig,
) -> None:
    """Iteratively adjust energy of an unstable target phase.

    For single-element systems (e.g. Si) skip CPD vertex calculation
    — pydefect's HalfspaceIntersection requires ≥2 chemical-potential
    dimensions — and produce a synthetic target_vertices.yaml instead.

    This mirrors the legacy ``pydefect cv`` loop that decrements energy
    by *energy_adjust_step* until a valid chem_pot_diag.json is produced.
    """
    # ── Single-element shortcut ──────────────────────────────────
    elements = list(target_composition.as_dict().keys())
    if len(elements) == 1:
        target_vertices = cpd_root / _TARGET_VERTICES
        if not target_vertices.is_file():
            logger.info(
                "Single-element system (%s): writing synthetic target_vertices.yaml.",
                elements[0],
            )
            comp_str = str(target_composition)
            se_path = cpd_root / _STANDARD_ENERGIES
            _write_single_element_target_vertices(cpd_root, comp_str, se_path)

        # Also produce chem_pot_diag.json so downstream checks pass
        chem_pot_diag = cpd_root / _CHEM_POT_DIAG
        if not chem_pot_diag.is_file():
            _write_synthetic_chem_pot_diag(cpd_root, target_composition)
        return

    # ── Multi-element: normal pydefect cv flow ───────────────────
    if not relative_energies_path.is_file():
        return

    with open(relative_energies_path) as f:
        rel_energies = yaml.safe_load(f) or {}

    # Match against pymatgen-reduced formula so we are insensitive to whether
    # pydefect's sre output uses "Sr1Te1", "SrTe", or "Sr1 Te1" as the key.
    # See issues/0001-srte-cpd-target-lookup-false-positive-failure.md.
    target_string: Optional[str] = None
    target_reduced = target_composition.reduced_formula
    for comp_str in rel_energies:
        if Composition(comp_str).reduced_formula == target_reduced:
            target_string = comp_str
            break
    if target_string is None:
        raise ValueError(
            f"Target composition {target_composition} not found in "
            f"{relative_energies_path}."
        )

    origin_energy = rel_energies[target_string]
    current_energy = origin_energy

    try:
        run_local(
            f'pydefect cv -t "{target_string}"', cwd=cpd_root
        )
    except RuntimeError:
        logger.warning(
            "pydefect cv failed (common for single-element or unstable systems). "
            "Attempting energy adjustment loop."
        )
        current_energy = _energy_adjustment_loop(
            cpd_root, relative_energies_path, target_string,
            current_energy, origin_energy, config,
        )

    if abs(current_energy - origin_energy) > 1e-8:
        logger.info(
            "Energy of %s adjusted from %.4f to %.4f",
            target_string, origin_energy, current_energy,
        )


def _energy_adjustment_loop(
    cpd_root: Path,
    relative_energies_path: Path,
    target_string: str,
    current_energy: float,
    origin_energy: float,
    config: PipelineConfig,
) -> float:
    """Loop with decrementing energy until pydefect cv succeeds."""
    chem_pot_diag = cpd_root / _CHEM_POT_DIAG
    # The 10 eV limit is exclusive of the *next* decrement: once the previous
    # attempt reached ``origin - 10.0``, the next ``-= step`` would overshoot,
    # so we abort before writing the overshoot value into
    # ``relative_energies.yaml``.
    lower_bound = origin_energy - 10.0
    while not chem_pot_diag.is_file():
        next_energy = current_energy - config.energy_adjust_step
        if next_energy < lower_bound:
            raise RuntimeError(
                f"pydefect cv still failing after adjusting energy by 10 eV for "
                f"{target_string}. Aborting."
            )
        current_energy = next_energy
        with open(relative_energies_path) as f:
            rel_energies = yaml.safe_load(f) or {}
        rel_energies[target_string] = current_energy
        with open(relative_energies_path, "w") as f:
            yaml.dump(rel_energies, f, default_flow_style=None)
        try:
            run_local(
                f'pydefect cv -t "{target_string}"', cwd=cpd_root
            )
        except RuntimeError:
            continue
    return current_energy

def _write_single_element_target_vertices(
    cpd_root: Path,
    composition_string: str,
    standard_energies_path: Path,
) -> None:
    """Write a synthetic target_vertices.yaml for single-element systems.

    Format expected by pydefect's TargetVertices.from_yaml:
        target: <comp>
        <comp>:
          chem_pot: 0.0
          competing_phases: []
          impurity_phases: []
    """
    data = {
        "target": composition_string,
        composition_string: {
            "chem_pot": 0.0,
            "competing_phases": [],
            "impurity_phases": [],
        },
    }
    with open(cpd_root / _TARGET_VERTICES, "w") as f:
        yaml.dump(data, f, default_flow_style=None)


def _write_synthetic_chem_pot_diag(
    cpd_root: Path,
    target_composition: Composition,
) -> None:
    """Write a synthetic chem_pot_diag.json for single-element systems."""
    import json
    comp_str = str(target_composition)
    data = {
        "target_composition": comp_str,
        "competing_phases": [comp_str],
        "vertices": [[0.0]],
        "target_vertices": [[0.0]],
    }
    with open(cpd_root / _CHEM_POT_DIAG, "w") as f:
        json.dump(data, f, indent=2)