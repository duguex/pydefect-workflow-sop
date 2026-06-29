"""Global cache for VASP calculation results.

Uses maggma ``JSONStore`` for lightweight, file-based persistence with
MongoDB-like query syntax.  Metadata (energy, bandgap, tags, ...) lives
in ``meta.json``; large parsed-output blobs (OUTCAR, vasprun, etc.) live
in ``blobs.json``.

Swap cache root for testing via :func:`override_cache_root`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# ── Cache root (swappable for tests) ────────────────────────────────────

CACHE_ROOT: Path = Path.home() / ".vasp_sop"
MP_CACHE: Path = CACHE_ROOT / "mp_cache"
POSCAR_CACHE: Path = MP_CACHE / "poscars"
CALC_CACHE: Path = CACHE_ROOT / "calc_cache"  # kept for backward compat


def override_cache_root(p: Path) -> None:
    """Swap cache root (for testing)."""
    global CACHE_ROOT, MP_CACHE, POSCAR_CACHE, CALC_CACHE, _meta_store, _blob_store, _SUBMISSION_DB
    with _stores_lock:
        CACHE_ROOT = p
        MP_CACHE = CACHE_ROOT / "mp_cache"
        POSCAR_CACHE = MP_CACHE / "poscars"
        CALC_CACHE = CACHE_ROOT / "calc_cache"
        _meta_store = None
        _blob_store = None
        _SUBMISSION_DB = None


# ── Store singletons (lazy-init) ───────────────────────────────────────

_meta_store = None
_blob_store = None
_stores_lock = threading.Lock()
_CACHE_KEY = ["formula", "content_hash"]


def _get_stores():
    """Return (meta_store, blob_store), creating on first access."""
    global _meta_store, _blob_store
    with _stores_lock:
        if _meta_store is None:
            from maggma.stores import JSONStore
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            _meta_store = JSONStore(
                paths=[str(CACHE_ROOT / "meta.json")],
                key=_CACHE_KEY,
                read_only=False,
            )
            _meta_store.connect()
        if _blob_store is None:
            from maggma.stores import JSONStore
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            _blob_store = JSONStore(
                paths=[str(CACHE_ROOT / "blobs.json")],
                key="content_hash",
                read_only=False,
            )
            _blob_store.connect()
    return _meta_store, _blob_store


# ══════════════════════════════════════════════════════════════════════════
# VASP I/O fingerprint helpers
# ══════════════════════════════════════════════════════════════════════════

_INCAR_FINGERPRINT_KEYS = ("ENCUT", "PREC", "ISMEAR", "SIGMA", "ISIF",
                           "LDAU", "LDAUTYPE", "LDAUU", "LDAUJ", "LDAUL",
                           "GGA", "IVDW", "LASPH", "METAGGA")


def _incar_fingerprint(src_dir: Path) -> str:
    incar_path = src_dir / "INCAR"
    if not incar_path.is_file():
        return "default"
    try:
        from pymatgen.io.vasp.inputs import Incar
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
    from pymatgen.io.vasp.inputs import Kpoints
    from pymatgen.core.structure import Structure

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

    incar_fp = _incar_fingerprint(src_dir)
    potcar_fp = _potcar_fingerprint(src_dir)
    return f"{struct_tag}_{kpoints_tag}_{incar_fp}_{potcar_fp}"


# ══════════════════════════════════════════════════════════════════════════
# Tag extraction
# ══════════════════════════════════════════════════════════════════════════

def _extract_tags(
    incar=None,
    kpoints=None,
    structure=None,
    sga=None,
) -> str:
    from pymatgen.io.vasp.inputs import Incar, Kpoints
    from pymatgen.core.structure import Structure
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    tags: list[str] = []

    if structure is not None:
        comp = structure.composition
        tags.append(comp.formula.replace(" ", ""))

    if kpoints is not None:
        style = kpoints.style
        kpts = kpoints.kpts[0] if kpoints.kpts else (0, 0, 0)
        if style == Kpoints.supported_modes.Gamma and max(kpts) <= 1:
            tags.append("gamma")
        elif style == Kpoints.supported_modes.Line_mode:
            tags.append("band-structure")
        else:
            tags.append("".join(str(k) for k in kpts[:3]))

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

    if encut:
        if encut >= 600:
            tags.append("high-encut")
        elif encut <= 300:
            tags.append("low-encut")

    if hfcalc:
        tags.append("hybrid")
        tags.append("HSE" if hfscreen > 0 else "PBE0")

    if ldau:
        tags.append("DFT+U")
    if ivdw:
        tags.append("DFT-D")

    if spin == 2:
        tags.append("spin")
    if lsorbit:
        tags.append("spin-orbit")

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


def _tags_from_doc(doc) -> str:
    """Extract tag string from a TaskDoc object."""
    tags: list[str] = []
    run_type = getattr(doc, "run_type", None)
    calc_type = getattr(doc, "calc_type", None)

    tag_map = {"GGA": "PBE", "GGA+U": "DFT+U", "HSE": "HSE"}
    if run_type and run_type.value in tag_map:
        tags.append(tag_map[run_type.value])
    elif run_type:
        tags.append(str(run_type.value))

    ct = str(calc_type) if calc_type else ""
    if "Static" in ct:
        tags.append("static")
    elif "Relax" in ct:
        tags.append("relax")

    if hasattr(doc, "symmetry") and doc.symmetry:
        tags.append(doc.symmetry.crystal_system.lower() if hasattr(doc.symmetry, "crystal_system") else "unknown")

    return ",".join(tags) if tags else "default"


# ══════════════════════════════════════════════════════════════════════════
# Detection
# ══════════════════════════════════════════════════════════════════════════

def _detect_calc_info(src_dir: Path) -> tuple[str, str, str]:
    from pymatgen.core.structure import Structure

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


# ══════════════════════════════════════════════════════════════════════════
# Core cache API
# ══════════════════════════════════════════════════════════════════════════

def _parse_vasp_dir(src_dir: Path) -> dict[str, Any]:
    """Parse VASP directory via TaskDoc, fall back to regex + pymatgen."""
    outcar_path = src_dir / "OUTCAR"
    if not outcar_path.is_file():
        return {"converged": False, "total_energy": None}

    # ── Try TaskDoc.from_directory first ────────────────────────────
    try:
        from emmet.core.tasks import TaskDoc
        doc = TaskDoc.from_directory(src_dir)

        return {
            "converged": doc.state == "successful",
            "total_energy": doc.output.energy if doc.output else None,
            "bandgap": doc.output.bandgap if doc.output else None,
            "formula_pretty": doc.formula_pretty,
            "nsites": doc.nsites,
            "space_group": doc.symmetry.crystal_system if doc.symmetry else None,
            "calc_type": str(doc.calc_type) if doc.calc_type else None,
            "tags": _tags_from_doc(doc),
            "parsed_by": "TaskDoc",
        }
    except Exception as exc:
        logger.debug("TaskDoc parse failed for %s: %s, falling back to regex",
                       src_dir, exc)

    # ── Regex fallback ──────────────────────────────────────────────
    import re as _re
    from pymatgen.io.vasp.outputs import Outcar, Vasprun
    from pymatgen.io.vasp.inputs import Incar, Kpoints
    from pymatgen.core.structure import Structure
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    text = outcar_path.read_text()
    total_energy: float | None = None
    converged = False
    m_e = _re.search(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)", text)
    if m_e:
        total_energy = float(m_e.group(1))
    if "General timing and accounting" in text[-4096:]:
        converged = True

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
            except Exception:
                pass
            break

    incar: Incar | None = None
    incar_path = src_dir / "INCAR"
    if incar_path.is_file():
        try:
            incar = Incar.from_file(str(incar_path))
        except Exception:
            pass

    kpts: Kpoints | None = None
    tags = _extract_tags(incar=incar, kpoints=kpts, structure=struct, sga=_sga)

    return {
        "converged": converged,
        "total_energy": total_energy,
        "bandgap": None,
        "formula_pretty": formula_pretty,
        "nsites": n_sites,
        "space_group": space_group,
        "calc_type": None,
        "tags": tags,
        "parsed_by": "regex",
    }


def _sanitize_value(v):
    """Convert non-JSON-serializable values to plain types recursively."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, dict):
        return {kk: _sanitize_value(vv) for kk, vv in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanitize_value(item) for item in v]
    if hasattr(v, 'magnitude'):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def _sanitize_dict(d: dict) -> dict:
    """Convert non-JSON-serializable values (e.g. FloatWithUnit) to plain types."""
    return _sanitize_value(d)


