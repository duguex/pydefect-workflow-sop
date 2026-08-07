"""Convergence verdict — the single authority on whether a VASP calculation converged.

One deep module: evidence (OUTCAR) in, :class:`ConvergenceVerdict` out.
Consumed by every caller that used to re-derive the "is this calc converged?"
rule (job polling, pydefect reconciliation, stall detection, task completion).

Rules (moved verbatim from the old ``vasp/io.py::check_converged``):

1. OUTCAR must exist and contain ``General timing and accounting`` (VASP
   finished writing results).
2. Run parameters prefer **OUTCAR** (this job) over **INCAR** (may already be
   edited for a CONTCAR restart / NSW bump).
3. Single-point / DFPT / MD (``NSW≤1`` or ``IBRION∉{1,2,3}``), and any task
   whose type is in :data:`NO_FORCE_GATE_TASK_TYPES`: timing only.
4. Ionic relaxation (``IBRION∈{1,2,3}`` and ``NSW>1``):
   - ``EDIFFG < 0`` (force criterion): hard gate ``max|F| ≤ |EDIFFG|`` on the
     last TOTAL-FORCE block.
   - Else (energy criterion / missing forces): ``n_ionic < NSW_run``
     (pymatgen-style early-exit heuristic).

Never raises. The module is standalone — it does not import ``vasp/io.py``.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass
from pathlib import Path

# Task types that never undergo ionic relaxation — the force gate is N/A for
# them even if their INCAR happens to carry relaxation tags.
NO_FORCE_GATE_TASK_TYPES = frozenset({"band", "dos", "dielectric"})

# A relaxation is considered stalled when the ionic max|F| fails to improve
# by at least this factor between consecutive evaluations.
STALL_FRACTION = 0.99

# Reason vocabulary for ConvergenceVerdict.reason.
REASON_MISSING_OUTCAR = "missing_outcar"
REASON_TRUNCATED = "truncated"
REASON_NOT_RELAXATION = "not_relaxation"
REASON_FORCE_GATE = "force_gate"
REASON_FORCE_GATE_FAIL = "force_gate_fail"
REASON_MISSING_FORCES = "missing_forces"
REASON_NSW_EARLY_EXIT = "nsw_early_exit"
REASON_NSW_EXHAUSTED = "nsw_exhausted"

_FORCE_HDR = "TOTAL-FORCE (eV/Angst)"
_TIMING_MARK = "General timing and accounting"

_NSW_RE = _re.compile(r"NSW\s*=\s*(\d+)", _re.I)
_IBRION_RE = _re.compile(r"IBRION\s*=\s*(-?\d+)", _re.I)
_EDIFFG_RE = _re.compile(
    r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", _re.I
)

# mtime-based memoisation: avoid re-reading unchanged OUTCARs across
# successive batch-run cycles.
_verdict_cache: dict[Path, tuple[float, "ConvergenceVerdict"]] = {}


@dataclass(frozen=True)
class ConvergenceVerdict:
    """The answer to "is this calculation converged?", with provenance.

    ``converged`` is the single truth consumers branch on. ``reason`` names
    which rule produced it. ``max_f`` is the last ionic max|F| (vector
    magnitude, ``None`` when unavailable); ``n_ionic`` the number of completed
    ionic steps (``None`` when not applicable).
    """

    converged: bool
    reason: str
    max_f: float | None = None
    n_ionic: int | None = None


def is_stalled(prev_max_f: float | None, cur_max_f: float | None) -> bool:
    """True when ionic force progress is not improving.

    Non-decreasing max|F| between consecutive evaluations means the relaxation
    is making no progress. ``None`` inputs (no force data) are never "stalled".
    """
    if prev_max_f is None or cur_max_f is None:
        return False
    if cur_max_f <= 0:
        return False
    return cur_max_f >= prev_max_f * STALL_FRACTION


def convergence_verdict(path: Path, task_type: str = "") -> ConvergenceVerdict:
    """Return the convergence verdict for the calculation directory *path*.

    *task_type* optionally names the calculation's job type; types in
    :data:`NO_FORCE_GATE_TASK_TYPES` skip the ionic force gate entirely
    (their convergence is "the job finished"). Never raises.
    """
    outcar: Path | None = None
    for cand in (path / "OUTCAR", path / "output" / "OUTCAR"):
        if cand.is_file():
            outcar = cand
            break
    if outcar is None:
        return ConvergenceVerdict(False, REASON_MISSING_OUTCAR)

    try:
        mtime = outcar.stat().st_mtime
    except OSError:
        return ConvergenceVerdict(False, REASON_MISSING_OUTCAR)
    cached = _verdict_cache.get(outcar)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    tail = _tail_text(outcar, n=8192)
    if tail is None or _TIMING_MARK not in tail:
        # Still report last-block force when available: stall detection reads
        # it on OUTCARs whose run crashed before the timing marker was written.
        verdict = ConvergenceVerdict(
            False, REASON_TRUNCATED, max_f=_last_max_force(outcar)
        )
        _verdict_cache[outcar] = (mtime, verdict)
        return verdict

    # Task types that never ionically relax: a finished job is the verdict.
    if task_type in NO_FORCE_GATE_TASK_TYPES:
        verdict = ConvergenceVerdict(True, REASON_NOT_RELAXATION)
        _verdict_cache[outcar] = (mtime, verdict)
        return verdict

    # Prefer OUTCAR tags (run that produced this OUTCAR); INCAR is fallback only.
    head = _head_text(outcar, 65536)
    nsw, ibrion, ediffg = _parse_run_tags(head)
    # OUTCAR sometimes prints tags late in the file (rare); also search tail.
    if nsw is None or ibrion is None or ediffg is None:
        t_nsw, t_ibr, t_ed = _parse_run_tags(tail)
        if nsw is None:
            nsw = t_nsw
        if ibrion is None:
            ibrion = t_ibr
        if ediffg is None:
            ediffg = t_ed

    incar_path = path / "INCAR"
    if incar_path.is_file() and (nsw is None or ibrion is None or ediffg is None):
        try:
            incar_text = incar_path.read_text()[:8192]
        except OSError:
            incar_text = ""
        i_nsw, i_ibr, i_ed = _parse_run_tags(incar_text)
        if nsw is None:
            nsw = i_nsw
        if ibrion is None:
            ibrion = i_ibr
        if ediffg is None:
            ediffg = i_ed

    if nsw is None:
        nsw = 0
    if ibrion is None:
        ibrion = -1

    # Single-point / DFPT / MD / unknown-static: finished job is enough
    if nsw <= 1 or ibrion not in (1, 2, 3):
        verdict = ConvergenceVerdict(True, REASON_NOT_RELAXATION)
        _verdict_cache[outcar] = (mtime, verdict)
        return verdict

    # --- Ionic relaxation ---
    max_f = _last_max_force(outcar)

    # Force criterion (EDIFFG < 0): hard gate — eliminates NSW-bump FP
    if ediffg is not None and ediffg < 0:
        if max_f is None:
            verdict = ConvergenceVerdict(False, REASON_MISSING_FORCES)
        else:
            verdict = ConvergenceVerdict(
                max_f <= abs(ediffg) + 1e-8,
                REASON_FORCE_GATE if max_f <= abs(ediffg) + 1e-8 else REASON_FORCE_GATE_FAIL,
                max_f=max_f,
            )
        _verdict_cache[outcar] = (mtime, verdict)
        return verdict

    # Energy criterion or missing EDIFFG: NSW early-exit with *run* NSW
    n_ionic = _count_total_force_blocks(outcar)
    if n_ionic < 1:
        verdict = ConvergenceVerdict(False, REASON_MISSING_FORCES)
    else:
        verdict = ConvergenceVerdict(
            n_ionic < nsw,
            REASON_NSW_EARLY_EXIT if n_ionic < nsw else REASON_NSW_EXHAUSTED,
            max_f=max_f,
            n_ionic=n_ionic,
        )
    _verdict_cache[outcar] = (mtime, verdict)
    return verdict


def _tail_text(path: Path, n: int = 4096) -> str | None:
    """Read the last *n* bytes of *path* (no full-file read for large files)."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= n:
        try:
            return path.read_text()
        except OSError:
            return None
    try:
        with path.open("rb") as f:
            f.seek(size - n)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _head_text(path: Path, n: int = 65536) -> str:
    """Read the first *n* bytes of *path*."""
    try:
        with path.open("rb") as f:
            return f.read(n).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _parse_run_tags(text: str) -> tuple[int | None, int | None, float | None]:
    """Return (nsw, ibrion, ediffg_signed) from VASP tag text (OUTCAR/INCAR)."""
    nsw = ibrion = None
    ediffg: float | None = None
    m = list(_NSW_RE.finditer(text))
    if m:
        nsw = int(m[-1].group(1))
    m = list(_IBRION_RE.finditer(text))
    if m:
        ibrion = int(m[-1].group(1))
    m = list(_EDIFFG_RE.finditer(text))
    if m:
        ediffg = float(m[-1].group(1))
    return nsw, ibrion, ediffg


