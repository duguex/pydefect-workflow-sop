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

import atexit as _atexit
import json as _json
import os as _os
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
REASON_ELECTRONIC_NOT_CONV = "electronic_not_conv"

# VASP prints "reached required accuracy" even when the final electronic
# step hit NELM — with a warning that forces/energies may be spurious
# ("increasing NELM, if you were close to").  pydefect's electronic_conv
# reads the vasprun scsteps and calls this unconverged; so do we (ADR 0016).
_NELM_WARN_MARK = "increasing NELM"

_nelm_cache: dict[tuple[str, int], bool] = {}


def _has_nelm_warning(outcar: Path) -> bool:
    """True when OUTCAR contains VASP's NELM-exhaustion warning anywhere
    (it can be MBs before EOF when more ionic steps followed)."""
    try:
        key = (str(outcar), outcar.stat().st_mtime)
    except OSError:
        return False
    cached = _nelm_cache.get(key)
    if cached is not None:
        return cached
    try:
        hit = _NELM_WARN_MARK in outcar.read_text(errors="ignore")
    except OSError:
        hit = False
    if len(_nelm_cache) > 4096:
        _nelm_cache.clear()
    _nelm_cache[key] = hit
    return hit

_FORCE_HDR = "TOTAL-FORCE (eV/Angst)"
_TIMING_MARK = "General timing and accounting"

_NSW_RE = _re.compile(r"NSW\s*=\s*(\d+)", _re.I)
_IBRION_RE = _re.compile(r"IBRION\s*=\s*(-?\d+)", _re.I)
_EDIFFG_RE = _re.compile(
    r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", _re.I
)

# mtime-based memoisation: avoid re-reading unchanged OUTCARs across
# successive batch-run cycles. Keyed by (outcar path, task_type) — the
# force gate differs per task type, so a plain path key would let a
# band/dos/dielectric verdict poison a relaxation verdict and vice versa.
# The sidecar file persists the memo across processes (batch status /
# progress re-parse thousands of OUTCARs per invocation otherwise).
_verdict_cache: dict[Path, dict[str, tuple[float, "ConvergenceVerdict"]]] = {}
_verdict_dirty: set[tuple[Path, str]] = set()
_verdict_loaded = False
_VERDICT_FLUSH_EVERY = 250
# Bump whenever verdict *logic* changes (not just per-file data): stale
# sidecars produced by older code must not be replayed.  v2 = ADR 0016
# electronic (NELM) gate — pre-gate verdicts were written without it.
_VERDICT_SCHEMA = 2


def _sidecar_path() -> Path:
    from vasp_sop.core.paths import VERDICT_CACHE

    return VERDICT_CACHE


def _load_sidecar() -> None:
    """Load the persistent verdict memo (tolerates absence/corruption)."""
    global _verdict_loaded
    if _verdict_loaded:
        return
    _verdict_loaded = True
    try:
        raw = _json.loads(_sidecar_path().read_text())
    except (OSError, _json.JSONDecodeError, ValueError):
        return
    if not isinstance(raw, dict) or raw.get("schema") != _VERDICT_SCHEMA:
        return
    for path_str, by_task in raw.items():
        if path_str == "schema":
            continue
        if not isinstance(by_task, dict):
            continue
        entries: dict[str, tuple[float, ConvergenceVerdict]] = {}
        for task_type, rec in by_task.items():
            if not isinstance(rec, dict) or "mtime" not in rec:
                continue
            v = rec.get("verdict")
            if not isinstance(v, dict):
                continue
            entries[task_type] = (
                float(rec["mtime"]),
                ConvergenceVerdict(
                    converged=bool(v.get("converged")),
                    reason=str(v.get("reason", "")),
                    max_f=v.get("max_f"),
                    n_ionic=v.get("n_ionic"),
                ),
            )
        if entries:
            _verdict_cache[Path(path_str)] = entries


def _flush_sidecar() -> None:
    """Write the dirty memo entries back atomically."""
    if not _verdict_dirty:
        return
    try:
        _load_sidecar()
        payload: dict[str, dict] = {}
        for outcar, by_task in _verdict_cache.items():
            entries = {}
            for task_type, (mtime, verdict) in by_task.items():
                entries[task_type] = {
                    "mtime": mtime,
                    "verdict": {
                        "converged": verdict.converged,
                        "reason": verdict.reason,
                        "max_f": verdict.max_f,
                        "n_ionic": verdict.n_ionic,
                    },
                }
            payload[str(outcar)] = entries
        payload["schema"] = _VERDICT_SCHEMA
        target = _sidecar_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(_json.dumps(payload))
        _os.replace(tmp, target)
    except OSError:
        pass
    finally:
        _verdict_dirty.clear()


_atexit.register(_flush_sidecar)


def _mark_dirty(outcar: Path, task_type: str) -> None:
    """Track a new memo entry; flush in batches to bound loss on a crash."""
    _verdict_dirty.add((outcar, task_type))
    if len(_verdict_dirty) >= _VERDICT_FLUSH_EVERY:
        _flush_sidecar()


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
    _load_sidecar()
    by_task = _verdict_cache.get(outcar)
    cached = by_task.get(task_type) if by_task is not None else None
    if cached is not None and cached[0] == mtime:
        return cached[1]

    # Window must cover the convergence line: long runs (100+ ionic steps
    # with many electronic iterations) can leave "reached required accuracy"
    # >64KB before EOF, behind the timing block.
    tail = _tail_text(outcar, n=262144)
    if tail is None or _TIMING_MARK not in tail:
        # Still report last-block force when available: stall detection reads
        # it on OUTCARs whose run crashed before the timing marker was written.
        verdict = ConvergenceVerdict(
            False, REASON_TRUNCATED, max_f=_last_max_force(outcar)
        )
        _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
        _mark_dirty(outcar, task_type)
        return verdict

    # Electronic convergence gate (ADR 0016): VASP's own "reached required
    # accuracy" can be a false positive when the last electronic step hit
    # NELM (VASP warns "spurious results ... increasing NELM").  pydefect
    # reads vasprun scsteps and marks electronic_conv=False — the energy is
    # unreliable, so the verdict must be unconverged too.  The warning can
    # sit MBs before EOF (later ionic steps follow), so check the tail
    # window first, then the whole file.
    if _NELM_WARN_MARK in tail or _has_nelm_warning(outcar):
        verdict = ConvergenceVerdict(False, REASON_ELECTRONIC_NOT_CONV)
        _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
        _mark_dirty(outcar, task_type)
        return verdict

    # Task types that never ionically relax: a finished job is the verdict.
    if task_type in NO_FORCE_GATE_TASK_TYPES:
        verdict = ConvergenceVerdict(True, REASON_NOT_RELAXATION)
        _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
        _mark_dirty(outcar, task_type)
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
        _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
        _mark_dirty(outcar, task_type)
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
        _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
        _mark_dirty(outcar, task_type)
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
    _verdict_cache.setdefault(outcar, {})[task_type] = (mtime, verdict)
    _mark_dirty(outcar, task_type)
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
