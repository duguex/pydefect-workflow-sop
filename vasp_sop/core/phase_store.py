"""Phase transition tracking — SQLite-backed history of pipeline state.

Each system records a new row every time ``_phase()`` returns a
different value.  ``batch status`` reads the latest record per system
from this store instead of scanning directories.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


_DEFAULT_DB = "phases.db"


def _db_path(given: Path | None) -> Path:
    if given is not None:
        return given
    from vasp_sop.core.cache import CACHE_ROOT
    return CACHE_ROOT / _DEFAULT_DB


class PhaseStore:
    """Record and query pipeline phase transitions per system."""

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
                CREATE TABLE IF NOT EXISTS phase_history (
                    system_name TEXT NOT NULL,
                    phase       TEXT NOT NULL,
                    timestamp   REAL NOT NULL,
                    source      TEXT NOT NULL DEFAULT 'batch_run'
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_ph_sys_time
                ON phase_history(system_name, timestamp)
            """)
            db.commit()
        finally:
            db.close()

    def record(self, system_name: str, phase: str, source: str = "batch_run") -> None:
        """Insert a phase transition record."""
        db = self._connection()
        try:
            db.execute(
                "INSERT INTO phase_history (system_name, phase, timestamp, source) "
                "VALUES (?, ?, ?, ?)",
                (system_name, phase, time.time(), source),
            )
            db.commit()
        finally:
            db.close()

    def latest(self, system_name: str) -> str | None:
        """Return the most recent phase for *system_name*, or None."""
        db = self._connection()
        try:
            row = db.execute(
                "SELECT phase FROM phase_history "
                "WHERE system_name = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (system_name,),
            ).fetchone()
            return row["phase"] if row else None
        finally:
            db.close()

    def latest_all(self) -> dict[str, str]:
        """Return {system_name: latest_phase} for every system with records."""
        db = self._connection()
        try:
            rows = db.execute("""
                SELECT system_name, phase FROM phase_history
                WHERE (system_name, timestamp) IN (
                    SELECT system_name, MAX(timestamp)
                    FROM phase_history
                    GROUP BY system_name
                )
            """).fetchall()
            return {r["system_name"]: r["phase"] for r in rows}
        finally:
            db.close()

    def history(self, system_name: str) -> list[dict]:
        """Return chronologically ordered phase records."""
        db = self._connection()
        try:
            rows = db.execute(
                "SELECT phase, timestamp, source FROM phase_history "
                "WHERE system_name = ? "
                "ORDER BY timestamp ASC",
                (system_name,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def close(self) -> None:
        """No-op: connections are short-lived (per-call)."""
        pass
