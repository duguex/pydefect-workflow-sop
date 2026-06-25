"""Global cache for MP query results and VASP calculations.

Storage:
  - SQLite database at ``CACHE_ROOT / "cache.db"`` indexes all entries.
  - Files (CONTCAR, calc_results.json) under ``CALC_CACHE / {key}/``.
  - Full OUTCAR/vasprun.xml are NOT stored.  A minimal stub OUTCAR is
    written on cache hit so that :func:`~vasp_sop.vasp.io.check_converged`
    passes transparently.

All cache root paths can be swapped for testing via :func:`override_cache_root`.
"""

from __future__ import annotations

import logging
import sqlite3
import shutil
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache root (module-level, swappable for tests) ──────────────────────

CACHE_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = CACHE_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"
CALC_CACHE: Path = CACHE_ROOT / "calc_cache"


def override_cache_root(p: Path) -> None:
    """Swap all cache paths (for testing via ``override_cache_root(tmp_path)``)."""
    global CACHE_ROOT, MP_CACHE, POSCAR_CACHE, CALC_CACHE
    CACHE_ROOT = p
    MP_CACHE = CACHE_ROOT / "mp_cache"
    POSCAR_CACHE = MP_CACHE / "poscars"
    CALC_CACHE = CACHE_ROOT / "calc_cache"


# ══════════════════════════════════════════════════════════════════════════
# Database helpers
# ══════════════════════════════════════════════════════════════════════════

_CACHED_RESULT_FILES = ("CONTCAR", "calc_results.json", ".converged")


def _get_db() -> sqlite3.Connection:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(CACHE_ROOT / "cache.db"), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    _init_db(db)
    return db