def _count_total_force_blocks(outcar: Path) -> int:
    """Count TOTAL-FORCE headers (≈ ionic steps) without loading whole file."""
    needle = _FORCE_HDR.encode("ascii")
    n = 0
    try:
        with outcar.open("rb") as f:
            buf = b""
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                data = buf + chunk
                n += data.count(needle)
                buf = data[-(len(needle) - 1) :] if len(needle) > 1 else b""
    except OSError:
        return 0
    return n


def _last_max_force(outcar: Path) -> float | None:
    """Max |F| on atoms from the last TOTAL-FORCE block (eV/Å, magnitude)."""
    # Prefer a large tail window so the final ionic block is included.
    try:
        size = outcar.stat().st_size
    except OSError:
        return None
    window = min(size, 2_500_000)
    text = _tail_text(outcar, n=window) if window else None
    if not text:
        return None
    idx = text.rfind(_FORCE_HDR)
    if idx < 0:
        return None
    block = text[idx + len(_FORCE_HDR) :]
    # Drop dashed separator line(s)
    lines = block.splitlines()
    forces: list[float] = []
    started = False
    for line in lines:
        s = line.strip()
        if not s:
            if started:
                break
            continue
        if set(s) <= {"-"}:
            started = True
            continue
        if s.lower().startswith("total drift"):
            break
        if not started:
            # synthetic OUTCARs may omit the dashed line
            started = True
        parts = s.split()
        if len(parts) < 6:
            if forces:
                break
            continue
        try:
            fx, fy, fz = float(parts[3]), float(parts[4]), float(parts[5])
        except ValueError:
            if forces:
                break
            continue
        forces.append((fx * fx + fy * fy + fz * fz) ** 0.5)
    if not forces:
        return None
    return max(forces)
