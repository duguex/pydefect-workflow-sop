"""Per-calculation VASP job state tracking — SQLite-backed.

Tracks each VASP calculation directory through:
    pending -> submitted -> converged

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
    from vasp_sop.core.cache import CACHE_ROOT
    return CACHE_ROOT / _DEFAULT_DB


_VALID_STATUSES = frozenset({"pending", "submitted", "converged", "failed"})


class JobStore:
    """Record and query per-calculation VASP job states."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = _db_path(db_path)
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self._path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        return db
    def _init_db(self) -> None:
        db = self._connection()
        try:
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
            db.commit()
        finally:
            db.close()

    def record(self, dir_path: str, status: str,
               source: str = "batch_run", attempt: int = 0,
               task_name: str = "", reason: str = "") -> None:
        """Insert a job state record."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}; "
                             f"must be one of {sorted(_VALID_STATUSES)}")
        db = self._connection()
        try:
            db.execute(
                "INSERT INTO job_history "
                "(dir_path, status, timestamp, source, attempt, task_name, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dir_path, status, time.time(), source, attempt, task_name, reason),
            )
            db.commit()
        finally:
            db.close()

    def track(self, dir_path: str) -> None:
        """加入待检查列表（提交时调用）。"""
        db = self._connection()
        try:
            db.execute(
                "INSERT OR REPLACE INTO tracked (dir_path, submitted_at) VALUES (?, ?)",
                (dir_path, time.time()),
            )
            db.commit()
        finally:
            db.close()

    def untrack(self, dir_path: str) -> None:
        """从待检查列表移除（收敛或放弃时调用）。"""
        db = self._connection()
        try:
            db.execute("DELETE FROM tracked WHERE dir_path = ?", (dir_path,))
            db.commit()
        finally:
            db.close()

    def tracked_dirs(self) -> list[dict]:
        """返回 tracked 表中所有目录。"""
        db = self._connection()
        try:
            rows = db.execute(
                "SELECT dir_path, submitted_at FROM tracked ORDER BY submitted_at"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def latest(self, dir_path: str) -> str | None:
        """Return most recent status for *dir_path*, or None."""
        db = self._connection()
        try:
            row = db.execute(
                "SELECT status FROM job_history "
                "WHERE dir_path = ? ORDER BY timestamp DESC LIMIT 1",
                (dir_path,),
            ).fetchone()
            return row["status"] if row else None
        finally:
            db.close()

    def latest_all(self) -> dict[str, str]:
        """Return {dir_path: latest_status} for every dir with records."""
        db = self._connection()
        try:
            rows = db.execute("""
                SELECT dir_path, status FROM job_history
                WHERE (dir_path, timestamp) IN (
                    SELECT dir_path, MAX(timestamp)
                    FROM job_history GROUP BY dir_path
                )
            """).fetchall()
            return {r["dir_path"]: r["status"] for r in rows}
        finally:
            db.close()

    def history(self, dir_path: str) -> list[dict]:
        """Return chronologically ordered state records."""
        db = self._connection()
        try:
            rows = db.execute(
                "SELECT status, timestamp, source, attempt, task_name, reason FROM job_history "
                "WHERE dir_path = ? ORDER BY timestamp ASC",
                (dir_path,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def close(self) -> None:
        pass
