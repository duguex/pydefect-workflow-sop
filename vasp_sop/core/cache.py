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
            content_hash    TEXT NOT NULL,
            task_name       TEXT,
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
            tags            TEXT,
            source_dir      TEXT,

            PRIMARY KEY (formula, content_hash)
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




_INCAR_FINGERPRINT_KEYS = ("ENCUT", "PREC", "ISMEAR", "SIGMA", "ISIF",
                           "LDAU", "LDAUTYPE", "LDAUU", "LDAUJ", "LDAUL",
                           "GGA", "IVDW", "LASPH", "METAGGA")


def _incar_fingerprint(src_dir: Path) -> str:
    """Return a compact parameter fingerprint for the INCAR in *src_dir*.

    Reads key INCAR tags that affect VASP results and produces a short
    string.  Returns ``"default"`` when no INCAR is present.
    """
    incar_path = src_dir / "INCAR"
    if not incar_path.is_file():
        return "default"
    try:
        incar = Incar.from_file(str(incar_path))
    except Exception:
        return "default"
    parts = []
    for k in _INCAR_FINGERPRINT_KEYS:
        v = incar.get(k)
        if v is not None:
            parts.append(f"{k}={v}")
    return "|".join(parts) if parts else "default"


def _potcar_fingerprint(src_dir: Path) -> str:
    """Return a compact POTCAR fingerprint for the calculation.

    Reads the header line (``PAW_PBE X\\d+ 01Jan2000``) of each POTCAR
    block and extracts the element+version identifier (e.g. ``Ba_sv``,
    ``Ga_d``).  Returns ``"nopot"`` when no POTCAR file is found.
    """
    potcar_path = src_dir / "POTCAR"
    if not potcar_path.is_file():
        return "nopot"
    import re as _re
    try:
        text = potcar_path.read_text()
        pp_ids = _re.findall(r"PAW_\w+\s+(\S+)", text)
        return ",".join(pp_ids) if pp_ids else "unknown"
    except Exception:
        return "unknown"

def _content_hash(src_dir: Path) -> str:
    """Return a stable content hash for a VASP calculation directory.

    Combines structure composition, KPOINTS grid, INCAR fingerprint,
    and POTCAR identifiers into a compact, repeatable string.
    Two directories with identical inputs produce the same hash.
    """
    # Structure component
    struct_tag = "unknown"
    for cand in (src_dir / "CONTCAR", src_dir / "POSCAR"):
        if cand.is_file():
            try:
                struct = Structure.from_file(str(cand))
                comp = struct.composition
                struct_tag = comp.formula.replace(" ", "")
                break
            except Exception:
                continue

    # KPOINTS component
    kpoints_path = src_dir / "KPOINTS"
    kpoints_tag = "nokpt"
    if kpoints_path.is_file():
        try:
            kpts = Kpoints.from_file(str(kpoints_path))
            style = kpts.style
            grid = kpts.kpts[0] if kpts.kpts else (0, 0, 0)
            if style == Kpoints.supported_modes.Gamma and max(grid) <= 1:
                kpoints_tag = "gamma"
            elif style == Kpoints.supported_modes.Line_mode:
                kpoints_tag = "band-structure"
            else:
                kpoints_tag = "".join(str(k) for k in grid[:3])
        except Exception:
            pass

    # INCAR component
    # POTCAR component
    potcar_fp = _potcar_fingerprint(src_dir)

    return f"{struct_tag}_{kpoints_tag}_{incar_fp}_{potcar_fp}"


