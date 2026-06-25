"""Global cache for VASP calculation results.

SQLite-only storage of **both inputs and outputs** parsed via pymatgen's
VASP I/O classes (Outcar, Vasprun, Incar, Kpoints, Structure).  No files are
written to the cache — every VASP calculation record is self-contained in one
DB row.

Swap cache root for testing via :func:`override_cache_root`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from pymatgen.io.vasp.outputs import Outcar, Vasprun
from pymatgen.io.vasp.inputs import Incar, Kpoints
from pymatgen.core.structure import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

logger = logging.getLogger(__name__)

# ── Cache root (swappable for tests) ────────────────────────────────────

CACHE_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = CACHE_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"
CALC_CACHE: Path = CACHE_ROOT / "calc_cache"  # kept for backward compat


def override_cache_root(p: Path) -> None:
    """Swap cache root (for testing)."""
    global CACHE_ROOT, MP_CACHE, POSCAR_CACHE, CALC_CACHE
    CACHE_ROOT = p
    MP_CACHE = CACHE_ROOT / "mp_cache"
    POSCAR_CACHE = MP_CACHE / "poscars"
    CALC_CACHE = CACHE_ROOT / "calc_cache"
# ══════════════════════════════════════════════════════════════════════════
# Database
# ══════════════════════════════════════════════════════════════════════════


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
            formula         TEXT NOT NULL,
            mpid            TEXT NOT NULL,
            cached_at       REAL NOT NULL,

            total_energy    REAL,
            converged       INTEGER NOT NULL DEFAULT 0,

            outcar_json     TEXT,
            vasprun_json    TEXT,

            structure_json  TEXT,
            incar_json      TEXT,
            kpoints_json    TEXT,

            n_sites         INTEGER,
            formula_pretty  TEXT,
            space_group     TEXT,

            PRIMARY KEY (formula, mpid)
        );
        CREATE TABLE IF NOT EXISTS cpd_results (
            formula          TEXT NOT NULL,
            mpid             TEXT NOT NULL,
            cached_at        REAL NOT NULL,
            composition_energies_json TEXT,
            standard_energies_json     TEXT,
            target_vertices_json       TEXT,
            PRIMARY KEY (formula, mpid)
        );
    """)


# ══════════════════════════════════════════════════════════════════════════
# VASP calculation cache
# ══════════════════════════════════════════════════════════════════════════


def _safe_outcar_dict(outcar: Outcar) -> dict[str, Any]:
    """Return Outcar.as_dict() with manually-added final_energy."""
    d = outcar.as_dict()
    d["final_energy"] = outcar.final_energy
    if hasattr(outcar, "is_stopped"):
        d["is_stopped"] = outcar.is_stopped
    if hasattr(outcar, "final_energy_wo_entrp"):
        d["final_energy_wo_entrp"] = outcar.final_energy_wo_entrp
    return d


