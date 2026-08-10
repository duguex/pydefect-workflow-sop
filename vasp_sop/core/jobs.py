"""VASP job submission and batch orchestration.

Provides :func:`submit_vasp` and :func:`wait_all` for pipeline stages.

Two backends:
- **local** — ``subprocess.Popen``, returns immediately.
- **crisp** — ``crisp submit``, returns immediately with a task_name.

The backend is selected automatically: crisp takes precedence when
the ``crisp`` CLI is available on ``PATH``.
"""

from __future__ import annotations

import json
import os
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Calculations with max lattice vector > MAX_LATTICE are skipped
# (not submitted). Set to None to disable.
MAX_LATTICE: float | None = 25.0


def lattice_too_large(src_dir: Path) -> bool:
    """True if max lattice vector exceeds MAX_LATTICE."""
    if MAX_LATTICE is None:
        return False
    try:
        from pymatgen.core.structure import Structure

        for cand in (Path(src_dir) / "CONTCAR", Path(src_dir) / "POSCAR"):
            if cand.is_file():
                a, b, c = Structure.from_file(str(cand)).lattice.abc
                return max(a, b, c) > MAX_LATTICE
    except Exception:
        return False
    return False


def _poscar_natoms(src_dir: Path) -> int:
    """Atom count from a POSCAR/CONTCAR (0 if unreadable)."""
    try:
        from pymatgen.core.structure import Structure

        for cand in (Path(src_dir) / "POSCAR", Path(src_dir) / "CONTCAR"):
            if cand.is_file():
                return len(Structure.from_file(str(cand)))
    except Exception:
        pass
    return 0


def crisp_active_dirs(*, skip: bool = False) -> set[str]:
    """Return work dirs of crisp jobs currently in a live status.

    The single seam for "what is crisp running right now": used by the batch
    poll loop (dedup against tracked dirs) and by crisp submission.
    When *skip* is True (e.g. dry-run), short-circuit and return an empty set
    without spawning the subprocess. This avoids a 30 s wait on `crisp jobs`
    when no submission is actually happening.
    """
    if skip:
        return set()
    try:
        r = subprocess.run(["crisp", "jobs"], capture_output=True, text=True, timeout=30)
        raw = json.loads(r.stdout)
        jobs = raw.get("jobs", raw.get("data", {}).get("jobs", []))
    except Exception:
        return set()
    alive = {"submit", "submitted", "running", "ready_fetch"}
    return {j.get("local_dir", "") for j in jobs
            if j.get("status") in alive and j.get("local_dir")}


# ══════════════════════════════════════════════════════════════════════════
# VaspJob hierarchy
# ══════════════════════════════════════════════════════════════════════════


class VaspJob:
    """Handle for a submitted VASP calculation.  Subclass defines ``poll``."""

    work_dir: Path
    _returncode: Optional[int] = None

    def __init__(self, work_dir: Path):
        self.work_dir = work_dir

    @property
    def done(self) -> bool:
        return self._returncode is not None

    @property
    def task_name(self) -> str | None:
        return None

    def poll(self) -> Optional[int]:
        """Non‑blocking status check.  Returns exit code or ``None``."""
        raise NotImplementedError

    def wait(self, poll_interval: int = 60) -> int:
        """Block until done."""
        while self._returncode is None:
            self.poll()
            if self._returncode is None:
                time.sleep(poll_interval)
        return self._returncode


class LocalVaspJob(VaspJob):
    """Wraps a ``subprocess.Popen``."""

    _proc: subprocess.Popen

    def __init__(self, work_dir: Path, proc: subprocess.Popen):
        super().__init__(work_dir)
        self._proc = proc

    def poll(self) -> Optional[int]:
        if self._returncode is not None:
            return self._returncode
        rc = self._proc.poll()
        if rc is not None:
            self._returncode = rc
        return rc