def _extract_tags(
    incar: Incar | None = None,
    kpoints: Kpoints | None = None,
    structure: Structure | None = None,
    sga: SpacegroupAnalyzer | None = None,
) -> str:
    """Return comma-separated semantic tags for a VASP calculation.

    Combines information from INCAR (functional, method, physics),
    KPOINTS (k-mesh scheme, density), and POSCAR/CONTCAR (structure
    type, space group, cell size).

    Returns empty string when no info is available.
    """
    tags: list[str] = []

    # ── Structure tags ───────────────────────────────────────────────
    # Use composition string, e.g. "Na16Cl16", as a compact structure tag.
    if structure is not None:
        comp = structure.composition
        tags.append(comp.formula.replace(" ", ""))

    # ── KPOINTS tags ─────────────────────────────────────────────────
    # "gamma" for Gamma-only (single k-point), grid string for regular
    # mesh, "band-structure" for line-mode band paths.
    if kpoints is not None:
        style = kpoints.style
        kpts = kpoints.kpts[0] if kpoints.kpts else (0, 0, 0)
        if style == Kpoints.supported_modes.Gamma and max(kpts) <= 1:
            tags.append("gamma")
        elif style == Kpoints.supported_modes.Line_mode:
            tags.append("band-structure")
        else:
            tags.append("".join(str(k) for k in kpts[:3]))

    # ── INCAR tags ───────────────────────────────────────────────────
    if incar is None:
        return ",".join(tags) if tags else ""

    gga = (incar.get("GGA") or "").strip().upper()
    metagga = (incar.get("METAGGA") or "").strip().upper()
    hfcalc = bool(incar.get("LHFCALC"))
    hfscreen = incar.get("HFSCREEN", 0)
    ldau = bool(incar.get("LDAU"))
    ivdw = incar.get("IVDW", 0)
    spin = incar.get("ISPIN", 1)
    lsorbit = bool(incar.get("LSORBIT"))
    ibrion = incar.get("IBRION", 0)
    nfree = incar.get("NFREE", 0)
    lepsilon = bool(incar.get("LEPSILON"))
    loptics = bool(incar.get("LOPTICS"))
    lcalcpol = bool(incar.get("LCALCPOL"))
    ldipol = bool(incar.get("LDIPOL"))
    encut = incar.get("ENCUT", 0)

    # Functional
    if gga == "PE":
        tags.append("PBE")
    elif gga == "PS":
        tags.append("PBEsol")
    elif gga:
        tags.append(gga)
    if metagga == "SCAN":
        tags.append("SCAN")
    elif metagga == "R2SCAN":
        tags.append("R2SCAN")
    elif metagga:
        tags.append(f"metaGGA({metagga})")

    # ENCUT tier
    if encut:
        if encut >= 600:
            tags.append("high-encut")
        elif encut <= 300:
            tags.append("low-encut")

    # Hybrid
    if hfcalc:
        tags.append("hybrid")
        tags.append("HSE" if hfscreen > 0 else "PBE0")

    # Methods
    if ldau:
        tags.append("DFT+U")
    if ivdw:
        tags.append("DFT-D")

    # Physics
    if spin == 2:
        tags.append("spin")
    if lsorbit:
        tags.append("spin-orbit")

    # Calculation types
    if ibrion in (5, 6, 7, 8) or nfree >= 2:
        tags.append("phonon")
    if loptics:
        tags.append("optics")
    if lepsilon:
        tags.append("dielectric")
    if lcalcpol:
        tags.append("polarization")
    if ldipol:
        tags.append("dipole")

    return ",".join(tags) if tags else "default"

def _detect_calc_info(src_dir: Path) -> tuple[str, str, str]:
    """Auto-detect (formula, content_hash, task_name) from a VASP dir.

    Returns:
        formula: reduced formula from POSCAR or "unknown"
        content_hash: stable input fingerprint for dedup
        task_name: human-readable name (dir name or ``formula_mp-mpid``)
    """
    name = src_dir.name
    formula: str | None = None
    if "_mp-" in name:
        parts = name.split("_mp-", 1)
        formula = parts[0]
        task_name = name
    else:
        task_name = name
        for cand in (src_dir / "CONTCAR", src_dir / "POSCAR"):
            if cand.is_file():
                try:
                    struct = Structure.from_file(str(cand))
                    formula = struct.composition.reduced_formula
                    break
                except Exception:
                    continue
    formula = formula or "unknown"
    ch = _content_hash(src_dir)
    return formula, ch, task_name