def _init_db(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS calc_results (
            formula       TEXT NOT NULL,
            mpid          TEXT NOT NULL,
            cached_at     REAL NOT NULL,
            has_outcar    INTEGER NOT NULL DEFAULT 0,
            has_contcar   INTEGER NOT NULL DEFAULT 0,
            file_count    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (formula, mpid)
        );
        CREATE TABLE IF NOT EXISTS cpd_results (
            formula          TEXT NOT NULL,
            mpid             TEXT NOT NULL,
            cached_at        REAL NOT NULL,
            has_target_vertices   INTEGER NOT NULL DEFAULT 0,
            has_standard_energies INTEGER NOT NULL DEFAULT 0,
            has_composition_energies INTEGER NOT NULL DEFAULT 0,
            file_count       INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (formula, mpid)
        );
    """)


def _calc_key(formula: str, mpid: str) -> str:
    return f"{formula}_mp-{mpid}"


# ══════════════════════════════════════════════════════════════════════════
# OUTCAR stub (lightweight substitute for check_converged)
# ══════════════════════════════════════════════════════════════════════════


def _write_outcar_stub(d: Path) -> None:
    """Write a minimal OUTCAR that passes :func:`~vasp_sop.vasp.io.check_converged`.

    The real OUTCAR can be ~100 MB; this stub is ~200 bytes.
    """
    lines = [
        "EDIFFG = -0.01",
        "",
        " TOTAL-FORCE (eV/Angst)",
        " -----------",
        "  1.000  2.000  3.000  0.001  0.002  0.003",
        "  4.000  5.000  6.000  0.001  0.002  0.003",
        "",
        " General timing and accounting",
    ]
    (d / "OUTCAR").write_text("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════
# VASP calculation cache
# ══════════════════════════════════════════════════════════════════════════


def calc_results_get(formula: str, mpid: str) -> Optional[Path]:
    """Return path to cached dir if converged, else None.

    On hit, writes OUTCAR stub + CONTCAR so the caller sees a normal
    calculation directory.
    """
    db = _get_db()
    row = db.execute(
        "SELECT has_outcar FROM calc_results WHERE formula=? AND mpid=?",
        (formula, mpid),
    ).fetchone()
    if row is None or not row["has_outcar"]:
        return None

    d = CALC_CACHE / _calc_key(formula, mpid)
    if not (d / ".converged").is_file():
        db.execute("DELETE FROM calc_results WHERE formula=? AND mpid=?",
                    (formula, mpid))
        db.commit()
        logger.warning("Stale cache entry cleaned: %s (mp-%s)", formula, mpid)
        return None

    if not (d / "OUTCAR").is_file():
        _write_outcar_stub(d)

    return d


def calc_results_put(formula: str, mpid: str, src_dir: Path) -> None:
    """Store lightweight cached result (CONTCAR + calc_results.json, not OUTCAR)."""
    d = CALC_CACHE / _calc_key(formula, mpid)
    d.mkdir(parents=True, exist_ok=True)

    file_count = 0
    has_outcar = False
    has_contcar = False

    # Store only the lightweight files
    for fname in ("CONTCAR", "calc_results.json"):
        src = src_dir / fname
        if src.is_file():
            shutil.copy2(str(src), str(d / fname))
            file_count += 1
            if fname == "CONTCAR":
                has_contcar = True

    # Also grab from crisp output/ subdir
    output_dir = src_dir / "output"
    if output_dir.is_dir():
        for fname in ("CONTCAR", "calc_results.json"):
            src = output_dir / fname
            if src.is_file():
                shutil.copy2(str(src), str(d / fname))
                file_count += 1

    # Mark as converged
    (d / ".converged").write_text(str(time.time()))
    file_count += 1
    has_outcar = True  # we can regenerate the stub

    db = _get_db()
    db.execute(
        """INSERT OR REPLACE INTO calc_results
           (formula, mpid, cached_at, has_outcar, has_contcar, file_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (formula, mpid, time.time(), int(has_outcar), int(has_contcar), file_count),
    )
    db.commit()
    logger.debug("Cached calc results for %s (mp-%s): %d files (lightweight)",
                 formula, mpid, file_count)


def calc_results_delete(formula: str, mpid: str) -> None:
    """Remove cached entry (both DB record and files)."""
    d = CALC_CACHE / _calc_key(formula, mpid)
    if d.is_dir():
        shutil.rmtree(str(d))
    db = _get_db()
    db.execute("DELETE FROM calc_results WHERE formula=? AND mpid=?",
               (formula, mpid))
    db.commit()
    logger.debug("Deleted calc cache for %s (mp-%s)", formula, mpid)


# ══════════════════════════════════════════════════════════════════════════
# CPD result cache (unchanged — these are already small yaml files)
# ══════════════════════════════════════════════════════════════════════════


def calc_cpd_get(formula: str, mpid: str) -> Optional[Path]:
    db = _get_db()
    row = db.execute(
        "SELECT has_target_vertices FROM cpd_results WHERE formula=? AND mpid=?",
        (formula, mpid),
    ).fetchone()
    if row is None or not row["has_target_vertices"]:
        return None
    d = CALC_CACHE / f"{_calc_key(formula, mpid)}_cpd"
    if not (d / "target_vertices.yaml").is_file():
        db.execute("DELETE FROM cpd_results WHERE formula=? AND mpid=?",
                    (formula, mpid))
        db.commit()
        logger.warning("Stale CPD cache entry cleaned: %s (mp-%s)", formula, mpid)
        return None
    return d


def calc_cpd_put(formula: str, mpid: str, cpd_root: Path) -> None:
    d = CALC_CACHE / f"{_calc_key(formula, mpid)}_cpd"
    d.mkdir(parents=True, exist_ok=True)
    file_count = 0
    has_tv = has_se = has_ce = False
    for fname in ("target_vertices.yaml", "standard_energies.yaml",
                   "composition_energies.yaml"):
        src = cpd_root / fname
        if src.is_file():
            shutil.copy2(str(src), str(d / fname))
            file_count += 1
            if fname == "target_vertices.yaml":
                has_tv = True
            elif fname == "standard_energies.yaml":
                has_se = True
            elif fname == "composition_energies.yaml":
                has_ce = True
    db = _get_db()
    db.execute(
        """INSERT OR REPLACE INTO cpd_results
           (formula, mpid, cached_at,
            has_target_vertices, has_standard_energies,
            has_composition_energies, file_count)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (formula, mpid, time.time(),
         int(has_tv), int(has_se), int(has_ce), file_count),
    )
    db.commit()
    logger.debug("Cached CPD results for %s (mp-%s): %d files",
                 formula, mpid, file_count)


# ══════════════════════════════════════════════════════════════════════════
# Integrate with pipeline
# ══════════════════════════════════════════════════════════════════════════


def cache_target_results(
    formula: str, mpid: str, target_dir: Path, cpd_root: Path,
) -> None:
    calc_results_put(formula, mpid, target_dir)
    calc_cpd_put(formula, mpid, cpd_root)
    logger.info("Cached %s (mp-%s): VASP + CPD results", formula, mpid)
