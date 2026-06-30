"""Defect post-processing — energy analysis, corrections, summaries."""

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
    """Run the defect energetics post-processing pipeline.

    Ported from ``pydefect_logic.py:583-637``.
    """
    summary_json = defect_root / "defect_energy_summary.json"
    if summary_json.is_file():
        logger.info("Defect energy summary already exists, skipping post-processing.")
        return
    perfect_dir = defect_root / "perfect"

    # ── OUTCAR check + recovery ────────────────────────────────────
    # The post-processing pipeline (pydefect CLI) reads OUTCAR from
    # disk.  If a dir has a cache entry but missing files, restore
    # from the cache (source_dir copy → blob structure_dict).
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