def _build_blob(src_dir: Path) -> dict[str, Any]:
    """Build the blob dict (big JSON payloads) from a VASP directory."""
    from pymatgen.io.vasp.outputs import Outcar, Vasprun
    from pymatgen.io.vasp.inputs import Incar, Kpoints
    from pymatgen.core.structure import Structure
    import re as _re

    blob: dict[str, Any] = {}

    outcar_path = src_dir / "OUTCAR"
    if outcar_path.is_file():
        try:
            outcar = Outcar(str(outcar_path))
            d = outcar.as_dict()
            d["final_energy"] = outcar.final_energy
            blob["outcar_dict"] = _sanitize_dict(d)
        except Exception:
            text = outcar_path.read_text()
            m = _re.search(r"free\s+energy\s+TOTEN\s*=\s*([-\d.]+)", text)
            if m:
                blob["outcar_dict"] = {
                    "final_energy": float(m.group(1)),
                    "converged": "General timing and accounting" in text[-4096:],
                }

    vasprun_path = src_dir / "vasprun.xml"
    if vasprun_path.is_file():
        try:
            v = Vasprun(str(vasprun_path))
            blob["vasprun_dict"] = _sanitize_dict(v.as_dict())
        except Exception as exc:
            logger.debug("vasprun parse failed for %s: %s", src_dir, exc)

    for cand in (src_dir / "CONTCAR", src_dir / "POSCAR"):
        if cand.is_file():
            try:
                struct = Structure.from_file(str(cand))
                blob["structure_dict"] = _sanitize_dict(struct.as_dict())
            except Exception:
                pass
            break

    incar_path = src_dir / "INCAR"
    if incar_path.is_file():
        try:
            blob["incar_dict"] = _sanitize_dict(Incar.from_file(str(incar_path)).as_dict())
        except Exception:
            pass

    kpts_path = src_dir / "KPOINTS"
    if kpts_path.is_file():
        try:
            blob["kpoints_dict"] = _sanitize_dict(Kpoints.from_file(str(kpts_path)).as_dict())
        except Exception:
            pass

    return blob