def vasp_results_put(
    src_dir: Path,
    formula: str | None = None,
    content_hash: str | None = None,
    task_name: str | None = None,
) -> None:
    """Parse VASP results from *src_dir* and store in SQLite.

    When *formula*, *content_hash*, or *task_name* are omitted they are
    auto-detected from the directory contents (see :func:`_detect_calc_info`).

    Uses regex for robust total_energy/converged extraction (works even with
    truncated OUTCAR), then tries pymatgen Outcar/Vasprun for full structured
    data capture (best-effort).

    Beyond the standard Outcar parse, also extracts:
    - Fermi contact shift / dipolar hyperfine coupling (``read_fermi_contact_shift``)
    - Zero-field splitting tensor (spin-spin contribution, custom table parse)
    These extended fields are merged into ``outcar.data`` before serialization.
    """


    if formula is None or content_hash is None or task_name is None:
        f, ch, tn = _detect_calc_info(src_dir)
        formula = formula or f
        content_hash = content_hash or ch
        task_name = task_name or tn

    outcar_path = src_dir / "OUTCAR"
    if not outcar_path.is_file():
        logger.warning("No OUTCAR in %s, skipping cache.", src_dir)
        return

    import re as _re

    text = outcar_path.read_text()
    total_energy: float | None = None
    converged = 0
    m_e = _re.search(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)", text)
    if m_e:
        total_energy = float(m_e.group(1))
    if "General timing and accounting" in text[-4096:]:
        converged = 1

    outcar_json: str | None = None
    try:
        outcar = Outcar(str(outcar_path))
        try:
            outcar.read_fermi_contact_shift()
        except Exception:
            pass
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

    vasprun_json: str | None = None
    vasprun_path = src_dir / "vasprun.xml"
    if vasprun_path.is_file():
        try:
            v = Vasprun(str(vasprun_path))
            vasprun_json = json.dumps(v.as_dict())
            if getattr(v, "converged", True):
                converged = 1
        except Exception as exc:
            logger.warning("Failed to parse vasprun.xml in %s: %s", src_dir, exc)

    structure_json: str | None = None
    n_sites: int | None = None
    formula_pretty: str | None = None
    space_group: str | None = None
    struct: Structure | None = None
    _sga: SpacegroupAnalyzer | None = None
    for cand in (src_dir / "CONTCAR", src_dir / "POSCAR"):
        if cand.is_file():
            try:
                struct = Structure.from_file(str(cand))
                structure_json = json.dumps(struct.as_dict())
                n_sites = struct.num_sites
                formula_pretty = struct.composition.reduced_formula
                _sga = SpacegroupAnalyzer(struct, symprec=0.1)
                space_group = _sga.get_space_group_symbol()
            except Exception as exc:
                logger.warning("Failed to parse structure in %s: %s", cand, exc)
            break

    incar: Incar | None = None
    incar_json: str | None = None
    incar_path = src_dir / "INCAR"
    if incar_path.is_file():
        try:
            incar = Incar.from_file(str(incar_path))
            incar_json = json.dumps(incar.as_dict())
        except Exception as exc:
            logger.warning("Failed to parse INCAR in %s: %s", src_dir, exc)
    kpts: Kpoints | None = None
    tags = _extract_tags(incar=incar, kpoints=kpts, structure=struct, sga=_sga)
    kpoints_json: str | None = None
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
           (formula, content_hash, task_name, cached_at,
            total_energy, converged,
            outcar_json, vasprun_json,
            structure_json, incar_json, kpoints_json,
            n_sites, formula_pretty, space_group, tags, source_dir)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (formula, content_hash, task_name, time.time(),
         total_energy, converged,
         outcar_json, vasprun_json,
         structure_json, incar_json, kpoints_json,
         n_sites, formula_pretty, space_group, tags,
         str(src_dir.resolve())),
    )
    db.commit()
    logger.debug("Cached %s/%s: energy=%s  sites=%s  sg=%s",
                 formula, task_name, total_energy or "?", n_sites or "?", space_group or "?")


def vasp_results_get(formula: str, key: str) -> dict[str, Any] | None:
    """Return full calc result dict from cache, or None if not cached.

    *key* is tried first as ``content_hash``, then as ``task_name``
    for backward compatibility with old-style (formula, mpid) lookups.
    """
    db = _get_db()
    row = db.execute(
        "SELECT * FROM vasp_results WHERE formula=? AND content_hash=? AND converged=1",
        (formula, key),
    ).fetchone()
    if row is None:
        row = db.execute(
            "SELECT * FROM vasp_results WHERE formula=? AND task_name=? AND converged=1",
            (formula, key),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def vasp_results_delete(formula: str, content_hash: str) -> None:
    """Remove cached entry by formula + content_hash."""
    db = _get_db()
    db.execute("DELETE FROM vasp_results WHERE formula=? AND content_hash=?",
               (formula, content_hash))
    db.commit()
    logger.debug("Deleted cache for %s/%s", formula, content_hash)




