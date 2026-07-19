"""Three-wave batch orchestrator (issue #95).

Extracted from ``cli/main.py`` ``_advance_one_system``.  Each wave
function is independently callable and uses :class:`~vasp_sop.core.system.System`
properties for directory access.

Wave schedule
-------------
- **wave1_optimize**: STRUCTURE_OPT — target submission, convergence check,
  cache restore.
- **wave2_submit**: COMPETING + UNITCELL_DEFECT submission — competing dirs,
  UC tasks, defect submission.  Also contains the "prepare" step that builds
  defect structures eagerly once the target POSCAR exists.
- **wave3_postprocess**: CHEM_POT_DIAGRAM + post-processing — CPD compute,
  defect analysis.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from vasp_sop.core.system import System

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────


def _make_info_fn(log_to_logger: bool) -> Callable[[str], None]:
    """Return a print-or-log function based on *log_to_logger*."""
    if log_to_logger:
        return lambda msg: logger.info("%s", msg)
    return print


def _submit_or_skip(
    path: Path,
    label: str,
    sys_name: str,
    dry_run: bool,
    info_fn: Callable[[str], None],
) -> Any:
    """Submit a VASP job via crisp, or skip in dry-run mode."""
    from vasp_sop.core.jobs import submit_vasp
    from vasp_sop.core.job_store import JobStore

    if dry_run:
        if not label.startswith("df-"):
            info_fn(f"  [dry-run] {sys_name:<18} would submit: {label}")
        return None
    try:
        job = submit_vasp(path.resolve())
        js = JobStore()
        js.track(str(path.resolve()))
        js.record(str(path.resolve()), "submitted", source=job.task_name)
        js.close()
        info_fn(f"  → {sys_name:<18} {label}: {job.task_name}")
        return job
    except Exception as exc:
        logger.warning("%s/%s submit failed: %s", sys_name, label, exc)
        return None


def _unitcell_build_failure(root: Path) -> dict[str, str] | None:
    """Read a terminal unitcell build failure without introducing a phase."""
    status_path = Path(root) / "unitcell" / "unitcell_build_status.json"
    if not status_path.is_file():
        return None
    try:
        status = json.loads(status_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict) or status.get("status") != "failed":
        return None
    return {
        "reason": str(status.get("reason", "unitcell_build_failed")),
        "diagnostic": str(status.get("diagnostic", "no diagnostic recorded")),
    }


# ── Wave 1 ─────────────────────────────────────────────────────────────────


def wave1_optimize(
    sys: System, js: Any, dry_run: bool, *, log_to_logger: bool = False
) -> None:
    """Wave 1: STRUCTURE_OPT — target submission, convergence check, cache restore.

    Preconditions
    -------------
    - ``sys.target_dir`` must not be ``None`` (cpd/ directory with target phase).
    - ``js`` is an open :class:`~vasp_sop.core.job_store.JobStore`.
    """
    from vasp_sop.vasp.io import check_converged, input_ready
    from vasp_sop.core.cache import cache_lookup
    from vasp_sop.core.job_store import JobStore, record_if_done

    info = _make_info_fn(log_to_logger)
    td = sys.target_dir
    if td is None:
        return

    if js.latest(str(td.resolve())) != "submitted":
        if check_converged(td):
            js.record(str(td.resolve()), "converged")
        else:
            cached = cache_lookup(td)
            if cached:
                logger.info("%s target restored from calc cache", sys.name)
                from vasp_sop.core.cache import restore_from_cache

                restored = restore_from_cache(td)
                if restored:
                    submit_info = {
                        "task_name": "cached",
                        "work_dir": str(td.resolve()),
                    }
                    with open(sys.cpd_dir / ".target_submit.json", "w") as _f:
                        json.dump(submit_info, _f)
                    record_if_done(JobStore(), td, source="cache_restore")
            elif input_ready(td):
                _submit_or_skip(td, "target", sys.name, dry_run, info)


# ── Wave 2 ─────────────────────────────────────────────────────────────────


def wave2_submit(
    sys: System, js: Any, dry_run: bool, *, log_to_logger: bool = False
) -> None:
    """Wave 2: COMPETING + UNITCELL_DEFECT submission.

    Handles
    -------
    - **Prepare**: build defect structures if target POSCAR exists (moved from
      the eager block that previously ran before phase dispatch).
    - **COMPETING**: submit competing phase directories.
    - **UNITCELL_DEFECT**: submit UC tasks (band/dos/dielectric), perfect
      supercell, and defect directories.

    Preconditions
    -------------
    - ``sys.root`` must contain ``plan.yaml``.
    - ``js`` is an open :class:`~vasp_sop.core.job_store.JobStore`.
    """
    from vasp_sop.vasp.io import check_converged, input_ready, prepare_inputs
    from vasp_sop.core.cache import cache_lookup
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.defect.builder import build_all as _build_defects

    info = _make_info_fn(log_to_logger)
    uc_root = sys.uc_dir
    df_root = sys.defect_dir

    # ── Prepare: build defect structures ─────────────────────────────
    td = sys.target_dir
    if td and (td / "POSCAR").is_file():
        if not (df_root / "defect_in.yaml").is_file():
            logger.info(
                "%s: building defect structures (early, phase=%s) ...",
                sys.name,
                sys.phase(),
            )
            try:
                _build_defects(df_root, td, sys.config)
            except Exception as exc:
                logger.error("%s defect build failed: %s", sys.name, exc)
        # Fill in any missing VASP inputs (parallel now, cheap)
        if (df_root / "defect_in.yaml").is_file():
            potcar_count = len(list(df_root.rglob("POTCAR")))
            dir_count = len([c for c in df_root.iterdir() if c.is_dir()])
            if potcar_count < dir_count:
                logger.info(
                    "%s: completing missing VASP inputs (%d/%d POTCARs) ...",
                    sys.name,
                    potcar_count,
                    dir_count,
                )
                try:
                    from vasp_sop.defect.builder import _generate_vasp_inputs

                    _generate_vasp_inputs(df_root, sys.config)
                except Exception as exc:
                    logger.error(
                        "%s VASP inputs completion failed: %s", sys.name, exc
                    )
        # Dry-run summary: count defect dirs that would be submitted
        if dry_run and (df_root / "defect_in.yaml").is_file():
            n_df = len(
                [
                    c
                    for c in df_root.iterdir()
                    if c.is_dir() and c.name != "perfect" and (c / "INCAR").is_file()
                ]
            )
            uc_tasks = [
                t
                for t in ("band", "dos", "dielectric")
                if (uc_root / t / "INCAR").is_file()
            ]
            parts: list[str] = []
            if uc_tasks:
                parts.append("uc-" + "+".join(uc_tasks))
            if n_df:
                parts.append(f"df-{n_df} defects")
            p = sys.phase()
            if p == "STRUCTURE_OPT":
                parts.append("perfect")
            if parts:
                info(
                    f"  [dry-run] {sys.name:<18} would submit: {' '.join(parts)}"
                )

    # ── COMPETING: submit competing dirs ─────────────────────────────
    p = sys.phase()
    if p == "COMPETING":
        for cd in sys._competing_dirs(js):
            if js.latest(str(cd.resolve())) == "submitted":
                continue
            if "_mp-" in cd.name:
                _cached = cache_lookup(cd)
                if _cached:
                    logger.info("%s restored from calc cache", cd.name)
                    from vasp_sop.core.cache import restore_from_cache

                    restore_from_cache(cd)
                    continue
            _submit_or_skip(cd, f"phase:{cd.name}", sys.name, dry_run, info)
        return

    # ── UNITCELL_DEFECT: submit UC tasks + defect dirs ───────────────
    if p != "UNITCELL_DEFECT":
        return

    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.vasp.io import check_task_complete

    if td and not (uc_root / "band" / "INCAR").is_file():
        _uc._prepare_all_inputs(uc_root, td, sys.config)
    if td and not (df_root / "perfect" / "INCAR").is_file():
        if not (df_root / "defect_in.yaml").is_file():
            _build_defects(df_root, td, sys.config)
        else:
            from vasp_sop.defect.builder import _generate_vasp_inputs

            _generate_vasp_inputs(df_root, sys.config)

    # Submit UC tasks (band, dos, dielectric)
    for task in ("band", "dos", "dielectric"):
        task_dir = uc_root / task
        if not task_dir.is_dir():
            continue
        if check_task_complete(task_dir, task):
            if js.latest(str(task_dir.resolve())) != "converged":
                js.record(str(task_dir.resolve()), "converged", source="backfill")
            continue
        # Stale JobStore "converged" without required outputs must resubmit.
        if js.latest(str(task_dir.resolve())) == "submitted":
            continue
        prepare_inputs(task_dir, sys.config, task_type=task)
        _submit_or_skip(task_dir, f"uc-{task}", sys.name, dry_run, info)

    # Submit perfect supercell
    perfect_dir = df_root / "perfect"
    if perfect_dir.is_dir() and input_ready(perfect_dir):
        perfect_path = str(perfect_dir.resolve())
        perfect_state = js.latest(perfect_path)
        if check_converged(perfect_dir):
            if perfect_state != "converged":
                js.record(perfect_path, "converged", source="backfill")
        elif perfect_state not in ("submitted", "failed", "unconverged"):
            _submit_or_skip(perfect_dir, "df-perfect", sys.name, dry_run, info)

    # Submit defect directories
    if df_root.is_dir() and not (df_root / "defect_energy_summary.json").is_file():
        from vasp_sop.vasp.io import (
            has_vasprun,
            recover_vasprun_artifacts,
            prepare_vasprun_recovery_run,
        )

        for child in sorted(df_root.iterdir()):
            if not child.is_dir() or child.name == "perfect":
                continue
            if not input_ready(child):
                continue
            latest = js.latest(str(child.resolve()))
            if latest == "submitted":
                continue

            # Ion-converged but missing vasprun/calc_results -> recovery (#0016)
            if check_converged(child):
                has_cr = (child / "calc_results.json").is_file()
                if has_cr or has_vasprun(child) or recover_vasprun_artifacts(child):
                    if js.latest(str(child.resolve())) != "converged":
                        js.record(
                            str(child.resolve()), "converged", source="backfill"
                        )
                    continue
                # Still no vasprun: single-point from CONTCAR
                if latest in ("failed",) and "vasprun_recovery" not in (
                    (js.history(str(child.resolve())) or [{}])[-1].get("reason", "")
                ):
                    # allow one recovery after failed recovery
                    pass
                if not prepare_vasprun_recovery_run(child):
                    logger.warning(
                        "%s: cannot prep vasprun recovery (inputs)", child.name
                    )
                    continue
                logger.info(
                    "%s: resubmit for missing vasprun (CONTCAR/static)", child.name
                )
                _submit_or_skip(
                    child, f"df-vr-{child.name}", sys.name, dry_run, info
                )
                if js.latest(str(child.resolve())) == "submitted":
                    js.record(
                        str(child.resolve()),
                        "submitted",
                        source="vasprun_recovery",
                        reason="vasprun_recovery",
                    )
                continue

            if latest in ("failed", "converged", "unconverged"):
                continue
            _submit_or_skip(child, f"df-{child.name}", sys.name, dry_run, info)


# ── Wave 3 ─────────────────────────────────────────────────────────────────


def wave3_postprocess(
    sys: System, dry_run: bool, *, log_to_logger: bool = False
) -> dict:
    """Wave 3: CHEM_POT_DIAGRAM + post-processing.

    Handles
    -------
    - **CHEM_POT_DIAGRAM**: CPD computation + structure_opt cache restore.
    - **UNITCELL_DEFECT** (when all VASP done): build unitcell yaml + analyze
      defects via pydefect.

    Returns
    -------
    dict
        Status information with at least ``"phase"`` and ``"status"`` keys.
    """
    from vasp_sop.vasp.io import check_converged, input_ready
    from vasp_sop.core.jobs import move_crisp_outputs
    from vasp_sop.core.cache import vasp_results_put
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.defect import cpd as _cpd
    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.defect.analysis import analyze as _analyze_defects

    info = _make_info_fn(log_to_logger)
    cpd_root = sys.cpd_dir
    uc_root = sys.uc_dir
    df_root = sys.defect_dir

    p = sys.phase()
    result: dict[str, Any] = {"phase": p}

    # ── CHEM_POT_DIAGRAM: CPD computation ────────────────────────────
    if p == "CHEM_POT_DIAGRAM":
        if not dry_run:
            for pd in cpd_root.iterdir():
                if pd.is_dir() and check_converged(pd):
                    move_crisp_outputs(pd)
            logger.info("%s: CPD post-processing ...", sys.name)
            try:
                target_composition = _cpd._get_target_composition(
                    sys.config.formula
                )
                _cpd.compute_chemical_potentials(
                    cpd_root, sys.config, target_composition
                )
                f = sys.config.formula
                m = sys._mpid
                if f and m:
                    td = sys.target_dir
                    so = uc_root / "structure_opt"
                    key = vasp_results_put(td)
                    if not key:
                        raise RuntimeError(
                            f"vasp_results_put failed for {sys.name} target"
                        )
                    from vasp_sop.core.cache import restore_from_key

                    if not restore_from_key(key, so):
                        raise RuntimeError(
                            f"structure_opt cache restore failed for {sys.name}"
                        )
                    logger.info("%s structure_opt restored from cache", sys.name)
            except Exception as exc:
                logger.error("%s CPD failed: %s", sys.name, exc)
                if not log_to_logger:
                    print(f"  ✗ {sys.name:<18} CPD post-processing FAILED")
                raise
        result["status"] = "cpd_done"
        return result

    # ── UNITCELL_DEFECT: post-processing ─────────────────────────────
    if p != "UNITCELL_DEFECT":
        result["status"] = "skipped"
        return result

    # Dry-run artifact-based preview (issue #20)
    if dry_run:
        artifacts = {
            "unitcell.yaml": uc_root / "unitcell.yaml",
            "target_vertices.yaml": cpd_root / "target_vertices.yaml",
            "standard_energies.yaml": cpd_root / "standard_energies.yaml",
        }
        missing = [name for name, path in artifacts.items() if not path.is_file()]
        has_defect_contcar = False
        if df_root.is_dir():
            has_defect_contcar = any(
                child.is_dir() and (child / "CONTCAR").is_file()
                for child in df_root.iterdir()
            )
        if not has_defect_contcar:
            missing.append("defect/CONTCAR")
        done_summary = df_root / "defect_energy_summary.json"
        if not missing and not done_summary.is_file():
            info(
                f"  [dry-run] {sys.name:<18} would post-process "
                f"(artifacts present, no analysis run)"
            )
        elif not missing and done_summary.is_file():
            info(
                f"  [dry-run] {sys.name:<18} already complete "
                f"(summary exists)"
            )
        else:
            info(
                f"  [dry-run] {sys.name:<18} post-process blocked "
                f"(missing: {', '.join(missing)})"
            )
        result["status"] = "dry_run_preview"
        return result

    # ── Real post-processing ─────────────────────────────────────────
    js = JobStore()
    from vasp_sop.vasp.io import check_task_complete as _ctc

    # UC done only when disk outputs are complete (not JobStore alone).
    uc_all_done = all(
        (not (uc_root / t / "INCAR").is_file()) or _ctc(uc_root / t, t)
        for t in ("band", "dos", "dielectric")
    )

    # Defect VASP finished: converged, failed, or not a calc dir.
    def _df_job_finished(child: Path) -> bool:
        if not input_ready(child):
            return True
        st = js.latest(str(child.resolve()))
        return st in ("converged", "failed", "unconverged")

    df_vasp_done = (
        all(_df_job_finished(child) for child in df_root.iterdir() if child.is_dir())
        if df_root.is_dir()
        else True
    )

    # On-disk readiness for pydefect: need OUTCAR (or failed/non-calc).
    def _df_ondisk_ok(child: Path) -> bool:
        if not input_ready(child):
            return True
        if js.latest(str(child.resolve())) in ("failed", "unconverged"):
            return True
        return (
            (child / "OUTCAR").is_file()
            or (child / "output" / "OUTCAR").is_file()
            or check_converged(child)
        )

    df_vasp_ondisk = (
        all(_df_ondisk_ok(child) for child in df_root.iterdir() if child.is_dir())
        if df_root.is_dir()
        else True
    )

    if (
        uc_all_done
        and df_vasp_done
        and df_vasp_ondisk
        and (df_root / "defect_energy_summary.json").is_file()
    ):
        result["status"] = "already_complete"
        return result

    if uc_all_done and df_vasp_done and df_vasp_ondisk:
        logger.info("%s: post-processing ...", sys.name)
        try:
            _uc.build_unitcell_yaml(uc_root, sys.config)
            failure = _unitcell_build_failure(uc_root.parent)
            if failure:
                raise RuntimeError(
                    f"unitcell blocked for {sys.name}: {failure['reason']}; "
                    f"{failure['diagnostic']}"
                )
            status = _analyze_defects(
                df_root,
                sys.root,
                sys.config,
                unitcell_yaml=uc_root / "unitcell.yaml",
                standard_energies=cpd_root / "standard_energies.yaml",
                target_vertices=cpd_root / "target_vertices.yaml",
            )
            if status == "full":
                info(f"  ✓ {sys.name:<18} pipeline complete")
            elif status == "partial":
                message = (
                    f"  ~ {sys.name:<18} post-process partial "
                    f"(see defect/analyze_status.json)"
                )
                if log_to_logger:
                    logger.warning("%s", message)
                else:
                    print(message)
            else:
                message = (
                    f"  ✗ {sys.name:<18} post-process failed "
                    f"(see defect/analyze_status.json)"
                )
                if log_to_logger:
                    logger.error("%s", message)
                else:
                    print(message)
            result["status"] = status
        except Exception as exc:
            logger.error("%s post-processing failed: %s", sys.name, exc)
            if _unitcell_build_failure(uc_root.parent):
                raise
            result["status"] = "failed"
    else:
        result["status"] = "vasp_pending"

    return result