def vasp_results_put(
    src_dir: Path,
    formula: str | None = None,
    content_hash: str | None = None,
    task_name: str | None = None,
) -> None:
    """Parse VASP results from *src_dir* and store in cache.

    Parsing: tries ``TaskDoc.from_directory()`` first, falls back to
    regex + pymatgen.  Metadata goes to ``meta.json``, large blobs
    go to ``blobs.json``.
    """
    if formula is None or content_hash is None or task_name is None:
        f, ch, tn = _detect_calc_info(src_dir)
        formula = formula or f
        content_hash = content_hash or ch
        task_name = task_name or tn

    parsed = _parse_vasp_dir(src_dir)
    if not parsed["converged"] and parsed["total_energy"] is None:
        logger.debug("Skipping %s: no converged result and no energy", src_dir)
        return

    meta_store, blob_store = _get_stores()

    meta_record = {
        "content_hash": content_hash,
        "formula": formula,
        "task_name": task_name,
        "cached_at": time.time(),
        "total_energy": parsed["total_energy"],
        "bandgap": parsed.get("bandgap"),
        "converged": int(parsed["converged"]),
        "calc_type": parsed.get("calc_type"),
        "n_sites": parsed.get("nsites"),
        "formula_pretty": parsed.get("formula_pretty"),
        "space_group": parsed.get("space_group"),
        "tags": parsed.get("tags", ""),
        "source_dir": str(src_dir.resolve()),
        "parsed_by": parsed.get("parsed_by", "unknown"),
    }
    meta_store.update([meta_record])

    if parsed["converged"]:
        blob = _build_blob(src_dir)
        if blob:
            import json as _json
            from monty.json import MontyEncoder
            blob_str = _json.dumps(blob, cls=MontyEncoder)
            blob_doc = {"content_hash": content_hash, "blob_json": blob_str}
            blob_store.update([blob_doc])