class CrispVaspJob(VaspJob):
    """Wraps a crisp task."""

    _task_name: str
    _poll_attempts: int = 0

    _STATUS_MAP = {
        "completed": 0,
        "failed": 1,
        "cancelled": 1,
    }

    def __init__(self, work_dir: Path, task_name: str):
        super().__init__(work_dir)
        self._task_name = task_name

    @property
    def task_name(self) -> str | None:
        return self._task_name

    def poll(self) -> Optional[int]:
        if self._returncode is not None:
            return self._returncode
        self._poll_attempts += 1
        try:
            result = subprocess.run(
                ["crisp", "jobs", "-n", self._task_name, "--refresh"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                return None
            payload = json.loads(result.stdout)
            status = (payload.get("job") or {}).get("status", "")
            if status in self._STATUS_MAP:
                self._returncode = self._STATUS_MAP[status]
                logger.info(
                    "crisp job %s finished: %s (exit %d)",
                    self._task_name, status, self._returncode,
                )
            return self._returncode
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════════════
# Backend detection
# ══════════════════════════════════════════════════════════════════════════

_CRISP_AVAILABLE: Optional[bool] = None


def _crisp_available() -> bool:
    global _CRISP_AVAILABLE
    if _CRISP_AVAILABLE is None:
        try:
            subprocess.run(
                ["crisp", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            _CRISP_AVAILABLE = True
            logger.info("crisp detected — using cluster submission.")
        except FileNotFoundError:
            _CRISP_AVAILABLE = False
            logger.info("crisp not found — using local subprocess.")
    return _CRISP_AVAILABLE


# ══════════════════════════════════════════════════════════════════════════
# Submission
# ══════════════════════════════════════════════════════════════════════════


def submit_vasp(
    work_dir: Path,
    nproc: int = 4,
    vasp_cmd: str = "mpirun -np {nproc} vasp_std",
    priority: int = 0,
) -> VaspJob:
    """Launch VASP in *work_dir* and return a handle.

    Backend: crisp (if available) or local ``Popen``.

    *priority* is the crisp dispatch priority (higher dispatches first);
    the batch orchestrator derives it from the system's root.
    """
    if not _vasp_input_ready(work_dir):
        raise RuntimeError(f"VASP input files not complete in {work_dir}.")

    if lattice_too_large(work_dir):
        raise RuntimeError(
            f"Lattice too large in {work_dir} "
            f"(max_abc > {MAX_LATTICE} Å, skipped)")
    if _crisp_available():
        return _crisp_submit(work_dir, priority=priority)
    return _local_submit(work_dir, nproc, vasp_cmd)


def _local_submit(
    work_dir: Path, nproc: int, vasp_cmd: str,
) -> LocalVaspJob:
    cmd = vasp_cmd.format(nproc=nproc)
    logger.info("Submit local VASP in %s: %s", work_dir, cmd)
    proc = subprocess.Popen(
        cmd.split(),
        cwd=str(work_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return LocalVaspJob(work_dir, proc)


def _crisp_submit(work_dir: Path, priority: int = 0) -> CrispVaspJob:
    logger.info("Submit crisp VASP in %s", work_dir)
    # ── Dedup: crisp agent.db can have active jobs unknown to JobStore ─
    import json as _json, sqlite3 as _sqlite3
    _agent_db = Path.home() / ".crisp" / "data" / "agent.db"
    if _agent_db.is_file():
        try:
            _conn = _sqlite3.connect(str(_agent_db), timeout=5)
            _cur = _conn.execute(
                "SELECT task_name, status FROM jobs WHERE local_dir = ? "
                "AND status IN ('submit','submitted','running','ready_fetch')",
                (str(work_dir.resolve()),),
            )
            _row = _cur.fetchone()
            _conn.close()
            if _row is not None:
                _existing_task, _existing_status = _row
                logger.info(
                    "crisp job %s already %s for %s — skipping duplicate submit",
                    _existing_task, _existing_status, work_dir.name,
                )
                return CrispVaspJob(work_dir, _existing_task)
        except Exception:
            logger.warning("agent.db dedup check failed, falling through to submit")
    submit_cmd = ["crisp", "submit"]
    if priority:
        submit_cmd += ["--priority", str(priority)]
    # Pin big defect supercells (>150 atoms) to long-QOS clusters via the
    # "long" cluster tag so long relaxations are not killed by short-QOS
    # time limits. (crisp's result-cache auto paths were retired 2026-08-11
    # — there is no --no-cache flag anymore.)
    _is_defect = "/defect/" in str(work_dir)
    if _is_defect:
        try:
            _nats = _poscar_natoms(work_dir)
        except Exception:
            _nats = 0
        if _nats > 150:
            submit_cmd += ["--tag", "long"]
    result = subprocess.run(
        submit_cmd,
        cwd=str(work_dir),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crisp submit failed in {work_dir}:\n{result.stderr.strip()}"
        )
    payload = json.loads(result.stdout)
    data = payload.get("data") or {}
    task_name = data.get("task_name") or payload.get("task_name")
    if not task_name:
        raise RuntimeError(f"crisp submit missing task_name: {payload}")
    logger.info("crisp task %s submitted for %s", task_name, work_dir.name)
    return CrispVaspJob(work_dir, task_name)


# ══════════════════════════════════════════════════════════════════════════
# Batch wait
# ══════════════════════════════════════════════════════════════════════════


def wait_all(jobs: list[VaspJob], poll_interval: int = 60) -> None:
    """Wait for all jobs to finish.  Raises RuntimeError on any failure."""
    pending = list(jobs)
    while pending:
        for j in list(pending):
            rc = j.poll()
            if rc is not None:
                pending.remove(j)
                logger.info(
                    "Job %s done (exit %d), %d remaining",
                    j.work_dir.name, rc, len(pending),
                )
                if rc != 0:
                    for other_job, other_proc in list(pending):
                        other_proc.terminate()
                    raise RuntimeError(
                        f"VASP failed in {j.work_dir} (exit code {rc}); "
                        f"terminated {len(pending)} pending jobs."
                    )
        if pending:
            time.sleep(poll_interval)


def crisp_terminal_status(work_dir: Path) -> str | None:
    """Return the latest local CRISP terminal marker, if present.

    A failure marker wins when both markers exist; the submit adapter is
    responsible for clearing the opposite marker on a normal retry.
    """
    work_dir = Path(work_dir)
    if (work_dir / ".failed").is_file():
        return "failed"
    if (work_dir / ".completed").is_file():
        return "completed"
    return None


def move_crisp_outputs(work_dir: Path) -> None:
    """Promote legacy crisp ``output/`` into *work_dir* (mtime-preferring).

    **Current crisp** writes VASP outputs **directly** into ``work_dir`` (no
    ``output/``). Call is then a no-op. Slurm logs are ``{jobid}.log``.

    **Legacy** jobs may still have ``work_dir/output/``. For each entry:

    - missing at root → move up
    - both exist as files → keep the **newer mtime**; drop the older copy
    - directories → merge with the same mtime rule

    Removes ``output/`` when finished (if present). Safe to call always.
    """
    output_dir = work_dir / "output"
    if not output_dir.is_dir():
        return
    import shutil

    for f in list(output_dir.iterdir()):
        dest = work_dir / f.name
        if f.is_dir():
            if dest.exists() and dest.is_dir():
                # merge then drop source; nested files use same mtime rule
                for child in list(f.rglob("*")):
                    if not child.is_file():
                        continue
                    rel = child.relative_to(f)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.move(str(child), str(target))
                    else:
                        try:
                            if child.stat().st_mtime >= target.stat().st_mtime:
                                target.unlink()
                                shutil.move(str(child), str(target))
                            else:
                                child.unlink()
                        except OSError:
                            try:
                                child.unlink()
                            except OSError:
                                pass
                shutil.rmtree(str(f), ignore_errors=True)
            else:
                if dest.exists():
                    # dest is a file, source is dir — prefer newer tree if possible
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                shutil.move(str(f), str(dest))
            continue

        # file
        if not dest.exists():
            shutil.move(str(f), str(dest))
            continue
        try:
            src_m = f.stat().st_mtime
            dst_m = dest.stat().st_mtime
        except OSError:
            try:
                f.unlink()
            except OSError:
                pass
            continue
        if src_m >= dst_m:
            # output/ is newer or equal → replace root
            try:
                dest.unlink()
            except OSError:
                pass
            shutil.move(str(f), str(dest))
        else:
            # root is newer → drop stale output copy
            try:
                f.unlink()
            except OSError:
                pass

    if output_dir.is_dir():
        try:
            # remove if empty or only empty dirs left
            shutil.rmtree(str(output_dir), ignore_errors=True)
        except OSError:
            pass



# ══════════════════════════════════════════════════════════════════════════
# CLI helper
# ══════════════════════════════════════════════════════════════════════════


def run_local(
    cmd: str, cwd: Path, timeout: int = 600, shell: bool = True
) -> subprocess.CompletedProcess:
    """Run a non‑intensive command locally (pydefect / vise CLI).

    Raises RuntimeError on non‑zero exit, TimeoutError on timeout.
    """
    # Ensure conda environment bins come before ~/.local/bin in the
    # subprocess PATH, otherwise a stale system-wide CLI (e.g. vise)
    # may use an incompatible Python/numpy version.
    env = None
    _path = os.environ.get("PATH", "")
    if ".local/bin" in _path:
        parts = _path.split(":")
        local_dirs = [p for p in parts if ".local/bin" in p]
        conda_dirs = [p for p in parts if "conda" in p and "bin" in p]
        other_dirs = [p for p in parts if p not in local_dirs and p not in conda_dirs]
        env = {**os.environ, "PATH": ":".join(conda_dirs + other_dirs + local_dirs)}
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            env=env,
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
    return all((path / f).is_file() for f in ("INCAR", "POSCAR", "POTCAR", "KPOINTS"))
