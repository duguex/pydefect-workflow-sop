"""Directory truth-status — one authoritative answer per calculation dir.

The 2026 incident (2026-08-12): the operator asked about Gd2GaSbO7:Bi cpd
dirs; agent.db had zero rows for them while the dirs carried real slurm
logs (``206555.log``, ``CRISP_COMPLETED``) — the DB history was incomplete
(older crisp submissions never landed in the current agent.db).  Any
single-source answer (DB-only, log-only, disk-only) lies.

``dir_status`` merges three evidence families into one state:

- **DB** (crisp agent.db): record count, status sequence, timestamps.
- **Disk hard evidence**: slurm ``*.log`` tail markers
  (``CRISP_COMPLETED`` / ``CRISP_FAILED`` / ZBRENT / TIME LIMIT),
  ``submit.slurm``, ``XDATCAR`` (ran at all), OUTCAR mtime, CONTCAR mtime.
- **Verdict + inputs**: convergence verdict, input readiness, drift
  (INCAR newer than OUTCAR).

States
------
``excluded``            ADR 0013 defect exclusion / cpd exclusion list.
``missing_inputs``      not input-ready and never ran.
``converged``           verdict converged.
``running``             DB live job, slurm log fresh.
``stalled``             DB live job but log/OUTCAR stale for hours.
``failed``              DB failed / CRISP_FAILED marker.
``unconverged``         OUTCAR present, verdict not converged.
``regen_pending``       inputs regenerated after the last run (drift).
``history_incomplete``  disk shows a finished run but DB has no record.
``never_ran``           no DB record, no run artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_STALL_HOURS = 4.0
_LOG_MARKERS = ("CRISP_COMPLETED", "CRISP_FAILED")
_LIVE_STATES = ("running", "submitted", "submit", "ready_fetch")


@dataclass(frozen=True)
class DirStatus:
    state: str
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _fmt_mtime(p: Path) -> str:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).strftime('%m-%d %H:%M')
    except OSError:
        return "?"


def _tail_text(path: Path, n: int = 2048) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, f.seek(0, 2) - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _latest_log(work_dir: Path) -> Path | None:
    try:
        logs = sorted(work_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return None
    return logs[-1] if logs else None


def _freshness(path: Path, now: float) -> float:
    try:
        return (now - path.stat().st_mtime) / 3600.0
    except OSError:
        return float("inf")


def _db_history(work_dir: Path) -> tuple[list[tuple[str, str, str]], str | None]:
    """(status, submit_time, error_head) rows for *work_dir* from agent.db."""
    import sqlite3

    db = Path.home() / ".crisp" / "data" / "agent.db"
    if not db.is_file():
        return [], None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return [], None
    try:
        rows = conn.execute(
            "SELECT status, substr(coalesce(submit_time,''),1,16),"
            " substr(coalesce(error_msg,''),1,60) FROM jobs"
            " WHERE local_dir = ? ORDER BY submit_time",
            (str(work_dir.resolve()),),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    latest = rows[-1][0] if rows else None
    return [(s, t, e[:48]) for s, t, e in rows], latest


def dir_status(
    work_dir: Path,
    *,
    task_type: str = "",
    now: float | None = None,
) -> DirStatus:
    """Single authoritative state for one calculation directory."""
    from vasp_sop.vasp.convergence import convergence_verdict
    from vasp_sop.vasp.io import input_ready

    work_dir = Path(work_dir)
    now = now if now is not None else __import__("time").time()
    ev: list[str] = []
    warn: list[str] = []

    # ── Exclusion gates ───────────────────────────────────────────────
    if "defect" in work_dir.parts:
        from vasp_sop.defect import is_valid_defect_dir

        if not is_valid_defect_dir(work_dir):
            ev.append("ADR 0013 exclusion (invalid defect dir)")
            return DirStatus("excluded", ev)
    if "cpd" in work_dir.parts:
        from vasp_sop.defect.cpd import is_excluded_phase

        sys_root = work_dir.parents[1]  # <sys>/cpd/<phase>
        if is_excluded_phase(sys_root, work_dir):
            ev.append("cpd exclusion list (cpd_excluded_phases.yaml)")
            return DirStatus("excluded", ev)

    # ── Input readiness ───────────────────────────────────────────────
    ready = input_ready(work_dir)
    ev.append(f"inputs: {'ready' if ready else 'INCOMPLETE'}")

    # ── Verdict (OUTCAR) ──────────────────────────────────────────────
    outcar = work_dir / "OUTCAR"
    if not outcar.is_file():
        outcar = work_dir / "output" / "OUTCAR"
    if outcar.is_file():
        v = convergence_verdict(work_dir, task_type=task_type)
        ev.append(f"OUTCAR {outcar.name}: {_fmt_mtime(outcar)}"
                  f" verdict={v.reason} converged={v.converged}")
    else:
        v = None
        ev.append("OUTCAR: missing")

    # ── Disk hard evidence ────────────────────────────────────────────
    log = _latest_log(work_dir)
    if log is not None:
        tail = _tail_text(log)
        marker = next((m for m in _LOG_MARKERS if m in tail), "no-marker")
        ev.append(f"slurm log {log.name}: {_fmt_mtime(log)}"
                  f" tail={marker}")
    else:
        marker = "none"
        ev.append("slurm log: none")
    xdat = work_dir / "XDATCAR"
    if xdat.is_file():
        ev.append(f"XDATCAR {_fmt_mtime(xdat)} (ran)")
    sub = work_dir / "submit.slurm"
    if sub.is_file():
        ev.append(f"submit.slurm {_fmt_mtime(sub)}")
    contcar = work_dir / "CONTCAR"
    if contcar.is_file():
        ev.append(f"CONTCAR {_fmt_mtime(contcar)}")
    incar = work_dir / "INCAR"
    if incar.is_file() and outcar.is_file() \
            and incar.stat().st_mtime > outcar.stat().st_mtime:
        ev.append("DRIFT: INCAR newer than OUTCAR (regenerated, not rerun)")

    # ── DB evidence ───────────────────────────────────────────────────
    hist, db_latest = _db_history(work_dir)
    if hist:
        ev.append(f"agent.db: {len(hist)} record(s), latest={db_latest}"
                  f" [{', '.join(s for s, _, _ in hist[-4:])}]")
    else:
        ev.append("agent.db: no records")

    # ── Decision ──────────────────────────────────────────────────────
    if v is not None and v.converged:
        return DirStatus("converged", ev, warn)
    if v is not None and v.reason == "truncated":
        warn.append("OUTCAR truncated")
    if not ready and log is None and not xdat.is_file():
        return DirStatus("missing_inputs", ev, warn)
    if db_latest in _LIVE_STATES:
        age = _freshness(log, now) if log is not None else float("inf")
        out_age = _freshness(outcar, now) if outcar.is_file() else float("inf")
        fresh = min(age, out_age)
        if fresh <= _STALL_HOURS:
            return DirStatus("running", ev, warn)
        warn.append(f"DB says {db_latest} but no disk activity for"
                    f" {fresh:.1f}h")
        return DirStatus("stalled", ev, warn)
    if db_latest == "failed" or marker == "CRISP_FAILED":
        return DirStatus("failed", ev, warn)
    # Ran (disk evidence) but DB never saw it — the 2026 cpd class.
    if log is not None and (marker == "CRISP_COMPLETED" or xdat.is_file()):
        warn.append("disk shows a finished run but agent.db has no record"
                    " (history incomplete?)")
        return DirStatus("history_incomplete", ev, warn)
    if v is not None:
        return DirStatus("unconverged", ev, warn)
    if incar.is_file() and not outcar.is_file() and sub.is_file():
        return DirStatus("regen_pending", ev, warn)
    return DirStatus("never_ran", ev, warn)
