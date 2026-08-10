"""CPD (chemical-potential diagram) stage.

Fetches competing phases from Materials Project, runs VASP calculations
for each, and constructs the chemical-potential diagram with pydefect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Optional

from vasp_sop.core.config import PipelineConfig

import yaml
from pymatgen.core import Composition

from vasp_sop.materials import list_phases
from vasp_sop.vasp.io import check_complete, patch_incar_u, prepare_inputs
from vasp_sop.defect import pydefect_adapter as _pdad
from vasp_sop.core.jobs import VaspJob, submit_vasp

logger = logging.getLogger(__name__)

_CPD_DIR = "cpd"
_TARGET_VERTICES = "target_vertices.yaml"
_COMPOSITION_ENERGIES = "composition_energies.yaml"
_RELATIVE_ENERGIES = "relative_energies.yaml"
_STANDARD_ENERGIES = "standard_energies.yaml"
_CHEM_POT_DIAG = "chem_pot_diag.json"
_MCE_REQUIRED_FILES = ("OUTCAR", "CONTCAR")


def _get_target_composition(formula: str):
    from pymatgen.core import Composition

    return Composition(formula)


@dataclass(frozen=True)
class CpdPreflight:
    """Validation result for the files consumed by ``pydefect_vasp mce``."""

    phase_dirs: tuple[str, ...]
    missing: dict[str, tuple[str, ...]]

    @property
    def ready(self) -> bool:
        return not self.missing


def preflight_cpd_inputs(cpd_root: Path) -> CpdPreflight:
    """Check the exact per-phase files required by ``pydefect_vasp mce``.

    The bundled pydefect implementation parses ``OUTCAR.final_energy`` and
    ``CONTCAR`` composition for every directory passed to ``mce``.  This
    adapter validates that contract without invoking pydefect or inferring
    VASP convergence state.
    """
    cpd_root = Path(cpd_root)
    phase_dirs = tuple(
        sorted(
            path for path in cpd_root.iterdir()
            if path.is_dir() and path.name != "combos"
        )
    )
    missing = {
        path.name: tuple(
            name for name in _MCE_REQUIRED_FILES if not (path / name).is_file()
        )
        for path in phase_dirs
        if any(not (path / name).is_file() for name in _MCE_REQUIRED_FILES)
    }
    return CpdPreflight(
        phase_dirs=tuple(path.name for path in phase_dirs),
        missing=missing,
    )


def _get_cpd_info(cpd_root: Path, intrinsic_elements: list[str]) -> dict[str, dict]:
    return list_phases(cpd_root, intrinsic_elements)


def _read_energy_per_atom(phase_dir: Path) -> float | None:
    """Extract energy-per-atom from OUTCAR in *phase_dir*, or None."""
    outcar = phase_dir / "OUTCAR"
    if not outcar.is_file():
        outcar = phase_dir / "output" / "OUTCAR"
    if not outcar.is_file():
        return None
    try:
        text = outcar.read_text()
    except OSError:
        return None
    # Parse "free  energy   TOTEN  =       -XX.XX eV" (last occurrence)
    energy: float | None = None
    for line in text.splitlines():
        if "free  energy   TOTEN" in line:
            parts = line.split("=")
            if len(parts) >= 2:
                try:
                    energy = float(parts[1].split()[0])
                except (ValueError, IndexError):
                    pass
    if energy is None:
        return None
    # Count atoms from POSCAR/CONTCAR
    for struct_file in ("CONTCAR", "POSCAR"):
        sp = phase_dir / struct_file
        if sp.is_file():
            try:
                from pymatgen.core import Structure

                n_atoms = len(Structure.from_file(str(sp)))
                if n_atoms > 0:
                    return energy / n_atoms
            except Exception:
                pass
    return None


def _split_target(
    cpd_root: Path,
    cpd_info: dict[str, dict],
    formula: str,
) -> tuple[Path, list[Path]]:
    """Return (target_dir, other_dirs).

    When multiple directories match the target composition, selection is
    deterministic: the directory with the lowest energy-per-atom (from
    OUTCAR) wins.  If no OUTCAR energies are available, the first match
    in sorted order is used.  The choice is logged for auditability.
    """
    from pymatgen.core import Composition

    target_comp = Composition(formula)
    candidates: list[Path] = []
    others: list[Path] = []
    for dirname, info in sorted(cpd_info.items()):
        p = (cpd_root / dirname).resolve()
        if Composition(info["formula"]) == target_comp:
            candidates.append(p)
        else:
            others.append(p)
    if not candidates:
        raise ValueError(f"Target {formula} not found in CPD dirs: {list(cpd_info)}")

    if len(candidates) == 1:
        target = candidates[0]
    else:
        # Deterministic selection: lowest energy-per-atom wins.
        scored: list[tuple[float | None, Path]] = [
            (_read_energy_per_atom(c), c) for c in candidates
        ]
        # Sort: entries with energy first (ascending), then those without.
        scored.sort(key=lambda x: (x[0] is None, x[0] if x[0] is not None else 0.0, str(x[1])))
        target = scored[0][1]
        energies_str = ", ".join(
            f"{c.name}={e:.4f} eV/atom" if e is not None else f"{c.name}=no OUTCAR"
            for e, c in scored
        )
        logger.info(
            "CPD target selection: %d candidates for %s — chose %s "
            "(lowest energy-per-atom). Scores: [%s]",
            len(candidates), formula, target.name, energies_str,
        )
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
        # Explicit -t structure_opt: vise 0.9.5's set_hubbard_u is only
        # honoured on explicit tasks (bare `vise vs` skips it, leaving
        # cpd INCARs without LDAU tags).
        prepare_inputs(work_dir, config, task_type="structure_opt")
        patch_incar_u(work_dir)  # ISPIN fallback; LDAU no-op when present
        outcar = work_dir / "OUTCAR"
        if outcar.is_file():
            logger.info("Skipping %s: OUTCAR exists", d)
            continue
        logger.info("CPD: submitting VASP for %s", d)
        jobs.append(submit_vasp(work_dir.resolve()))
    return jobs


def handoff_target_results(
    cpd_target: Path,
    structure_output: Path,
    target_composition: Composition,
) -> None:
    """Copy canonical target results (cpd/<target>) into unitcell/structure_opt."""
    cpd_target = Path(cpd_target)
    structure_output = Path(structure_output)
    structure_output.mkdir(parents=True, exist_ok=True)
    if not cpd_target.is_dir():
        raise FileNotFoundError(f"CPD target directory missing: {cpd_target}")

    from pymatgen.core import Structure

    target_poscar = cpd_target / "POSCAR"
    target_contcar = cpd_target / "CONTCAR"
    if not target_poscar.is_file():
        raise FileNotFoundError(f"CPD target POSCAR missing: {target_poscar}")
    if not target_contcar.is_file():
        raise FileNotFoundError(f"Target CONTCAR missing: {target_contcar}")

    expected = target_composition.reduced_formula
    target_formula = Structure.from_file(str(target_poscar)).composition.reduced_formula
    source_formula = Structure.from_file(str(target_contcar)).composition.reduced_formula
    if target_formula != expected or source_formula != expected:
        raise ValueError(
            "Target handoff composition mismatch: "
            f"expected {expected}, POSCAR={target_formula}, "
            f"CONTCAR={source_formula}"
        )
    required_results = ("POSCAR", "INCAR", "KPOINTS", "POTCAR",
                         "OUTCAR", "CONTCAR", "vasprun.xml")
    missing = [name for name in required_results if not (cpd_target / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "CPD target missing required files: " + ", ".join(missing)
        )

    for name in required_results:
        src = cpd_target / name
        dst = structure_output / name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
    logger.info("Staged target results %s -> %s", cpd_target, structure_output)



def ensure_target_results(
    cpd_target: Path,
    structure_output: Path,
    target_composition: Composition,
) -> None:
    """Validate canonical target, then handoff to unitcell/structure_opt."""
    cpd_target = Path(cpd_target)
    source_files = ("OUTCAR", "CONTCAR", "vasprun.xml")
    if not cpd_target.is_dir() or not all(
        (cpd_target / name).is_file() for name in source_files
    ):
        raise FileNotFoundError(
            f"CPD target {cpd_target} missing required results"
        )
    handoff_target_results(cpd_target, structure_output, target_composition)


def collect_cpd_phase_provenance(cpd_root: Path) -> dict[str, list[dict[str, str]]]:
    """Record phase sources and reject duplicate reduced compositions."""
    from pymatgen.core import Structure

    phases: list[dict[str, str]] = []
    by_composition: dict[str, list[str]] = {}
    for phase_dir in sorted(cpd_root.iterdir()):
        if not phase_dir.is_dir():
            continue
        structure_path = phase_dir / "CONTCAR"
        if not structure_path.is_file():
            structure_path = phase_dir / "POSCAR"
        if not structure_path.is_file():
            continue
        try:
            composition = Structure.from_file(str(structure_path)).composition.reduced_formula
        except Exception as exc:
            raise ValueError(f"Cannot parse CPD phase structure: {phase_dir}") from exc
        row = {
            "phase_dir": phase_dir.name,
            "composition": composition,
            "structure_source": structure_path.name,
        }
        phases.append(row)
        by_composition.setdefault(composition, []).append(phase_dir.name)

    provenance = {"phases": phases}
    (cpd_root / "cpd_phase_provenance.yaml").write_text(
        yaml.safe_dump(provenance, sort_keys=False)
    )
    duplicates = {
        formula: names for formula, names in by_composition.items() if len(names) > 1
    }
    if duplicates:
        details = "; ".join(
            f"{formula}: {', '.join(names)}" for formula, names in sorted(duplicates.items())
        )
        raise ValueError(f"Duplicate CPD compositions: {details}")
    return provenance
def compute_chemical_potentials(
    cpd_root: Path,
    config: PipelineConfig,
    target_composition: Composition,
) -> None:
    """Run pydefect post-processing steps for the CPD stage."""
    policy = getattr(config, "correction_policy", "custom_molecular_reference")
    if policy != "custom_molecular_reference":
        raise ValueError(f"Unsupported correction_policy for CPD execution: {policy}")
    target_vertices = cpd_root / _TARGET_VERTICES
    composition_energies = cpd_root / _COMPOSITION_ENERGIES
    relative_energies = cpd_root / _RELATIVE_ENERGIES

    if not target_vertices.is_file():
        collect_cpd_phase_provenance(cpd_root)
        preflight = preflight_cpd_inputs(cpd_root)
        (cpd_root / "cpd_preflight.yaml").write_text(
            yaml.safe_dump(
                {
                    "ready": preflight.ready,
                    "phase_dirs": list(preflight.phase_dirs),
                    "missing": {
                        name: list(files) for name, files in preflight.missing.items()
                    },
                },
                sort_keys=False,
            )
        )
        if not preflight.ready:
            details = "; ".join(
                f"{name}: {', '.join(files)}"
                for name, files in preflight.missing.items()
            )
            raise RuntimeError(f"CPD mce preflight failed: {details}")
    # ── composition_energies.yaml ────────────────────────────────────
    if not target_vertices.is_file():
        # Collect only the phase directories validated for mce.
        _pdad.mce(cpd_root, preflight.phase_dirs)

        if composition_energies.is_file():
            apply_molecule_corrections(
                composition_energies, config.molecule_corrections
            )

    # ── Binary compound shortcut ──────────────────────────────────
    # pydefect's sre/cv/pc pipeline doesn't handle 2-element systems
    # (1D chem-pot diagram). Use direct computation instead.
    n_elements = len(target_composition.elements)
    if n_elements <= 2 and not target_vertices.is_file():
        logger.info(
            "Binary compound (%d elements): using direct chem-pot computation.",
            n_elements,
        )
        _write_binary_target_vertices(
            cpd_root, target_composition, str(target_composition)
        )
        return

    # ── relative_energies.yaml / standard_energies.yaml ──────────────
    if not target_vertices.is_file():
        _pdad.sre(cpd_root)

    # ── Chem-pot diagram (energy adjustment for unstable phases) ─────
    if not target_vertices.is_file():
        adjust_unstable_phase(cpd_root, relative_energies, target_composition, config)

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
                cpd_root.name,
                n_elements,
            )
        else:
            # Plotting is a diagnostic only — a failure here must NOT block
            # the rest of the pipeline. See issues/0002-skip-4d-cpd-diagram.md.
            try:
                _pdad.chem_pot_diagram(cpd_root)
            except Exception as exc:
                logger.warning(
                    "pydefect pc failed for %s (non-fatal): %s",
                    cpd_root.name,
                    exc,
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
        _pdad.chemical_vertices(cpd_root, target_string)
    except RuntimeError:
        logger.warning(
            "pydefect cv failed (common for single-element or unstable systems). "
            "Attempting energy adjustment loop."
        )
        current_energy = _energy_adjustment_loop(
            cpd_root,
            relative_energies_path,
            target_string,
            current_energy,
            origin_energy,
            config,
        )

    if abs(current_energy - origin_energy) > 1e-8:
        logger.info(
            "Energy of %s adjusted from %.4f to %.4f",
            target_string,
            origin_energy,
            current_energy,
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
            _pdad.chemical_vertices(cpd_root, target_string)
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


def _write_binary_target_vertices(
    cpd_root: Path,
    target_composition: Composition,
    formula: str,
) -> None:
    """Write synthetic CPD output for binary compounds.

    pydefect's sre/cv/pc pipeline cannot handle 2-element systems
    (1D chem-pot diagram). Compute chemical potentials directly from
    competing-phase total energies instead.
    """
    import yaml

    comp_energies_path = cpd_root / _COMPOSITION_ENERGIES
    se_path = cpd_root / _STANDARD_ENERGIES
    target_vertices = cpd_root / _TARGET_VERTICES

    if not comp_energies_path.is_file():
        logger.warning(
            "Binary CPD: %s not found, cannot compute chem pots.", _COMPOSITION_ENERGIES
        )
        return

    comp_energies = yaml.safe_load(comp_energies_path.read_text()) or {}

    # Build standard_energies.yaml
    std_energies = {}
    for phase, data in comp_energies.items():
        energy = data.get("energy", 0.0)
        comp = Composition(phase)
        n = comp.num_atoms if comp.num_atoms > 0 else 1
        std_energies[phase] = {
            "energy": energy,
            "energy_per_atom": energy / n,
        }

    with open(se_path, "w") as f:
        yaml.dump(std_energies, f, default_flow_style=None)
    logger.info(
        "Binary CPD: wrote %s with %d phases", _STANDARD_ENERGIES, len(std_energies)
    )

    # ── Compute real chem_pot ────────────────────────────────────────
    # Find elemental reference energies from composition_energies
    elem_energies: dict[str, float] = {}
    for phase, data in comp_energies.items():
        comp = Composition(phase)
        if comp.is_element:
            elem = list(comp.elements)[0].symbol
            elem_energies[elem] = data.get("energy", 0.0) / comp.num_atoms

    target_energy = comp_energies.get(formula, {}).get("energy", 0.0)

    # Formation energy = E_target - Σ n_i * μ_i^0 (elemental references)
    if all(e.symbol in elem_energies for e in target_composition.elements):
        ref_energy = sum(
            target_composition[e.symbol] * elem_energies[e.symbol]
            for e in target_composition.elements
        )
        chem_pot = target_energy - ref_energy
    else:
        # Fallback: total energy per formula unit (still >> 0.0)
        chem_pot = target_energy
        logger.warning(
            "Binary CPD: elemental references not fully available "
            "(found: %s), using total energy as chem_pot",
            sorted(elem_energies.keys()),
        )

    # Write synthetic target_vertices.yaml
    data = {
        "target": formula,
        formula: {
            "chem_pot": chem_pot,
            "competing_phases": list(std_energies.keys()),
            "impurity_phases": [],
        },
    }
    with open(target_vertices, "w") as f:
        yaml.dump(data, f, default_flow_style=None)
    logger.info(
        "Binary CPD: wrote synthetic %s for %s (chem_pot=%.4f)",
        _TARGET_VERTICES,
        formula,
        chem_pot,
    )

    # Write synthetic chem_pot_diag.json
    import json

    diag = {
        "target_composition": formula,
        "competing_phases": list(std_energies.keys()),
        "vertices": [[chem_pot]],
        "target_vertices": [[chem_pot]],
    }
    with open(cpd_root / _CHEM_POT_DIAG, "w") as f:
        json.dump(diag, f, indent=2)
    logger.info("Binary CPD: wrote synthetic %s", _CHEM_POT_DIAG)
