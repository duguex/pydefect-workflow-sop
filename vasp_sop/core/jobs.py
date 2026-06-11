"""VASP job submission and batch orchestration.

``vasp_sop`` does **not** interact with cluster schedulers directly.
It provides a two-phase interface:

1. :func:`submit_vasp` — launch VASP and return a :class:`VaspJob` handle.
2. :func:`wait_all` — block until every job in a batch finishes.

Pipeline stages submit independent calculations in parallel,
then wait at dependency boundaries.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# VaspJob — handle for a running / submitted VASP calculation
# ══════════════════════════════════════════════════════════════════════════


class VaspJob:
    """Handle for a submitted VASP calculation.

    Use :func:`wait_all` to block until a set of jobs finishes.
    """

    work_dir: Path
    _process: Optional[subprocess.Popen]
    _returncode: Optional[int]

    def __init__(self, work_dir: Path, process: Optional[subprocess.Popen] = None):
        self.work_dir = work_dir
        self._process = process
        self._returncode = None

    @property
    def done(self) -> bool:
        return self._returncode is not None

    def poll(self) -> Optional[int]:
        if self._returncode is not None:
            return self._returncode
        if self._process is None:
            return None
        rc = self._process.poll()
        if rc is not None:
            self._returncode = rc
        return rc


# ══════════════════════════════════════════════════════════════════════════
# Submission & batch wait
# ══════════════════════════════════════════════════════════════════════════


def submit_vasp(
    work_dir: Path,
    nproc: int = 4,
    vasp_cmd: str = "mpirun -np {nproc} vasp_std",
) -> VaspJob:
    """Launch VASP in *work_dir* asynchronously.

    Returns a :class:`VaspJob` handle immediately.
    Call :func:`wait_all` to block on a batch.
    """
    if not _vasp_input_ready(work_dir):
        raise RuntimeError(
            f"VASP input files not complete in {work_dir}."
        )

    cmd = vasp_cmd.format(nproc=nproc)
    logger.info("Submit VASP in %s: %s", work_dir, cmd)

    proc = subprocess.Popen(
        cmd.split(),
        cwd=str(work_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return VaspJob(work_dir=work_dir, process=proc)


def wait_all(jobs: list[VaspJob]) -> None:
    """Wait for all jobs to finish.  Raises RuntimeError on any failure."""
    pending = list(jobs)
    while pending:
        for j in list(pending):
            rc = j.poll()
            if rc is not None:
                pending.remove(j)
                if rc != 0:
                    raise RuntimeError(
                        f"VASP failed in {j.work_dir} (exit code {rc})."
                    )


# ══════════════════════════════════════════════════════════════════════════
# Local CLI helper (pydefect, vise, …)
# ══════════════════════════════════════════════════════════════════════════


def run_local(
    cmd: str, cwd: Path, timeout: int = 600, shell: bool = True
) -> subprocess.CompletedProcess:
    """Run a non‑intensive command locally (pydefect / vise CLI).

    Raises RuntimeError on non‑zero exit, TimeoutError on timeout.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
    except subprocess.TimeoutExpired as e:
        raise TimeoutError(
            f"Command timed out after {timeout}s in {cwd}:\n"
            f"  $ {cmd}\n"
            f"  stdout: {(e.stdout or '')[:1024]}\n"
            f"  stderr: {(e.stderr or '')[:1024]}"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed in {cwd} (exit {result.returncode}):\n"
            f"  $ {cmd}\n"
            f"  stdout: {result.stdout.strip()[:2048]}\n"
            f"  stderr: {result.stderr.strip()[:2048]}"
        )
    return result


def _vasp_input_ready(path: Path) -> bool:
    """Check that all four required VASP input files exist."""
    return all((path / f).is_file() for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"))
