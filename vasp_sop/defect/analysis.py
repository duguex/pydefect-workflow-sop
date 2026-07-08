"""Defect post-processing — energy analysis, corrections, summaries.

Each pydefect step has a guard that skips it if its output already
exists, so re-running post-processing picks up where it left off.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.jobs import run_local

logger = logging.getLogger(__name__)


def analyze(
    defect_root: Path,
    project_root: Path,
    config: PipelineConfig,
    unitcell_yaml: Path,
    standard_energies: Path,
    target_vertices: Path,
) -> None:
    """Run the defect energetics post-processing pipeline."""
    summary_json = defect_root / "defect_energy_summary.json"
    if summary_json.is_file():
        logger.info("Defect energy summary already exists, skipping post-processing.")
        return
    perfect_dir = defect_root / "perfect"

    # ── OUTCAR check + recovery ────────────────────────────────────
    from vasp_sop.core.cache import restore_from_cache
    from vasp_sop.core.jobs import move_crisp_outputs
    missing_outcars: list[str] = []
    for d in list(defect_root.iterdir()) + [perfect_dir]:
        if not d.is_dir():
            continue
        if (d / "OUTCAR").is_file():
            continue
        move_crisp_outputs(d)
        if (d / "OUTCAR").is_file():
            continue
        if restore_from_cache(d):
            logger.info("Restored OUTCAR for %s from cache", d.name)
        else:
            missing_outcars.append(d.name)
    if missing_outcars:
        logger.error(
            "Cannot run post-processing: %d defect dir(s) missing OUTCAR "
            "and could not be restored from cache: %s. "
            "Restore the files manually or re-run VASP for these dirs.",
            len(missing_outcars), ", ".join(missing_outcars),
        )
        return

    # Collect defect dirs once, reuse across guards
    defect_dirs_all = sorted(
        d for d in defect_root.iterdir()
        if d.is_dir() and d.name != "perfect" and "_" in d.name
    )

    # ── cr (calc_results) ───────────────────────────────────────────
    cr_present = [d for d in defect_dirs_all if (d / "calc_results.json").is_file()]
    perfect_cr = perfect_dir / "calc_results.json"
    if len(cr_present) == len(defect_dirs_all) and perfect_cr.is_file():
        logger.info("calc_results.json exists for all dirs, skipping pydefect_vasp cr.")
    else:
        run_local("pydefect_vasp cr -d *_* perfect", cwd=defect_root)

    # ── efnv (energy-free NV) correction ────────────────────────────
    if perfect_dir.is_dir() and perfect_cr.is_file():
        run_local(
            f"pydefect efnv -d *_* -pcr {perfect_cr} -u {unitcell_yaml}",
            cwd=defect_root,
        )

    # ── dsi (defect structure info) ─────────────────────────────────
    dsi_present = [d for d in defect_dirs_all if (d / "defect_structure_info.json").is_file()]
    if len(dsi_present) == len(defect_dirs_all):
        logger.info("defect_structure_info.json exists for all dirs, skipping pydefect dsi.")
    else:
        run_local("pydefect dsi -d *_*", cwd=defect_root)

    # ── dvf (defect volume fraction) ────────────────────────────────
    dvf_present = [d for d in defect_dirs_all if (d / "defect_volume_fraction.json").is_file()]
    if len(dvf_present) == len(defect_dirs_all):
        logger.info("defect_volume_fraction.json exists for all dirs, skipping pydefect_util dvf.")
    else:
        try:
            run_local("pydefect_util dvf -d *_*", cwd=defect_root)
        except Exception:
            logger.warning("pydefect_util dvf failed (may be slow on NFS or missing inputs), "
                           "skipping defect volume fraction.")

    # ── pbes (perfect band-edge state) ──────────────────────────────
    pbes_json = perfect_dir / "perfect_band_edge_state.json"
    if pbes_json.is_file():
        logger.info("perfect_band_edge_state.json exists, skipping pydefect_vasp pbes.")
    else:
        run_local("pydefect_vasp pbes -d perfect", cwd=defect_root)

    # ── beoi + bes (band-edge orbital info + state) ────────────────
    if pbes_json.is_file():
        run_local(f"pydefect_vasp beoi -d *_* -pbes {pbes_json}", cwd=defect_root)
        run_local(f"pydefect bes -d *_* -pbes {pbes_json}", cwd=defect_root)

    # ── dei (defect energy info) ───────────────────────────────────
    if perfect_cr.is_file() and unitcell_yaml.is_file() and standard_energies.is_file():
        corrected = [d for d in defect_dirs_all if (d / "correction.json").is_file()]
        not_corrected = [d for d in defect_dirs_all if not (d / "correction.json").is_file()]
        if not_corrected:
            logger.warning(
                "Skipping %d defect(s) missing correction.json: %s",
                len(not_corrected), ", ".join(d.name for d in not_corrected),
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
        run_local(f"pydefect cs -d *_* -pcr {perfect_cr}", cwd=defect_root)

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