def vasp_results_get(formula: str, key: str) -> dict[str, Any] | None:
    """Return cached result dict for (formula, key).

    *key* is tried first as ``content_hash``, then as ``task_name``.
    """
    meta_store, blob_store = _get_stores()
    row = meta_store.query_one(
        {"formula": formula, "content_hash": key, "converged": 1}
    )
    if row is None:
        row = meta_store.query_one(
            {"formula": formula, "task_name": key, "converged": 1}
        )
    if row is None:
        return None

    result = dict(row)
    blob = blob_store.query_one({"content_hash": row["content_hash"]})
    if blob and "blob_json" in blob:
        import json as _json
        blob_data = _json.loads(blob["blob_json"])
        for field in ("outcar_dict", "vasprun_dict", "structure_dict",
                       "incar_dict", "kpoints_dict"):
            json_key = field.replace("_dict", "_json")
            if field in blob_data:
                result[json_key] = _json.dumps(blob_data[field])
            else:
                result[json_key] = None
    else:
        for field in ("outcar_json", "vasprun_json", "structure_json",
                       "incar_json", "kpoints_json"):
            result[field] = None
    return result


def cache_lookup(src_dir: Path) -> dict[str, Any] | None:
    """Return cached result for *src_dir*, or None."""
    formula, ch, _ = _detect_calc_info(src_dir)
    return vasp_results_get(formula, ch)


def vasp_results_delete(formula: str, content_hash: str) -> None:
    """Remove cached entry by formula + content_hash."""
    meta_store, blob_store = _get_stores()
    meta_store.remove_docs({"formula": formula, "content_hash": content_hash})
    blob_store.remove_docs({"content_hash": content_hash})
    logger.debug("Deleted cache for %s/%s", formula, content_hash)


