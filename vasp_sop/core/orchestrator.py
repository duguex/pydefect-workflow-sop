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
import re
from pathlib import Path
from typing import Any, Callable

from vasp_sop.core.system import (
    CHEM_POT_DIAGRAM,
    COMPETING,
    COMPLETE,
    NO_TARGET,
    STRUCTURE_OPT,
    UNITCELL_DEFECT,
    System,
)

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────


def _make_info_fn(log_to_logger: bool) -> Callable[[str], None]:
    """Return a print-or-log function based on *log_to_logger*."""
    if log_to_logger:
        return lambda msg: logger.info("%s", msg)
    return print


_Q_RE = re.compile(r"_(-?\d+)$")


def _defect_group_key(name: str) -> str:
    """Defect group = directory name with the charge suffix stripped.

    ``Va_Gd1_-3`` and ``Va_Gd1_-2`` share ``Va_Gd1``; complex defects keep
    their ``motif+unit`` prefix (``Gd_Ga1+Va_O1_-1`` → ``Gd_Ga1+Va_O1``).
    """
    return _Q_RE.sub("", name)


def _defect_charge(name: str) -> int | None:
    """The charge state parsed from a defect directory name, or None."""
    m = _Q_RE.search(name)
    return int(m.group(1)) if m else None


def _chain_roots(charges: list[int]) -> set[int]:
    """Median charge states — the chain's starting points (ADR 0010).

    Odd length: the single median.  Even length: the two middle charges,
    which start in parallel and seed outward in both directions.
    """
    qs = sorted(charges)
    n = len(qs)
    if n == 0:
        return set()
    if n % 2 == 1:
        return {qs[n // 2]}
    return {qs[n // 2 - 1], qs[n // 2]}


def _submit_or_skip(
    path: Path,
    label: str,
    sys_name: str,
    dry_run: bool,
    info_fn: Any,
    *,
    js: Any = None,
    source: str | None = None,
    priority: int = 0,
) -> Any:
    """Submit a VASP job via crisp, or skip in dry-run mode."""
    from vasp_sop.core.jobs import submit_vasp
    from vasp_sop.core.job_store import JobStore

    if dry_run:
        if not label.startswith("df-"):
            info_fn(f"  [dry-run] {sys_name:<18} would submit: {label}")
        return None
    try:
        job = submit_vasp(path.resolve(), priority=priority)
        owned = js is None
        store = js if not owned else JobStore()
        try:
            store.track(str(path.resolve()))
            store.record(
                str(path.resolve()), "submitted",
                source=source or job.task_name,
            )
        finally:
            if owned:
                store.close()
        info_fn(f"  → {sys_name:<18} {label}: {job.task_name}")
        return job
    except Exception as exc:
        logger.warning("%s/%s submit failed: %s", sys_name, label, exc)
        return None


def _unitcell_build_failure(root: Path) -> dict[str, str] | None:
    """Read a terminal unitcell build failure without introducing a phase.

    Self-heals on staleness, never on evidence: a failure marker is cleared
    only when every UC task (band/dos/dielectric) is complete on disk AND
    the marker predates those outputs — i.e. the build failed, then the UC
    leg later completed anyway (SeO2: 07-17 pydefect_vasp_u_failed, UC
    converged weeks later).  A marker NEWER than the outputs is a genuine
    current build failure and keeps blocking (disk truth wins, ADR 0003).
    """
    status_path = Path(root) / "unitcell" / "unitcell_build_status.json"
    if not status_path.is_file():
        return None
    try:
        status = json.loads(status_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict) or status.get("status") != "failed":
        return None
    from vasp_sop.vasp.io import check_task_complete

    uc = Path(root) / "unitcell"
    artifacts = [
        uc / t / "OUTCAR"
        for t in ("band", "dos", "dielectric")
    ] + [uc / t / "vasprun.xml" for t in ("band", "dos")]
    try:
        marker_mtime = status_path.stat().st_mtime
    except OSError:
        marker_mtime = 0.0
    newest_output = max(
        (p.stat().st_mtime for p in artifacts if p.is_file()),
        default=0.0,
    )
    if all(
        (uc / t).is_dir() and check_task_complete(uc / t, t)
        for t in ("band", "dos", "dielectric")
    ) and newest_output > marker_mtime:
        try:
            status_path.unlink()
        except OSError:
            pass
        logger.warning(
            "%s: cleared stale unitcell build failure (UC leg done on disk)",
            Path(root).name,
        )
        return None
    return {
        "reason": str(status.get("reason", "unitcell_build_failed")),
        "diagnostic": str(status.get("diagnostic", "no diagnostic recorded")),
    }


# ── Wave 1 ─────────────────────────────────────────────────────────────────


def wave1_optimize(
    sys: System, js: Any, dry_run: bool, *, log_to_logger: bool = False,
    priority: int = 0,
) -> None:
    """Wave 1: STRUCTURE_OPT — target submission, convergence check, cache restore.

    Preconditions
    -------------
    - ``sys.target_dir`` must not be ``None`` (cpd/ directory with target phase).
    - ``js`` is an open :class:`~vasp_sop.core.job_store.JobStore`.
    """
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready

    info = _make_info_fn(log_to_logger)
    td = sys.target_dir
    if td is None:
        return

    if js.latest(str(td.resolve())) != "submitted":
        if convergence_verdict(td).converged:
            js.record(str(td.resolve()), "converged")
        elif input_ready(td):
            _submit_or_skip(td, "target", sys.name, dry_run, info, js=js,
                            priority=priority)


# ── Wave 2 ─────────────────────────────────────────────────────────────────


def _stage2_soc_pending(child: Path, js: Any) -> bool:
    """ADR 0014: dir converged in stage 1 (non-SOC) and never supplemented.

    A ``soc_stage2`` record anywhere in the dir's history means stage 2
    was already armed — do not resubmit (covers in-flight, done, failed).
    """
    cp = str(child.resolve())
    if js.latest(cp) != "converged":
        return False
    return not any(r.get("source") == "soc_stage2" for r in js.history(cp))


def _submit_stage2_soc(child: Path, sys: Any, js: Any, dry_run: bool,
                       info_fn: Any, *, priority: int = 0) -> None:
    """Submit the ADR 0014 SOC supplement for one converged dir.

    ``Bi_*`` dirs continue from CONTCAR with LSORBIT (structure also
    relaxes under SOC); everything else gets an NSW=0 single point —
    an SOC energy correction on the non-SOC-optimized structure.
    """
    from vasp_sop.vasp.io import patch_incar

    is_bi = child.name.startswith("Bi_")
    patch_incar(child, LSORBIT=".TRUE.", ISYM=-1)
    if not is_bi:
        patch_incar(child, NSW=0)
    else:
        cont = child / "CONTCAR"
        if cont.is_file() and cont.stat().st_size > 0:
            (child / "POSCAR").write_text(cont.read_text(errors="ignore"))
    _submit_or_skip(child, f"soc2:{child.name}", sys.name, dry_run, info_fn,
                    js=js, source="soc_stage2", priority=priority)


def wave2_submit(
    sys: System, js: Any, dry_run: bool, *, log_to_logger: bool = False,
    retry_failed: bool = False, priority: int = 0,
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
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready, prepare_inputs
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.defect.builder import build_all as _build_defects
    from vasp_sop.defect import is_valid_defect_dir

    info = _make_info_fn(log_to_logger)
    uc_root = sys.uc_dir
    df_root = sys.defect_dir

    # ── Prepare: build defect structures ─────────────────────────────
    # Chemical-environment systems (ADR 0005) have no defect leg.
    td = sys.target_dir
    if td and (td / "POSCAR").is_file() and not sys.is_chemical_environment:
        if not (df_root / "defect_in.yaml").is_file():
            logger.info(
                "%s: building defect structures (early, phase=%s) ...",
                sys.name,
                sys.derive_phase(js),
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
    p = sys.derive_phase(js)
    if p == "COMPETING":
        for cd in sys.competing_dirs(js):
            cp = str(cd.resolve())
            latest = js.latest(cp)
            if latest == "submitted":
                continue
            if latest in ("failed", "unconverged"):
                # One-shot auto-rerun (ADR 0007, same policy as defect
                # dirs): a second failure is terminal forever, armed only
                # by an explicit `batch run --retry-failed`.
                if not retry_failed:
                    continue
                if any(r.get("source") == "auto_retry"
                       for r in js.history(cp)):
                    continue
                _submit_or_skip(cd, f"phase:{cd.name}", sys.name, dry_run, info,
                                js=js, source="auto_retry", priority=priority)
                continue
            _submit_or_skip(cd, f"phase:{cd.name}", sys.name, dry_run, info,
                            js=js, priority=priority)
        # ADR 0014: SOC supplement for converged competing phases.
        if sys.config.stage2_soc:
            cpd_dir = sys.cpd_dir
            if cpd_dir.is_dir():
                for pd in sorted(cpd_dir.iterdir()):
                    if not pd.is_dir() or not input_ready(pd):
                        continue
                    if _stage2_soc_pending(pd, js):
                        _submit_stage2_soc(pd, sys, js, dry_run, info,
                                           priority=priority)
        return

    # ── UNITCELL_DEFECT: submit UC tasks + defect dirs ───────────────
    # Chemical-environment systems never run this leg (ADR 0005).
    if p != "UNITCELL_DEFECT" or sys.is_chemical_environment:
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
        # A runnable submission needs a real POSCAR.  An empty/missing one
        # (0-byte placeholder, e.g. bulk_restart-era) makes crisp refuse the
        # upload — record failed(empty_poscar) once and stop retrying every
        # cycle; repair the data and `batch retry` to re-arm.
        poscar = task_dir / "POSCAR"
        if not poscar.is_file() or poscar.stat().st_size == 0:
            cp = str(task_dir.resolve())
            last = js.latest(cp)
            if not (last == "failed" and js.history(cp)[-1].get("reason")
                    == "empty_poscar"):
                js.record(cp, "failed", reason="empty_poscar")
                logger.warning("%s/%s: empty POSCAR, not submitting",
                               sys.name, task)
            continue
        prepare_inputs(task_dir, sys.config, task_type=task)
        _submit_or_skip(task_dir, f"uc-{task}", sys.name, dry_run, info, js=js,
                        priority=priority)

    # Submit perfect supercell
    perfect_dir = df_root / "perfect"
    if perfect_dir.is_dir() and input_ready(perfect_dir):
        perfect_path = str(perfect_dir.resolve())
        perfect_state = js.latest(perfect_path)
        if convergence_verdict(perfect_dir).converged:
            if perfect_state != "converged":
                js.record(perfect_path, "converged", source="backfill")
        elif perfect_state not in ("submitted", "failed", "unconverged"):
            _submit_or_skip(perfect_dir, "df-perfect", sys.name, dry_run, info, js=js,
                            priority=priority)

    # Submit defect directories
    if df_root.is_dir() and not (df_root / "defect_energy_summary.json").is_file():
        from vasp_sop.vasp.io import (
            has_vasprun,
            recover_vasprun_artifacts,
            prepare_vasprun_recovery_run,
            seed_geometry_from_contcar,
        )

        # Charge-state chain grouping (ADR 0010): defect dirs sharing a name
        # (charge suffix stripped) form one chain; median charge(s) start
        # first and converged siblings seed the others' geometries.  Verdicts
        # are cached once per pass — every child below reads them.
        groups: dict[str, dict[str, Any]] = {}
        verdicts: dict[str, bool] = {}
        for c in sorted(df_root.iterdir()):
            if not c.is_dir() or c.name == "perfect":
                continue
            # ADR 0013: anion-cation antisites are excluded from the defect
            # set — never submitted by wave2 (poll path already untracks).
            if not is_valid_defect_dir(c):
                continue
            if not input_ready(c):
                continue
            q = _defect_charge(c.name)
            if q is None:
                continue
            g = groups.setdefault(
                _defect_group_key(c.name),
                {"dirs": [], "charges": set()},
            )
            g["dirs"].append(c)
            g["charges"].add(q)
            verdicts[str(c.resolve())] = convergence_verdict(c).converged
        for g in groups.values():
            g["roots"] = _chain_roots(sorted(g["charges"]))

        for child in sorted(df_root.iterdir()):
            if not child.is_dir() or child.name == "perfect":
                continue
            # ADR 0013 exclusion (mirrors the group-scan gate above).
            if not is_valid_defect_dir(child):
                continue
            if not input_ready(child):
                continue
            latest = js.latest(str(child.resolve()))
            if latest == "submitted":
                continue

            # Ion-converged but missing vasprun/calc_results -> recovery (#0016)
            if verdicts.get(str(child.resolve()), False):
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
                    child, f"df-vr-{child.name}", sys.name, dry_run, info, js=js,
                    priority=priority)
                if js.latest(str(child.resolve())) == "submitted":
                    js.record(
                        str(child.resolve()),
                        "submitted",
                        source="vasprun_recovery",
                        reason="vasprun_recovery",
                    )
                continue

            if latest == "converged":
                continue

            # ── Charge-state chain (ADR 0010) ─────────────────────────
            # Seeding applies ONLY to the first submission of a non-root
            # charge: a dir with submission history never re-seeds from a
            # sibling (its own partial CONTCAR is a better continuation —
            # see the restart branch below).  A dir with no history waits
            # for a converged sibling to seed its geometry.
            q = _defect_charge(child.name)
            g = groups.get(_defect_group_key(child.name)) if q is not None else None
            if g is not None and q not in g["roots"]:
                if not js.history(str(child.resolve())):
                    conv_siblings = [
                        c for c in g["dirs"]
                        if c is not child and verdicts.get(str(c.resolve()), False)
                    ]
                    if not conv_siblings:
                        logger.debug(
                            "%s: waiting for chain sibling (ADR 0010)", child.name
                        )
                        continue
                    src = min(
                        conv_siblings,
                        key=lambda c: abs((_defect_charge(c.name) or 0) - q),
                    )
                    if not seed_geometry_from_contcar(child, src):
                        # converged sibling without a usable CONTCAR — keep
                        # waiting rather than run from the pristine structure
                        continue
                    logger.info(
                        "%s: seeded geometry from %s (ADR 0010)",
                        child.name, src.name,
                    )
                    _submit_or_skip(
                        child, f"df-{child.name}", sys.name, dry_run, info, js=js,
                        source=f"seeded_from_{src.name}",
                        priority=priority,
                    )
                    continue

            if latest in ("failed", "unconverged", "pending"):
                # ADR 0010 revision: any dir that already ran once
                # continues from its own partial CONTCAR instead of
                # re-seeding from a sibling (or starting over).  Auto
                # restarts every cycle until convergence.
                if (child / "CONTCAR").is_file():
                    from vasp_sop.vasp.io import restart_from_contcar
                    try:
                        restart_from_contcar(child)
                    except Exception:
                        pass
                _submit_or_skip(
                    child, f"df-{child.name}", sys.name, dry_run, info,
                    js=js, source="restart",
                    priority=priority,
                )
                continue
            _submit_or_skip(child, f"df-{child.name}", sys.name, dry_run, info, js=js,
                            priority=priority)

    # ADR 0014: two-phase SOC — supplement converged non-SOC dirs.
    # Stage 1 converges without LSORBIT; stage 2 adds it (Bi_* dirs
    # continue from CONTCAR, everything else gets an NSW=0 single point).
    if sys.config.stage2_soc and df_root.is_dir():
        for child in sorted(df_root.iterdir()):
            if not child.is_dir() or not input_ready(child):
                continue
            if _stage2_soc_pending(child, js):
                _submit_stage2_soc(child, sys, js, dry_run, info,
                                   priority=priority)


# ── Wave 3 ─────────────────────────────────────────────────────────────────


def wave3_postprocess(
    sys: System, js: Any, dry_run: bool, *, log_to_logger: bool = False
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
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready
    from vasp_sop.core.jobs import move_crisp_outputs
    from vasp_sop.defect import cpd as _cpd
    from vasp_sop.defect import unitcell as _uc
    from vasp_sop.defect.analysis import analyze as _analyze_defects

    info = _make_info_fn(log_to_logger)
    cpd_root = sys.cpd_dir
    uc_root = sys.uc_dir
    df_root = sys.defect_dir

    p = sys.derive_phase(js)
    result: dict[str, Any] = {"phase": p}

    # ── CHEM_POT_DIAGRAM: CPD computation ────────────────────────────
    if p == "CHEM_POT_DIAGRAM":
        if not dry_run:
            for pd in cpd_root.iterdir():
                if pd.is_dir() and convergence_verdict(pd).converged:
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
                m = sys.mpid
                if f and m:
                    td = sys.target_dir
                    so = uc_root / "structure_opt"
                    _cpd.handoff_target_results(td, so, target_composition)
                    logger.info("%s structure_opt staged from target", sys.name)
            except Exception as exc:
                logger.error("%s CPD failed: %s", sys.name, exc)
                if not log_to_logger:
                    print(f"  ✗ {sys.name:<18} CPD post-processing FAILED")
                raise
        result["status"] = "cpd_done"
        return result

    # ── UNITCELL_DEFECT: post-processing ─────────────────────────────
    # Chemical-environment systems never run this leg (ADR 0005).
    if p != "UNITCELL_DEFECT" or sys.is_chemical_environment:
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
    from vasp_sop.vasp.io import check_task_complete as _ctc

    # UC done only when disk outputs are complete (not JobStore alone).
    uc_all_done = all(
        (not (uc_root / t / "INCAR").is_file()) or _ctc(uc_root / t, t)
        for t in ("band", "dos", "dielectric")
    )

    # Defect VASP finished: converged, failed, or not a calc dir.
    # ADR 0013-excluded dirs never run, so they must not block wave3.
    from vasp_sop.defect import is_valid_defect_dir as _valid_df

    def _df_job_finished(child: Path) -> bool:
        if not input_ready(child) or not _valid_df(child):
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
        if not input_ready(child) or not _valid_df(child):
            return True
        if js.latest(str(child.resolve())) in ("failed", "unconverged"):
            return True
        return (
            (child / "OUTCAR").is_file()
            or (child / "output" / "OUTCAR").is_file()
            or convergence_verdict(child).converged
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


# ── CPD-only entrypoint (issue #93) ────────────────────────────────────────


def cpd_only(
    root: Path,
    formula: str,
    config: Any,
    *,
    dry_run: bool = False,
    log_to_logger: bool = False,
) -> dict[str, Any]:
    """Run ONLY the CPD phase for a single system (issue #93).

    Creates a :class:`~vasp_sop.core.system.System`, submits competing
    phases (wave2), then runs CPD post-processing (wave3).  Stops before
    UNITCELL_DEFECT — no UC or defect work is performed.

    Parameters
    ----------
    root:
        System root directory (contains ``cpd/``, ``plan.yaml``).
    formula:
        Target chemical formula (e.g. ``"GaN"``).
    config:
        A :class:`~vasp_sop.core.config.PipelineConfig` or compatible object.
    dry_run:
        If True, do not submit VASP jobs.
    log_to_logger:
        If True, use logger instead of print for progress messages.

    Returns
    -------
    dict
        Status dict with ``"phase"`` and ``"status"`` keys.
    """
    from vasp_sop.core.job_store import JobStore

    root = Path(root)
    sys_obj = System(root, config)
    js = JobStore()
    info = _make_info_fn(log_to_logger)

    info(f"CPD-only mode for {sys_obj.name} (formula={formula})")

    # ── Wave 2: submit competing phases ──────────────────────────────
    phase = sys_obj.phase()
    if phase == "COMPETING":
        info(f"  Submitting competing phases ...")
        wave2_submit(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        # Re-check phase after submission
        phase = sys_obj.phase()

    # ── Wave 3: CPD post-processing ──────────────────────────────────
    if phase == "CHEM_POT_DIAGRAM":
        info(f"  Running CPD post-processing ...")
        result = wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        info(f"  CPD complete: {result.get('status', 'unknown')}")
        return result

    # If already past CPD or not yet ready
    if phase in ("UNITCELL_DEFECT", "COMPLETE"):
        info(f"  CPD already complete (phase={phase}), nothing to do.")
        return {"phase": phase, "status": "already_complete"}

    info(f"  System not ready for CPD (phase={phase}). "
         f"Run structure_opt and competing phases first.")
    return {"phase": phase, "status": "not_ready"}


# ══════════════════════════════════════════════════════════════════════════
# Whole-system advance + batch loop (moved from cli/main.py)
# ══════════════════════════════════════════════════════════════════════════

_MAX_RESTART = 5


def advance_one_system(
    s: dict, *, dry_run: bool = False, log_to_logger: bool = False,
    retry_failed: bool = False,
) -> None:
    """Advance one system by one cycle (runs serially in batch mode).

    Thin dispatcher — builds a :class:`~vasp_sop.core.system.System`,
    reads the (memory- or disk-derived) phase, and delegates to the wave
    functions.  After a phase's work completes the disk is re-derived and the
    result persisted (ADR 0001).
    """
    from vasp_sop.core.system import System
    from vasp_sop.core.job_store import JobStore

    _logger = logging.getLogger(__name__)

    sys_obj = System(s["root"], s["config"])
    js = JobStore()

    # ADR 0015: refresh competing phases when plan elements changed
    # (e.g. dopant added after cpd was fetched).  Cheap local check per
    # cycle; fetch+submit only on mismatch.
    if not dry_run:
        try:
            from vasp_sop.defect.cpd import ensure_cpd_phases
            n = ensure_cpd_phases(sys_obj.cpd_dir, sys_obj.config)
            if n:
                _logger.info("%s: cpd refresh submitted %d new phase(s).",
                             s["name"], n)
        except Exception as exc:
            _logger.warning("%s: cpd refresh failed (non-fatal): %s",
                            s["name"], exc)

    p = sys_obj.phase(js)

    # ── Failure gate ─────────────────────────────────────────────────
    if p == UNITCELL_DEFECT:
        failure = _unitcell_build_failure(s["root"])
        if failure:
            raise RuntimeError(
                f"unitcell blocked for {s['name']}: {failure['reason']}; "
                f"{failure['diagnostic']}"
            )
    if p == COMPLETE or p == NO_TARGET:
        return

    # ── Wave 1: STRUCTURE_OPT ────────────────────────────────────────
    if p == STRUCTURE_OPT:
        wave1_optimize(sys_obj, js, dry_run, log_to_logger=log_to_logger,
                       priority=s.get("priority", 0))
        # Re-derive from disk — the target may now be recorded as done
        p = sys_obj.derive_phase(js)

    # ── Wave 2: COMPETING (early return) ─────────────────────────────
    if p == COMPETING:
        wave2_submit(sys_obj, js, dry_run, log_to_logger=log_to_logger,
                     priority=s.get("priority", 0))
        return

    # ── Wave 3: CHEM_POT_DIAGRAM ─────────────────────────────────────
    if p == CHEM_POT_DIAGRAM:
        wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)

    # ── Wave 2 + 3: UNITCELL_DEFECT ─────────────────────────────────
    if p == UNITCELL_DEFECT:
        try:
            if dry_run:
                wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)
            wave2_submit(sys_obj, js, dry_run, log_to_logger=log_to_logger,
                         retry_failed=retry_failed,
                         priority=s.get("priority", 0))
            if not dry_run:
                wave3_postprocess(sys_obj, js, dry_run, log_to_logger=log_to_logger)
        except Exception as exc:
            _logger.error("%s UNITCELL_DEFECT failed: %s", s["name"], exc)
            if _unitcell_build_failure(sys_obj.root):
                raise
            if not log_to_logger:
                print(f"  ✗ {s['name']:<18} UNITCELL_DEFECT FAILED")

    # ── Post-cycle phase is re-derived from disk next cycle (ADR 0011) ─


class BatchOrchestrator:
    """Owns the JobStore handle and the batch poll loop.

    The CLI constructs one and calls :meth:`run`.  Every transition
    invariant — backfill, orphan sweep, converged finalize, restart
    policy, snapshots, system advance — lives behind this object.
    """

    def __init__(
        self,
        root: Path | list[Path],
        *,
        dry_run: bool = False,
        exclude: list[str] | None = None,
        poll_interval: int = 60,
        loop: bool = False,
        retry_failed: bool = False,
    ) -> None:
        from vasp_sop.core.job_store import JobStore

        # Ordered roots: earlier roots dispatch first (higher crisp
        # priority). Single-root callers keep legacy behaviour.
        self.roots: list[Path] = (
            [Path(root)] if isinstance(root, Path) else [Path(r) for r in root]
        )
        if not self.roots:
            raise ValueError("BatchOrchestrator needs at least one root")
        # Primary root: owns the loop's log file and snapshot. With a
        # single unified loop the log is one view over all roots.
        self.root = self.roots[0]
        self.dry_run = dry_run
        self.exclude = list(exclude or [])
        self.poll_interval = poll_interval
        self.loop = loop
        self.retry_failed = retry_failed
        self.js = JobStore()
        self.sw = None
        if loop:
            from vasp_sop.core.logging import setup_file_logging
            from vasp_sop.core.snapshot import SnapshotWriter

            setup_file_logging(self.root)
            self.sw = SnapshotWriter(self.root)

        self.systems: list[dict] = []
        self.blocked_systems: set[str] = set()
        self.first_pass = True

        self._collect_systems()

    def _dispatch_priority(self, wd: Path) -> int:
        """Crisp dispatch priority for a work dir.

        The root that contains *wd* decides; earlier roots in ``self.roots``
        get a higher priority (``10 * (n - 1 - index)``, so the first root
        is 10 and later roots step down by 10).  Anything outside every
        root falls back to 0 (legacy default).
        """
        resolved = wd.resolve()
        for index, root in enumerate(self.roots):
            if resolved.is_relative_to(root.resolve()):
                return 10 * (len(self.roots) - 1 - index)
        return 0

    def _collect_systems(self) -> None:
        from vasp_sop.core.config import PipelineConfig

        sys_list: list[dict] = []
        for root_index, root in enumerate(self.roots):
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                plan_path = d / "plan.yaml"
                if not plan_path.is_file():
                    continue
                try:
                    config = PipelineConfig.from_yaml(plan_path, root=d)
                except Exception:
                    continue
                src = config.poscar_src
                mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
                sys_list.append({
                    "name": d.name,
                    "root": d,
                    "config": config,
                    "formula": config.formula,
                    "mpid": mpid,
                    "root_index": root_index,
                    "priority": 10 * (len(self.roots) - 1 - root_index),
                })
        if self.exclude:
            sys_list = [s for s in sys_list if s["name"] not in self.exclude]
        self.systems = sys_list

    # ── logging helpers ─────────────────────────────────────────────

    def _print_info(self, message: str) -> None:
        if self.loop:
            logger.info("%s", message)
        else:
            print(message)

    # ── transitions ─────────────────────────────────────────────────

    def finalize_converged(self, wd: Path) -> None:
        """The one converged transition: move outputs, record, untrack."""
        from vasp_sop.core.jobs import move_crisp_outputs

        wd_str = str(wd.resolve())
        move_crisp_outputs(wd)
        self.js.record(wd_str, "converged")
        self.js.untrack(wd_str)

    def handle_unconverged(self, wd: Path) -> None:
        """VASP normal exit but unconverged — CONTCAR restart or give up."""
        from vasp_sop.vasp.io import (
            restart_from_contcar,
            seed_geometry_from_contcar,
        )
        from vasp_sop.core.jobs import submit_vasp
        from vasp_sop.vasp.convergence import convergence_verdict, is_stalled

        wd_str = str(wd.resolve())
        try:
            history = self.js.history(wd_str)
            attempt = history[-1].get("attempt", 0) if history else 0

            cur_verdict = convergence_verdict(wd)
            cur_f = cur_verdict.max_f if cur_verdict.max_f is not None else 0.0

            if cur_f > 0 and attempt > 0:
                for h in reversed(history):
                    reason = h.get("reason", "")
                    if reason.startswith("restart,"):
                        for part in reason.split(","):
                            if part.startswith("prev_f="):
                                prev_f = float(part.split("=")[1])
                                if is_stalled(prev_f, cur_f):
                                    self.js.record(
                                        wd_str, "failed",
                                        reason=f"stalled,max_f={cur_f:.4f}",
                                        attempt=attempt,
                                    )
                                    self.js.untrack(wd_str)
                                    logger.warning(
                                        "! %s stalled (max_f %.4f→%.4f), giving up",
                                        wd.name, prev_f, cur_f,
                                    )
                                    return
                                break
                        break

            if attempt >= _MAX_RESTART:
                self.js.record(
                    wd_str, "failed",
                    reason=f"unconverged,max_f={cur_f:.4f}",
                    attempt=attempt,
                )
                self.js.untrack(wd_str)
                logger.error(
                    "! %s unconverged after %d restart(s), giving up",
                    wd.name, attempt,
                )
                return

            # Charge-state chain (ADR 0010): a non-root defect restarts only
            # from a converged sibling's geometry (seed); without one it
            # waits for the chain instead of continuing its stale geometry.
            seeded = False
            q = _defect_charge(wd.name)
            if q is not None:
                conv_sib = None
                for cand in wd.parent.iterdir():
                    if cand.name == wd.name or not cand.is_dir():
                        continue
                    if _defect_group_key(cand.name) != _defect_group_key(wd.name):
                        continue
                    if convergence_verdict(cand).converged:
                        conv_sib = cand
                        break
                if conv_sib is not None:
                    seeded = seed_geometry_from_contcar(wd, conv_sib)
                else:
                    self.js.record(
                        wd_str, "unconverged", source="chain_wait",
                        attempt=attempt + 1,
                    )
                    self.js.untrack(wd_str)
                    logger.info(
                        "! %s waiting for chain sibling (ADR 0010)", wd.name
                    )
                    return

            if not seeded:
                restart_from_contcar(wd)
            job = submit_vasp(wd.resolve(), priority=self._dispatch_priority(wd))
            if getattr(job, "task_name", "") == "cached":
                # crisp has this exact calc cached — the cached result IS the
                # answer; re-running reproduces the same (unconverged) output,
                # so accept it as terminal instead of looping every cycle.
                self.js.record(
                    wd_str, "unconverged",
                    reason=f"cached_result,max_f={cur_f:.4f}",
                    attempt=attempt + 1,
                )
                self.js.untrack(wd_str)
                logger.warning(
                    "! %s result cached by crisp — accepting terminal "
                    "(max_f %.4f)", wd.name, cur_f,
                )
                return
            self.js.record(
                wd_str, "submitted",
                source=job.task_name, attempt=attempt + 1,
                reason=f"restart,prev_f={cur_f:.4f}",
            )
            logger.info(
                "→ %s restart #%d (max_f %.4f, %s)",
                wd.name, attempt + 1, cur_f, job.task_name,
            )
        except Exception as exc:
            logger.warning("%s unconverged handling failed: %s", wd.name, exc)

    # ── loop machinery ──────────────────────────────────────────────

    def _restore_crisp_active(self) -> None:
        """Populate JobStore from crisp's currently-running tasks."""
        from vasp_sop.core.jobs import crisp_active_dirs

        if self.dry_run:
            return
        active = crisp_active_dirs(skip=False)
        if active:
            logger.info(
                "Found %d active crisp tasks, recording in JobStore.",
                len(active),
            )
            for p in active:
                self.js.track(p)
                self.js.record(p, "submitted", source="restored")

    def _backfill(self) -> int:
        """Record already-converged CPD phases that never reached the store."""
        from vasp_sop.core.jobs import move_crisp_outputs
        from vasp_sop.vasp.convergence import convergence_verdict

        backfilled = 0
        for s in self.systems:
            cpd_root = s["root"] / "cpd"
            if not cpd_root.is_dir():
                continue
            for pd in cpd_root.iterdir():
                if not pd.is_dir() or "_mp-" not in pd.name:
                    continue
                if self.js.latest(str(pd.resolve())) == "converged":
                    continue
                if not convergence_verdict(pd).converged:
                    continue
                move_crisp_outputs(pd)
                backfilled += 1
                self.js.record(str(pd.resolve()), "converged", source="backfill")
        if backfilled:
            logger.info("Backfilled %d already-converged phase results.", backfilled)
        return backfilled

    def _orphan_sweep(self) -> int:
        """Promote legacy ``output/`` results from tracked subtrees."""
        from vasp_sop.core.jobs import move_crisp_outputs

        orphaned = 0
        for s in self.systems:
            for root_dir in (s["root"] / "unitcell", s["root"] / "defect"):
                if not root_dir.is_dir():
                    continue
                for child in root_dir.iterdir():
                    if not child.is_dir():
                        continue
                    out_dir = child / "output"
                    if not out_dir.is_dir():
                        continue
                    if not (out_dir / "OUTCAR").is_file():
                        continue
                    move_crisp_outputs(child)
                    orphaned += 1
        if orphaned:
            logger.info("Processed %d orphaned crisp outputs.", orphaned)
        return orphaned

    def _reconcile_stale(self) -> int:
        """Settle stale ``submitted`` records for untracked calc dirs.

        ``_poll_tracked`` only reconciles dirs still in the ``tracked``
        table (it owns live/pending dirs, complete with the 7-day orphan
        timeout).  A ``submitted`` record left behind on an *untracked* dir
        never settles on its own: wave2/wave3 skip dirs whose latest status
        is ``submitted``, so the analyze gate deadlocks (Ba_Se1_1 case) and
        a never-executed run hides forever.

        Untracked dirs that crisp no longer lists as live are settled from
        disk truth; everything else is left untouched:
        - converged verdict -> ``converged`` (outputs promoted)
        - OUTCAR without VASP's timing banner -> ``failed`` (vasp_crash)
        - normal exit, unconverged -> ``unconverged``
        - never ran (no OUTCAR) but inputs are installed -> ``failed``
          (orphaned) — matches the tracked-dir orphan policy; re-run is a
          human call via ``batch retry``
        - never ran AND inputs not installed -> untouched (a scope decision,
          not a machine one)
        """
        from vasp_sop.core.jobs import crisp_active_dirs, move_crisp_outputs
        from vasp_sop.vasp.convergence import convergence_verdict, _tail_text
        from vasp_sop.vasp.io import input_ready

        crispy = crisp_active_dirs(skip=self.dry_run)
        tracked = {r["dir_path"] for r in self.js.tracked_dirs()}
        settled = 0
        for s in self.systems:
            root = s["root"]
            for base in (root / "cpd", root / "unitcell", root / "defect"):
                if not base.is_dir():
                    continue
                for child in base.iterdir():
                    if not child.is_dir():
                        continue
                    cp = str(child.resolve())
                    if self.js.latest(cp) != "submitted":
                        continue
                    if cp in crispy or cp in tracked:
                        continue
                    if convergence_verdict(child).converged:
                        move_crisp_outputs(child)
                        self.js.record(cp, "converged", source="reconcile")
                        settled += 1
                        continue
                    outcar = child / "OUTCAR"
                    if not outcar.is_file():
                        outcar = child / "output" / "OUTCAR"
                    if not outcar.is_file():
                        if input_ready(child):
                            self.js.record(cp, "failed", reason="orphaned")
                            settled += 1
                        continue
                    tail = _tail_text(outcar, 4096)
                    if not tail or "General timing and accounting" not in tail:
                        self.js.record(cp, "failed", reason="vasp_crash")
                        settled += 1
                    else:
                        self.js.record(cp, "unconverged", source="reconcile")
                        settled += 1
        if settled:
            logger.info(
                "Settled %d stale submitted record(s) from disk truth.",
                settled,
            )
        return settled

    def _poll_tracked(self) -> int:
        """Poll tracked dirs: finalize converged, detect crashes, restart."""
        from vasp_sop.core.jobs import crisp_active_dirs
        from vasp_sop.vasp.convergence import convergence_verdict, _tail_text
        from vasp_sop.defect import is_valid_defect_dir

        completed = 0
        crispy = crisp_active_dirs(skip=True) if self.dry_run else crisp_active_dirs(skip=False)
        import time as _time
        for row in self.js.tracked_dirs():
            wd = Path(row["dir_path"])
            wd_str = str(wd.resolve())
            # ADR 0013: anion-cation antisites are excluded from the defect
            # set — never restart/resubmit them (wave2 already skips them;
            # the poll path must not resurrect them after a cancel).
            if "defect" in wd.parts and not is_valid_defect_dir(wd):
                self.js.untrack(wd_str)
                continue
            if wd_str in crispy:
                continue
            if convergence_verdict(wd).converged:
                self.finalize_converged(wd)
                completed += 1
                continue
            outcar = wd / "OUTCAR"
            if not outcar.is_file():
                outcar = wd / "output" / "OUTCAR"
            if not outcar.is_file():
                if _time.time() - row["submitted_at"] > 7 * 86400:
                    self.js.record(wd_str, "failed", reason="orphaned")
                    self.js.untrack(wd_str)
                continue
            tail = _tail_text(outcar, 4096)
            if not tail or "General timing and accounting" not in tail:
                self.js.record(wd_str, "failed", reason="vasp_crash")
                self.js.untrack(wd_str)
                continue
            self.handle_unconverged(wd)
        return completed

    def _advance_systems(self) -> tuple[int, list[tuple[str, str]]]:
        """Advance every system one cycle; return (skipped, errors)."""
        n_skipped = 0
        errors: list[tuple[str, str]] = []
        for idx, s in enumerate(self.systems, 1):
            name = s["name"]
            if name in self.blocked_systems:
                n_skipped += 1
                continue
            from vasp_sop.core.system import System

            p = System(s["root"], s["config"]).phase()
            failure = _unitcell_build_failure(s["root"])
            if p == UNITCELL_DEFECT and failure:
                self.blocked_systems.add(name)
                reason = failure["reason"]
                diagnostic = failure["diagnostic"]
                message = f"{name} blocked: unitcell {reason}; {diagnostic}"
                if self.loop:
                    logger.error(message)
                else:
                    print(f"  ✗ {message}")
                errors.append((name, reason))
                continue
            if p in (COMPLETE, NO_TARGET):
                n_skipped += 1
                continue

            if self.loop:
                try:
                    advance_one_system(s, dry_run=self.dry_run, log_to_logger=True, retry_failed=self.retry_failed)
                    logger.info(
                        "  [%d/%d] %-18s %s ... done", idx, len(self.systems), name, p
                    )
                except Exception as exc:
                    failure = _unitcell_build_failure(s["root"])
                    if failure:
                        self.blocked_systems.add(name)
                        reason = failure["reason"]
                    else:
                        reason = str(exc).split("(")[0].strip() or type(exc).__name__
                    logger.error("%s advance failed: %s", name, exc)
                    errors.append((name, reason))
            else:
                print(
                    f"  [{idx}/{len(self.systems)}] {name:<18} {p} ...",
                    end="", flush=True,
                )
                try:
                    advance_one_system(s, dry_run=self.dry_run, retry_failed=self.retry_failed)
                    print(" done")
                except Exception as exc:
                    reason = str(exc).split("(")[0].strip() or type(exc).__name__
                    logger.error("%s advance failed: %s", name, exc)
                    print(f" FAILED ({reason})")
                    errors.append((name, reason))
        return n_skipped, errors

    def _status(self, errors: list[tuple[str, str]]) -> tuple[dict, int]:
        """Aggregate phase counts, report errors, write snapshot; return (counts, done)."""
        from vasp_sop.core.system import System

        phases = [System(s["root"], s["config"]).phase() for s in self.systems]
        done_count = sum(
            1 for p in phases
            if p in (COMPLETE, NO_TARGET)
        )
        counts = {p: phases.count(p) for p in sorted(set(phases))}
        parts = [f"{p}={n}" for p, n in sorted(counts.items())]
        self._print_info("  ".join(parts))

        if errors:
            if self.loop:
                logger.warning("%d system(s) with errors:", len(errors))
                for name, reason in errors:
                    logger.warning("  %-18s  %s", name, reason)
            else:
                print(f"\n  ⚠ {len(errors)} system(s) with errors:")
                for name, reason in errors:
                    print(f"    {name:<18}  {reason}")

        if self.loop and self.sw is not None:
            self._write_snapshot(counts, errors)
        return counts, done_count

    def _write_snapshot(
        self, counts: dict, errors: list[tuple[str, str]]
    ) -> None:
        import json
        import subprocess

        from vasp_sop.defect.analysis import classify_analyze_status

        analyze_counts = {"full": 0, "partial": 0, "failed": 0}
        for s in self.systems:
            defect_root = s["root"] / "defect"
            if defect_root.is_dir():
                try:
                    analyze_counts[classify_analyze_status(defect_root)] += 1
                except Exception:
                    pass

        crisp_active = crisp_running = crisp_failed = -1
        try:
            result = subprocess.run(
                ["crisp", "jobs", "-a"], capture_output=True, text=True, timeout=30,
            )
            jobs = json.loads(result.stdout).get("jobs") or []
            project_jobs = [
                job for job in jobs
                if any(
                    (job.get("local_dir") or "").startswith(str(r))
                    for r in self.roots
                )
            ]
            crisp_active = sum(
                1 for job in project_jobs
                if job.get("status") in (
                    "submit", "submitted", "running", "ready_fetch", "pending",
                )
            )
            crisp_running = sum(
                1 for job in project_jobs if job.get("status") == "running"
            )
            crisp_failed = sum(
                1 for job in project_jobs if job.get("status") == "failed"
            )
        except Exception:
            pass

        self.sw.write({
            "phases": dict(counts),
            "analyze": analyze_counts,
            "crisp_active": crisp_active,
            "crisp_running": crisp_running,
            "crisp_failed": crisp_failed,
            "errors": [
                {"system": name, "reason": reason} for name, reason in errors
            ],
        })

    # ── public entry ────────────────────────────────────────────────

    def run(self, max_cycles: int | None = None) -> None:
        """Run the batch cycle: one pass (single-shot) or continuous loop.

        *max_cycles* bounds the loop (used by the single-system ``pipeline``
        command); when exhausted without completion the loop just exits and
        the caller checks the final phase.
        """
        from vasp_sop.core.batch_lifecycle import is_stop_requested

        if not self.systems:
            logger.warning("No systems found.")
            self.js.close()
            return
        self._print_info(f"Batch run: {len(self.systems)} systems\n")
        if self.dry_run:
            self._print_info(
                "Dry-run mode: will build defect structures and generate "
                "inputs, NO VASP submission.\n"
            )

        self._restore_crisp_active()

        cycle = 0
        try:
            while not is_stop_requested():
                cycle += 1
                if not self.dry_run:
                    self._backfill()
                    self._orphan_sweep()
                    completed = self._poll_tracked()
                    if completed:
                        self._print_info(
                            f"  Finalized {completed} completed calculation(s)."
                        )
                    settled = self._reconcile_stale()
                    if settled:
                        self._print_info(
                            f"  Settled {settled} stale submitted record(s) "
                            "from disk truth."
                        )

                n_skipped, errors = self._advance_systems()

                if n_skipped:
                    self._print_info(
                        f"  [{n_skipped}/{len(self.systems)} systems already done, "
                        "skipped]\n"
                    )

                counts, done_count = self._status(errors)

                terminal_count = done_count + len(self.blocked_systems)
                if done_count == len(self.systems):
                    self._print_info("\nAll systems complete.")
                    break
                if self.loop and terminal_count == len(self.systems):
                    self._print_info("\nAll systems complete or blocked.")
                    break

                if max_cycles is not None and cycle >= max_cycles:
                    logger.warning(
                        "Batch loop reached the %d-cycle cap; exiting "
                        "(caller verifies final phase).",
                        max_cycles,
                    )
                    break

                if not self.loop:
                    still = len(self.systems) - done_count
                    blocked = len(errors)
                    running = still - blocked
                    print(
                        f"\n{running} running, {blocked} blocked, {still} remaining "
                        "— re-run `vasp-sop batch run .` after VASP jobs complete."
                    )
                    break

                self._print_info(
                    f"\n  Sleeping {self.poll_interval}s … (Ctrl+C to interrupt)"
                )
                import time as _time

                _time.sleep(self.poll_interval)
                self.first_pass = False
        except KeyboardInterrupt:
            self._print_info("\nInterrupted.")
        finally:
            self.js.close()
