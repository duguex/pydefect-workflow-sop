"""Defect VASP execution — submit, monitor, restart stalled jobs."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from vasp_sop.vasp.io import check_converged, input_ready, parse_max_force, restart_from_contcar
from vasp_sop.core.jobs import move_crisp_outputs, submit_vasp
from vasp_sop.vasp.errors import diagnose_failure, recommended_fix
from vasp_sop.vasp.auto_heal import apply_correction

logger = logging.getLogger(__name__)


def run_vasp(defect_root: Path) -> None:
    """Submit perfect + all defect VASP jobs, with CONTCAR restart for timeouts.

    Loops until all jobs converge or no more progress (max_f stops decreasing).
    """
    perfect_dir = defect_root / "perfect"
    if not perfect_dir.is_dir():
        raise RuntimeError(
            f"Perfect supercell directory not found at {perfect_dir}."
        )

    def _collect_jobs() -> list[Path]:
        from vasp_sop.defect import is_valid_defect_dir
        result = []
        if not check_converged(perfect_dir):
            result.append(perfect_dir)
        for child in sorted(defect_root.iterdir()):
            if not child.is_dir() or child.name == "perfect":
                continue
            if not is_valid_defect_dir(child):
                continue
            if not input_ready(child):
                continue
            if not check_converged(child):
                result.append(child)
        return result


    prev_forces: dict[str, float] = {}
    stalled: set[str] = set()

    for attempt in range(20):
        dirs = _collect_jobs()
        if not dirs:
            break
        corrected: set[str] = set()

        for d in dirs:
            if (d / "CONTCAR").is_file() and not check_converged(d):
                dirname = d.name
                old_f = prev_forces.get(dirname, 999.0)
                cur_f = max(parse_max_force(d), 0.0)
                if cur_f > 0 and cur_f >= old_f * 0.99:
                    stalled.add(dirname)
                    failure = diagnose_failure(d / "OUTCAR")
                    logger.info(
                        "Stalled %s (max_f %.4f -> %.4f)%s",
                        dirname, old_f, cur_f,
                        f", diagnosed: {failure}" if failure else "",
                    )
                    if failure:
                        fix = recommended_fix(failure)
                        if fix:
                            logger.info("  Suggested fix for %s: %s", dirname, fix)
                else:
                    stalled.discard(dirname)
                prev_forces[dirname] = cur_f

                if dirname not in stalled:
                    logger.info(
                        "Restarting %s from CONTCAR (attempt %d, max_f=%.4f)",
                        dirname, attempt + 1, cur_f,
                    )
                    restart_from_contcar(d)
                elif apply_correction(d, diagnose_failure(d / "OUTCAR"), attempt + 1):
                    logger.warning("Recovered stalled %s via auto-heal", dirname)
                    corrected.add(dirname)

        # Only submit non-stalled jobs
        active = [d for d in dirs if d.name not in stalled or d.name in corrected]
        if not active:
            logger.info("All remaining jobs stalled. Giving up.")
            break
        logger.info("Submitting %d VASP job(s) (attempt %d)", len(active), attempt + 1)
        jobs = [submit_vasp(d.resolve()) for d in active]

        # Poll with retry (don't raise on individual failure)
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
                time.sleep(60)

    still_incomplete = [d.name for d in _collect_jobs()]
    if still_incomplete:
        logger.warning(
            "Defect VASP still incomplete after %d attempts: %s",
            attempt + 1, ", ".join(still_incomplete),
        )
