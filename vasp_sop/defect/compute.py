"""Defect VASP execution — submit, monitor, restart stalled jobs."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from vasp_sop.vasp.io import check_converged, input_ready, restart_from_contcar
from vasp_sop.core.jobs import move_crisp_outputs, submit_vasp
from vasp_sop.vasp.errors import diagnose_failure, recommended_fix

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
        result = []
        if not check_converged(perfect_dir):
            result.append(perfect_dir)
        for child in sorted(defect_root.iterdir()):
            if not child.is_dir() or child.name == "perfect":
                continue
            if not input_ready(child):
                continue
            if not check_converged(child):
                result.append(child)
        return result

    def _max_f(path: Path) -> float:
        """Extract max force from OUTCAR (0 if unavailable)."""
        for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
            if cand.is_file():
                text = cand.read_text()
                idx = text.rfind("TOTAL-FORCE (eV/Angst)")
                if idx < 0:
                    return 0.0
                mf = 0.0
                for line in text[idx:].splitlines()[2:]:
                    p = line.strip().split()
                    if len(p) < 6:
                        break
                    try:
                        mf = max(mf, abs(float(p[3])), abs(float(p[4])), abs(float(p[5])))
                    except ValueError:
                        break
                return mf
        return 0.0

    prev_forces: dict[str, float] = {}
    stalled: set[str] = set()

    for attempt in range(20):
        dirs = _collect_jobs()
        if not dirs:
            break

        for d in dirs:
            if (d / "CONTCAR").is_file() and not check_converged(d):
                dirname = d.name
                old_f = prev_forces.get(dirname, 999.0)
                cur_f = _max_f(d)
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
                else:
                    # Stalled: auto-recover with POTIM increase, but keep
                    # in stalled set so this iteration skips submission.
                    logger.warning("Recovering stalled %s (max_f=%.4f)", dirname, cur_f)
                    incar_path = d / "INCAR"
                    if incar_path.is_file():
                        from pymatgen.io.vasp.inputs import Incar
                        incar = Incar.from_file(str(incar_path))
                        current_potim = incar.get("POTIM", 0.5)
                        new_potim = min(current_potim * 1.5, 5.0)
                        incar["POTIM"] = new_potim
                        incar.write_file(str(incar_path))
                        logger.info("  POTIM %.2f -> %.2f for %s", current_potim, new_potim, dirname)
                    restart_from_contcar(d)

        # Only submit non-stalled jobs
        active = [d for d in dirs if d.name not in stalled]
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
