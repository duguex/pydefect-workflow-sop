"""Defect structure generation — supercell, enumeration, VASP inputs."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.io import prepare_inputs
from vasp_sop.core.jobs import run_local

logger = logging.getLogger(__name__)

_DOS_EXTREMA = "../unitcell/dos/volumetric_data_local_extrema.json"


def build_all(
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


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════


def _build_supercell(defect_root: Path, uc_contcar: Path, config: PipelineConfig) -> None:
    """Construct the supercell via pydefect."""
    sc_info = defect_root / "supercell_info.json"
    if sc_info.is_file():
        logger.info("Supercell info already exists, skipping supercell construction.")
        return

    import time as _time
    # Ensure NFS visibility before subprocess.run(cwd=...)
    _time.sleep(0.5)
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
    for child in defect_root.iterdir():
        if not child.is_dir():
            continue
        prepare_inputs(
            child, config,
            kspacing=0.1, task_type="defect",
            extra_uis="SIGMA 0.02 LORBIT 11",
        )


def construct_complex_defects(defect_root: Path, config: PipelineConfig) -> None:
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
