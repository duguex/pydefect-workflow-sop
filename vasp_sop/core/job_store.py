"""Per-calculation VASP job state tracking — SQLite-backed.

Tracks each VASP calculation directory through:
    pending -> submitted -> converged | unconverged | failed

Also supports a tracked table for active job directories awaiting
completion checks.

System-level phase is derived from per-calculation states + marker
files in ``_phase()`` (see ``vasp_sop/cli/main.py``).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


_DEFAULT_DB = "jobs.db"


def _db_path(given: Path | None) -> Path:
    if given is not None:
        return given
    # Job lifecycle state lives under ~/.vasp_sop (not vasp-cache results root).
    from vasp_sop.core.cache import SOP_ROOT
    return SOP_ROOT / _DEFAULT_DB


_VALID_STATUSES = frozenset({
    "pending", "submitted", "converged", "unconverged", "failed",
})


class JobStore:
    """Record and query per-calculation VASP job states.

    Supports context manager for batch loops::

        with JobStore() as js:
            js.record(...)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = _db_path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    @property
    def _db(self) -> sqlite3.Connection:
        """Lazily-opened persistent connection (WAL mode)."""
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path), timeout=10)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _init_db(self) -> None:
        db = self._db
        db.execute("""
            CREATE TABLE IF NOT EXISTS job_history (
                dir_path    TEXT NOT NULL,
                status      TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                source      TEXT NOT NULL DEFAULT 'batch_run',
                attempt     INTEGER NOT NULL DEFAULT 0,
                task_name   TEXT NOT NULL DEFAULT '',
                reason      TEXT NOT NULL DEFAULT ''
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_jh_dir_time
            ON job_history(dir_path, timestamp)
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS tracked (
                dir_path      TEXT PRIMARY KEY,
                submitted_at  REAL NOT NULL
            )
        """)
        for col, col_type in [("attempt", "INTEGER NOT NULL DEFAULT 0"),
                               ("task_name", "TEXT NOT NULL DEFAULT ''"),
                               ("reason", "TEXT NOT NULL DEFAULT ''")]:
            try:
                db.execute(f"ALTER TABLE job_history ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        db.commit()

    def record(self, dir_path: str, status: str,
               source: str = "batch_run", attempt: int = 0,
               task_name: str = "", reason: str = "") -> None:
        """Insert a job state record."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}; "
                             f"must be one of {sorted(_VALID_STATUSES)}")
        self._db.execute(
            "INSERT INTO job_history "
            "(dir_path, status, timestamp, source, attempt, task_name, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dir_path, status, time.time(), source, attempt, task_name, reason),
        )
        self._db.commit()

    def track(self, dir_path: str) -> None:
        """加入待检查列表（提交时调用）。"""
        self._db.execute(
            "INSERT OR REPLACE INTO tracked (dir_path, submitted_at) VALUES (?, ?)",
            (dir_path, time.time()),
        )
        self._db.commit()

    def untrack(self, dir_path: str) -> None:
        """从待检查列表移除（收敛或放弃时调用）。"""
        self._db.execute("DELETE FROM tracked WHERE dir_path = ?", (dir_path,))
        self._db.commit()

    def tracked_dirs(self) -> list[dict]:
        """返回 tracked 表中所有目录。"""
        rows = self._db.execute(
            "SELECT dir_path, submitted_at FROM tracked ORDER BY submitted_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest(self, dir_path: str) -> str | None:
        """Return most recent status for *dir_path*, or None."""
        row = self._db.execute(
            "SELECT status FROM job_history "
            "WHERE dir_path = ? ORDER BY timestamp DESC LIMIT 1",
            (dir_path,),
        ).fetchone()
        return row["status"] if row else None

    def latest_all(self) -> dict[str, str]:
        """Return {dir_path: latest_status} for every dir with records."""
        rows = self._db.execute("""
            SELECT dir_path, status FROM job_history
            WHERE (dir_path, timestamp) IN (
                SELECT dir_path, MAX(timestamp)
                FROM job_history GROUP BY dir_path
            )
        """).fetchall()
        return {r["dir_path"]: r["status"] for r in rows}

    def history(self, dir_path: str) -> list[dict]:
        """Return chronologically ordered state records."""
        rows = self._db.execute(
            "SELECT status, timestamp, source, attempt, task_name, reason FROM job_history "
            "WHERE dir_path = ? ORDER BY timestamp ASC",
            (dir_path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        """Close the persistent connection (idempotent)."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def calc_done_on_disk(path: Path, *, task_type: str = "") -> bool:
    """True if *path* is complete enough to record as converged.

    Unitcell band/dos/dielectric use :func:`check_task_complete`; all other
    calcs use ionic :func:`check_converged`.
    """
    path = Path(path)
    name = task_type or path.name
    if name in ("band", "dos", "dielectric"):
        from vasp_sop.vasp.io import check_task_complete
        return check_task_complete(path, name)
    from vasp_sop.vasp.io import check_converged
    return check_converged(path)


def record_if_done(
    store: JobStore,
    path: Path,
    *,
    source: str = "batch_run",
    task_type: str = "",
    task_name: str = "",
) -> str:
    """Record converged or unconverged from disk truth. Returns status written."""
    path = Path(path)
    p = str(path.resolve())
    if calc_done_on_disk(path, task_type=task_type):
        store.record(p, "converged", source=source, task_name=task_name)
        return "converged"
    # Finished but not done, or incomplete — only mark unconverged if OUTCAR exists
    out = path / "OUTCAR"
    if not out.is_file():
        out = path / "output" / "OUTCAR"
    if out.is_file():
        store.record(
            p, "unconverged", source=source, task_name=task_name,
            reason="disk_not_converged",
        )
        return "unconverged"
    return store.latest(p) or "pending"


def reconcile_false_converged(
    store: JobStore | None = None,
    *,
    path_prefix: str | None = None,
) -> dict[str, int]:
    """Rewrite latest==converged entries that fail disk checks → unconverged.

    Returns counts: checked, fixed, kept.
    """
    store = store or JobStore()
    stats = {"checked": 0, "fixed": 0, "kept": 0, "missing": 0}
    for dir_path, status in store.latest_all().items():
        if status != "converged":
            continue
        if path_prefix and not dir_path.startswith(path_prefix):
            continue
        stats["checked"] += 1
        p = Path(dir_path)
        if not p.is_dir():
            stats["missing"] += 1
            continue
        if calc_done_on_disk(p):
            stats["kept"] += 1
            continue
        store.record(
            dir_path, "unconverged", source="reconcile",
            reason="false_converged",
        )
        store.untrack(dir_path)
        stats["fixed"] += 1
    return stats