def calc_results_put(formula: str, mpid: str, src_dir: Path) -> None:
    """Parse VASP results from *src_dir* and store in SQLite.

    Uses regex for robust total_energy/converged extraction (works even with
    truncated OUTCAR), then tries pymatgen Outcar/Vasprun for full structured
    data capture (best-effort).
    """
    outcar_path = src_dir / "OUTCAR"
    if not outcar_path.is_file():
        logger.warning("No OUTCAR in %s, skipping cache.", src_dir)
        return

    import re as _re

    text = outcar_path.read_text()
    total_energy: Optional[float] = None
    converged = 0
    m_e = _re.search(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)", text)
    if m_e:
        total_energy = float(m_e.group(1))
    if "General timing and accounting" in text[-4096:]:
        converged = 1

    outcar_json: Optional[str] = None
    try:
        outcar = Outcar(str(outcar_path))
        outcar_json = json.dumps(_safe_outcar_dict(outcar))
    except Exception as exc:
        logger.warning("Failed full Outcar parse in %s: %s", src_dir, exc)

    vasprun_json: Optional[str] = None
    vasprun_path = src_dir / "vasprun.xml"
    if vasprun_path.is_file():
        try:
            v = Vasprun(str(vasprun_path))
            vasprun_json = json.dumps(v.as_dict())
            if getattr(v, "converged", True):
                converged = 1
        except Exception as exc:
            logger.warning("Failed to parse vasprun.xml in %s: %s", src_dir, exc)

    structure_json: Optional[str] = None
    n_sites: Optional[int] = None
    formula_pretty: Optional[str] = None
    space_group: Optional[str] = None
    for cand in (src_dir / "CONTCAR", src_dir / "POSCAR"):
        if cand.is_file():
            try:
                struct = Structure.from_file(str(cand))
                structure_json = json.dumps(struct.as_dict())
                n_sites = struct.num_sites
                formula_pretty = struct.composition.reduced_formula
                sga = SpacegroupAnalyzer(struct, symprec=0.1)
                space_group = sga.get_space_group_symbol()
            except Exception as exc:
                logger.warning("Failed to parse structure in %s: %s", cand, exc)
            break

    incar_json: Optional[str] = None
    incar_path = src_dir / "INCAR"
    if incar_path.is_file():
        try:
            incar = Incar.from_file(str(incar_path))
            incar_json = json.dumps(incar.as_dict())
        except Exception as exc:
            logger.warning("Failed to parse INCAR in %s: %s", src_dir, exc)

    kpoints_json: Optional[str] = None
    kpoints_path = src_dir / "KPOINTS"
    if kpoints_path.is_file():
        try:
            kpts = Kpoints.from_file(str(kpoints_path))
            kpoints_json = json.dumps(kpts.as_dict())
        except Exception as exc:
            logger.warning("Failed to parse KPOINTS in %s: %s", src_dir, exc)

    db = _get_db()
    db.execute(
        """INSERT OR REPLACE INTO calc_results
           (formula, mpid, cached_at,
            total_energy, converged,
            outcar_json, vasprun_json,
            structure_json, incar_json, kpoints_json,
            n_sites, formula_pretty, space_group)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (formula, mpid, time.time(),
         total_energy, converged,
         outcar_json, vasprun_json,
         structure_json, incar_json, kpoints_json,
         n_sites, formula_pretty, space_group),
    )
    db.commit()
    logger.debug("Cached %s (mp-%s): energy=%s  sites=%s  sg=%s",
                 formula, mpid, total_energy or "?", n_sites or "?", space_group or "?")

def calc_results_get(formula: str, mpid: str) -> Optional[dict[str, Any]]:
    """Return full calc result dict from cache, or None if not cached."""
    db = _get_db()
    row = db.execute(
        "SELECT * FROM calc_results WHERE formula=? AND mpid=? AND converged=1",
        (formula, mpid),
    ).fetchone()
    if row is None:
        return None
    return dict(row)
def calc_results_delete(formula: str, mpid: str) -> None:
    """Remove cached entry."""
    db = _get_db()
    db.execute("DELETE FROM calc_results WHERE formula=? AND mpid=?",
               (formula, mpid))
    db.commit()
    logger.debug("Deleted calc cache for %s (mp-%s)", formula, mpid)


# ══════════════════════════════════════════════════════════════════════════
# CPD result cache
# ══════════════════════════════════════════════════════════════════════════


def calc_cpd_get(formula: str, mpid: str) -> Optional[dict[str, Any]]:
    """Return CPD result dict from cache, or None."""
    db = _get_db()
    row = db.execute(
        "SELECT * FROM cpd_results WHERE formula=? AND mpid=?",
        (formula, mpid),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def calc_cpd_put(formula: str, mpid: str, cpd_root: Path) -> None:
    """Cache CPD YAML files as JSON blobs in SQLite."""
    import yaml
    data: dict[str, Any] = {"cached_at": time.time()}

    for key, fname in (
        ("composition_energies_json", "composition_energies.yaml"),
        ("standard_energies_json", "standard_energies.yaml"),
        ("target_vertices_json", "target_vertices.yaml"),
    ):
        path = cpd_root / fname
        if path.is_file():
            try:
                with open(path) as f:
                    data[key] = json.dumps(yaml.safe_load(f))
            except Exception as exc:
                logger.warning("Failed to read %s: %s", path, exc)

    if not data:
        return

    db = _get_db()
    db.execute(
        """INSERT OR REPLACE INTO cpd_results
           (formula, mpid, cached_at,
            composition_energies_json,
            standard_energies_json,
            target_vertices_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (formula, mpid, data.get("cached_at"),
         data.get("composition_energies_json"),
         data.get("standard_energies_json"),
         data.get("target_vertices_json")),
    )
    db.commit()
    logger.debug("Cached CPD results for %s (mp-%s)", formula, mpid)


# ══════════════════════════════════════════════════════════════════════════
# Pipeline integration
# ══════════════════════════════════════════════════════════════════════════


def cache_target_results(
    formula: str, mpid: str, target_dir: Path, cpd_root: Path,
) -> None:
    calc_results_put(formula, mpid, target_dir)
    calc_cpd_put(formula, mpid, cpd_root)
    logger.info("Cached %s (mp-%s): VASP + CPD results", formula, mpid)