def query(
    formula: str | None = None,
    functional: str | None = None,
    calc_type: str | None = None,
    tags_contains: str | None = None,
    bandgap_min: float | None = None,
    converged_only: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Semantic cross-project cache query.

    Examples::

        query(formula="GaN")
        query(functional="HSE", calc_type="Static")
        query(tags_contains="DFT+U", bandgap_min=2.0)
    """
    meta_store, _ = _get_stores()
    criteria: dict[str, Any] = {}
    if formula:
        criteria["formula"] = formula
    if functional:
        criteria["tags"] = {"$regex": functional}
    if calc_type:
        criteria["calc_type"] = {"$regex": calc_type}
    if tags_contains:
        if "tags" in criteria:
            existing = criteria["tags"]
            criteria["$and"] = [
                {"tags": existing},
                {"tags": {"$regex": tags_contains}},
            ]
        else:
            criteria["tags"] = {"$regex": tags_contains}
    if bandgap_min is not None:
        criteria["bandgap"] = {"$gte": bandgap_min}
    if converged_only:
        criteria["converged"] = 1

    return list(meta_store.query(criteria=criteria))[:limit]


# ══════════════════════════════════════════════════════════════════════════
# Bulk operations
# ══════════════════════════════════════════════════════════════════════════

def scan_converged_dirs(root: Path, *, skip_cached: bool = True) -> list[Path]:
    """Scan *root* recursively for directories with converged OUTCARs.

    Returns a sorted list of directories containing converged OUTCARs.
    When *skip_cached* is True, directories already in the cache are excluded.
    """
    dirs: list[Path] = []
    for outcar in sorted(root.rglob("OUTCAR")):
        d = outcar.parent
        if d.name == "output" and (d.parent / "OUTCAR").is_file():
            continue
        if skip_cached and cache_lookup(d) is not None:
            continue
        text = outcar.read_text()
        if "General timing and accounting" not in text[-4096:]:
            continue
        dirs.append(d)
    return dirs


def backfill_all(root: Path, max_workers: int = 16) -> int:
    """Cache all converged VASP calculations under *root* not already cached.

    Parses in parallel for speed, then writes results in a single batch
    to avoid JSONStore concurrency issues.
    """
    dirs = scan_converged_dirs(root)
    if not dirs:
        return 0

    from concurrent.futures import ProcessPoolExecutor, as_completed

    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_parse_and_build, d): d
            for d in dirs
        }
        for future in as_completed(futures):
            d = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", d, exc)

    meta_store, blob_store = _get_stores()
    meta_docs: list[dict] = []
    blob_docs: list[dict] = []
    for r in results:
        meta_docs.append(r["meta"])
        if r.get("blob"):
            blob_docs.append(r["blob"])

    if meta_docs:
        meta_store.update(meta_docs)
    if blob_docs:
        blob_store.update(blob_docs)

    return len(meta_docs)


def _parse_and_build(src_dir: Path) -> dict[str, Any]:
    """Worker function for parallel backfill. Returns {meta, blob}."""
    f, ch, tn = _detect_calc_info(src_dir)
    parsed = _parse_vasp_dir(src_dir)
    meta = {
        "content_hash": ch,
        "formula": f,
        "task_name": tn,
        "cached_at": time.time(),
        "total_energy": parsed.get("total_energy"),
        "bandgap": parsed.get("bandgap"),
        "converged": int(parsed.get("converged", False)),
        "calc_type": parsed.get("calc_type"),
        "n_sites": parsed.get("nsites"),
        "formula_pretty": parsed.get("formula_pretty"),
        "space_group": parsed.get("space_group"),
        "tags": parsed.get("tags", ""),
        "source_dir": str(src_dir.resolve()),
        "parsed_by": parsed.get("parsed_by", "unknown"),
    }
    result: dict[str, Any] = {"meta": meta}
    if parsed.get("converged"):
        blob = _build_blob(src_dir)
        if blob:
            import json as _json
            from monty.json import MontyEncoder
            result["blob"] = {
                "content_hash": ch,
                "blob_json": _json.dumps(blob, cls=MontyEncoder),
            }
    return result


# ══════════════════════════════════════════════════════════════════════════
# Migration from old SQLite cache
# ══════════════════════════════════════════════════════════════════════════

def migrate_from_sqlite() -> int:
    """Migrate old SQLite cache to JSONStore. Returns number of migrated records."""
    old_db_path = CACHE_ROOT / "cache.db"
    if not old_db_path.is_file():
        logger.info("No old cache.db found, nothing to migrate.")
        return 0

    db = sqlite3.connect(str(old_db_path))
    db.row_factory = sqlite3.Row

    try:
        rows = list(db.execute(
            "SELECT * FROM vasp_results WHERE converged = 1"
        ))
    except sqlite3.OperationalError:
        logger.info("No vasp_results table in old cache.db.")
        db.close()
        return 0
    db.close()

    meta_store, blob_store = _get_stores()
    meta_docs: list[dict] = []
    blob_docs: list[dict] = []
    count = 0

    for row in rows:
        d = dict(row)
        ch = d.get("content_hash", "")
        if not ch:
            continue

        meta_doc = {
            "content_hash": ch,
            "formula": d.get("formula", "unknown"),
            "task_name": d.get("task_name", ""),
            "cached_at": d.get("cached_at", time.time()),
            "total_energy": d.get("total_energy"),
            "bandgap": d.get("bandgap"),
            "converged": int(d.get("converged", 1)),
            "calc_type": d.get("calc_type"),
            "nsites": d.get("n_sites"),
            "formula_pretty": d.get("formula_pretty"),
            "space_group": d.get("space_group"),
            "tags": d.get("tags", ""),
            "source_dir": d.get("source_dir", ""),
            "parsed_by": "migrated",
        }
        meta_docs.append(meta_doc)

        blob_fields = {}
        for field in ("outcar_json", "vasprun_json", "structure_json",
                       "incar_json", "kpoints_json"):
            val = d.get(field)
            if val:
                try:
                    blob_fields[field.replace("_json", "_dict")] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        if blob_fields:
            blob_docs.append({"content_hash": ch, **blob_fields})

        count += 1

    if meta_docs:
        meta_store.update(meta_docs)
    if blob_docs:
        blob_store.update(blob_docs)

    logger.info("Migrated %d records from SQLite to JSONStore.", count)
    return count


# ══════════════════════════════════════════════════════════════════════════
# Cache status / listing
# ══════════════════════════════════════════════════════════════════════════

def list_cache(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent cache entries."""
    meta_store, _ = _get_stores()
    results = list(meta_store.query(
        criteria={"converged": 1},
        properties=["content_hash", "formula", "task_name", "cached_at",
                     "total_energy", "bandgap", "calc_type", "nsites",
                     "tags"],
    ))
    results.sort(key=lambda r: r.get("cached_at", 0), reverse=True)
    return results[:limit]


def cache_stats() -> dict[str, Any]:
    """Return aggregate statistics about the cache."""
    meta_store, _ = _get_stores()
    total = meta_store.count()
    converged = meta_store.count({"converged": 1})
    formulas = meta_store.distinct("formula")
    return {
        "total_entries": total,
        "converged_entries": converged,
        "unique_formulas": len(formulas),
        "formulas": sorted(formulas),
    }


# ══════════════════════════════════════════════════════════════════════════
# Submission tracking — cross-process VASP job state via SQLite
# ══════════════════════════════════════════════════════════════════════════

_SUBMISSION_DB: sqlite3.Connection | None = None


def _submission_db() -> sqlite3.Connection:
    """Return the submission-tracking SQLite connection (WAL mode)."""
    global _SUBMISSION_DB
    with _stores_lock:
        if _SUBMISSION_DB is None:
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(CACHE_ROOT / "submissions.db"), timeout=10)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS submitted (
                dir_path TEXT PRIMARY KEY,
                task_name TEXT NOT NULL,
                submitted_at REAL NOT NULL
            )""")
            _SUBMISSION_DB = db
    return _SUBMISSION_DB


def mark_submitted(dir_path: str, task_name: str) -> None:
    """Record that a VASP job has been submitted for *dir_path*."""
    db = _submission_db()
    db.execute(
        "INSERT OR REPLACE INTO submitted VALUES (?, ?, ?)",
        (dir_path, task_name, time.time()),
    )
    db.commit()


def is_submitted(dir_path: str, *, stale_hours: float = 6.0) -> bool:
    """Return True if a submission is recorded and not stale.

    A submission is considered stale (and eligible for re-submit) if
    it was recorded more than *stale_hours* ago.  This handles the case
    where crisp lost the job (e.g. node failure, job preemption).
    """
    db = _submission_db()
    row = db.execute(
        "SELECT submitted_at FROM submitted WHERE dir_path = ?",
        (dir_path,),
    ).fetchone()
    if row is None:
        return False
    elapsed = time.time() - row[0]
    return elapsed < stale_hours * 3600


def clear_submission(dir_path: str) -> None:
    """Remove a submission record (job completed or cancelled)."""
    db = _submission_db()
    with _stores_lock:
        db.execute("DELETE FROM submitted WHERE dir_path = ?", (dir_path,))
        db.commit()


def _get_submitted_dirs() -> list[str]:
    """Return all active (non-stale) submitted dir paths."""
    db = _submission_db()
    cutoff = time.time() - 6 * 3600
    rows = db.execute(
        "SELECT dir_path FROM submitted WHERE submitted_at > ?",
        (cutoff,),
    ).fetchall()
    return [r[0] for r in rows]


def clear_stale_submissions(stale_hours: float = 6.0) -> int:
    """Remove all submissions older than *stale_hours*.  Returns count."""
    db = _submission_db()
    cutoff = time.time() - stale_hours * 3600
    n = db.execute("DELETE FROM submitted WHERE submitted_at < ?", (cutoff,)).rowcount
    db.commit()
    return n