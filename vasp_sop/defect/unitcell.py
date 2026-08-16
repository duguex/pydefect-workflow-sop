"""Unitcell stage.

Runs structure optimisation for the perfect unit cell, followed by
band-structure, DOS, and dielectric-response calculations.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.io import input_ready, prepare_inputs
from vasp_sop.core.jobs import (
    VaspJob,
    move_crisp_outputs,
    submit_vasp,
    wait_all,
    run_local,
)
from vasp_sop.defect import pydefect_adapter as _pdad

logger = logging.getLogger(__name__)

_UNITCELL_DIR = "unitcell"
_STRUCTURE_OPT = "structure_opt"
_UNITCELL_YAML = "unitcell.yaml"



def _prepare_all_inputs(uc_root: Path, target_dir: Path, config: PipelineConfig) -> None:
    """Create unitcell dirs and generate VASP inputs (no VASP needed)."""
    uc_root.mkdir(parents=True, exist_ok=True)
    structure_opt_dir = uc_root / _STRUCTURE_OPT

    # Copy POSCAR from CPD target dir (CONTCAR doesn't exist yet — VASP hasn't run)
    poscar_src = target_dir / "POSCAR"
    structure_opt_dir.mkdir(parents=True, exist_ok=True)
    if poscar_src.exists() and not (structure_opt_dir / "POSCAR").exists():
        shutil.copy2(str(poscar_src), str(structure_opt_dir / "POSCAR"))

    prepare_inputs(structure_opt_dir, config, task_type="structure_opt")
    pp_opt = (
        f"--potcar {' '.join(config.potcar_overrides)}"
        if config.potcar_overrides else ""
    )
    encut_opt = (
        f"-uis ENCUT {config.encut}" if config.encut else ""
    )
    # ``functional`` and ENCUT both come from config so all unitcell tasks
    # use the same setup as the CPD and defect stages.
    task_cmd_overrides = f" -x {config.functional} {encut_opt}"
    pp_suffix = f" --options set_hubbard_u True {pp_opt}"

    for task_name in _pdad.vise_task_types():
        task_dir = uc_root / task_name
        task_dir.mkdir(exist_ok=True)
        if not input_ready(task_dir):
            _copy_input_from_opt(structure_opt_dir, task_dir)
        # Replace the hardcoded ``-x pbesol`` baked into the template with the
        # config's functional, and inject ENCUT after the existing -uis flags
        # (or as the first -uis token if none present).
        base = _pdad.VISE_TASKS[task_name].replace("-x pbesol", task_cmd_overrides, 1)
        if config.encut and "ENCUT" not in base:
            base = base + f" -uis ENCUT {config.encut}"
        cmd = base + pp_suffix
        if not input_ready(task_dir):
            run_local(cmd, cwd=task_dir, timeout=300)
            # vise CLI 不经 prepare_inputs——按协议表补 NELM/EDIFF
            # (ADR 0024 单一事实源;vise 模板 NELM=100 会漂移)。
            from vasp_sop.vasp.io import patch_incar, protocol_tags

            tags = protocol_tags(task_name)
            fallback = {k: v for k, v in tags.items() if k in ("NELM", "EDIFF")}
            if tags.get("EDIFFG") is not None:
                fallback["EDIFFG"] = tags["EDIFFG"]
            patch_incar(task_dir, **fallback)


def _get_task_dirs(uc_root: Path, config: PipelineConfig) -> list[Path]:
    """Return [band_dir, dos_dir, dielectric_dir] for submission."""
    return [uc_root / t for t in _pdad.vise_task_types()]





# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════




def _copy_input_from_opt(src: Path, dst: Path) -> None:
    """Copy optimized structure from *src* to sub-task dir.

    Prefers CONTCAR (optimized) over POSCAR (initial) so that
    band/dos/dielectric tasks use the relaxed structure.
    """
    contcar = src / "CONTCAR"
    poscar_src = contcar if contcar.is_file() else (src / "POSCAR")
    if poscar_src.is_file():
        shutil.copy(str(poscar_src), str(dst / "POSCAR"))

    prior_src = src / "prior_info.yaml"
    if prior_src.is_file():
        shutil.copy(str(prior_src), str(dst / "prior_info.yaml"))


def _unitcell_failure_reason(diagnostic: str) -> str:
    """Classify terminal unitcell failures into stable reason codes."""
    normalized = diagnostic.lower().replace("_", " ")
    missing_vasprun_markers = (
        "missing vasprun",
        "vasprun.xml missing",
        "vasprun.xml not found",
        "no vasprun",
    )
    if any(marker in normalized for marker in missing_vasprun_markers):
        return "missing_vasprun"

    zero_gap_markers = (
        "zero band gap",
        "zero-gap",
        "zero gap",
        "near-zero band gap",
        "near-zero gap",
        "near zero band gap",
        "near zero gap",
    )
    if any(marker in normalized for marker in zero_gap_markers):
        return "zero_gap"
    return "pydefect_vasp_u_failed"


def build_unitcell_yaml(uc_root: Path, config: PipelineConfig) -> None:
    """Run post-processing visualisation and unitcell.yaml generation."""
    uc_yaml = uc_root / _UNITCELL_YAML
    if uc_yaml.is_file():
        logger.info("Unitcell yaml already exists, skipping post-processing.")
        return

    band_dir = uc_root / "band"
    dos_dir = uc_root / "dos"
    dielectric_dir = uc_root / "dielectric"

    band_vasprun_candidates = [
        (band_dir / "vasprun.xml").resolve(),
        (band_dir / "output" / "vasprun.xml").resolve(),
    ]
    band_vasprun = next((p for p in band_vasprun_candidates if p.is_file()),
                         band_vasprun_candidates[0])
    band_outcar = (band_dir / "OUTCAR").resolve()
    dielectric_outcar = (dielectric_dir / "OUTCAR").resolve()

    if band_vasprun.is_file():
        try:
            _pdad.run_in_subdir(uc_root, "band", "vise pb")
        except Exception:
            logger.warning("vise pb failed (likely no band structure to plot), skipping band plot.")

    if dos_dir.is_dir():
        try:
            _pdad.run_in_subdir(uc_root, "dos", "vise pd")
        except Exception:
            logger.warning("vise pd failed (likely missing vasprun.xml), skipping DOS plot.")
        try:
            _pdad.local_extrema(uc_root)
        except Exception:
            logger.warning("pydefect_vasp le failed (AECCAR missing), skipping local-extrema.")

    if dielectric_dir.is_dir():
        try:
            _pdad.run_in_subdir(uc_root, "dielectric", "vise pdf")
        except Exception:
            logger.warning("vise pdf failed (likely no band gap), skipping dielectric plot.")
    cmd = (
        f"pydefect_vasp u -vb {shlex.quote(str(band_vasprun))} "
        f"-ob {shlex.quote(str(band_outcar))} "
        f"-odc {shlex.quote(str(dielectric_outcar))} "
        f"-odi {shlex.quote(str(dielectric_outcar))} "
        f"-n {shlex.quote(config.formula)}"
    )
    try:
        _pdad.unitcell_yaml(
            uc_root,
            band_vasprun=band_vasprun,
            band_outcar=band_outcar,
            dielectric_outcar=dielectric_outcar,
            formula=config.formula,
        )
    except Exception as exc:
        logger.warning(
            "pydefect_vasp u failed (likely zero band gap or missing "
            "vasprun): %s — unitcell.yaml not written",
            exc,
        )
        try:
            diagnostic = str(exc)
            status = {
                "status": "failed",
                "reason": _unitcell_failure_reason(diagnostic),
                "diagnostic": diagnostic,
                "command": cmd,
            }
            (uc_root / "unitcell_build_status.json").write_text(
                json.dumps(status, ensure_ascii=False) + "\n"
            )
        except OSError:
            pass
        return
    if uc_yaml.is_file():
        try:
            (uc_root / "unitcell_build_status.json").write_text(
                '{"status": "ok"}\n'
            )
        except OSError:
            pass
