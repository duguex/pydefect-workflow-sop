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
        CREATE TABLE IF NOT EXISTS vasp_results (
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


def vasp_results_put(formula: str, mpid: str, src_dir: Path) -> None:
    """Parse VASP results from *src_dir* and store in SQLite.

    Uses regex for robust total_energy/converged extraction (works even with
    truncated OUTCAR), then tries pymatgen Outcar/Vasprun for full structured
    data capture (best-effort).

    Beyond the standard Outcar parse, also extracts:
    - Fermi contact shift / dipolar hyperfine coupling (``read_fermi_contact_shift``)
    - Zero-field splitting tensor (spin-spin contribution, custom table parse)
    These extended fields are merged into ``outcar.data`` before serialization.
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

        # ── Extend parsing: hyperfine coupling (Fermi contact + dipolar) ──
        # pymatgen's Outcar has read_fermi_contact_shift() but does not call
        # it by default — invoke explicitly.
        try:
            outcar.read_fermi_contact_shift()
        except Exception:
            pass

        # ── Extend parsing: zero-field splitting tensor (ZFS) ────────────
        # Standard Outcar does not parse the spin-spin ZFS table.  Use
        # Outcar.read_table_pattern (inherited) to extract it.
        try:
            zfs = outcar.read_table_pattern(
                header_pattern=(
                    r"Spin-spin contribution to zero-field splitting tensor \(MHz\)"
                    r"\s*\-+\s*"
                    r"D_xx\s+D_yy\s+D_zz\s+D_xy\s+D_xz\s+D_yz\s*"
                    r"\-+"
                ),
                row_pattern=r"\s*" + r"\s+".join([r"([-]?\d+\.\d+)"] * 6),
                footer_pattern=r"\-+",
                postprocess=float,
                last_one_only=True,
            )
            if zfs:
                outcar.data["zero_field_splitting"] = zfs[0]
        except Exception:
            pass

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
        """INSERT OR REPLACE INTO vasp_results
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

def vasp_results_get(formula: str, mpid: str) -> Optional[dict[str, Any]]:
    """Return full calc result dict from cache, or None if not cached."""
    db = _get_db()
    row = db.execute(
        "SELECT * FROM vasp_results WHERE formula=? AND mpid=? AND converged=1",
        (formula, mpid),
    ).fetchone()
    if row is None:
        return None
    return dict(row)
def vasp_results_delete(formula: str, mpid: str) -> None:
    """Remove cached entry."""
    db = _get_db()
    db.execute("DELETE FROM vasp_results WHERE formula=? AND mpid=?",
               (formula, mpid))
    db.commit()
    logger.debug("Deleted calc cache for %s (mp-%s)", formula, mpid)



