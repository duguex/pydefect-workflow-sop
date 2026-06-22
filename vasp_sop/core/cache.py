"""Global cache for MP query results and VASP calculations.

Cache hierarchy (``~/.vasp_sop/``):

::

  mp_cache/
    Si.json            ← MP phase list (联网一次后永不过期)
    poscars/
      mp-149/          ← POSCAR + POTCAR
        POSCAR
        POTCAR
  calc_cache/
    Si_mp-149/         ← VASP 计算结果（OUTCAR、CONTCAR、vasprun.xml）
      OUTCAR
      CONTCAR
    Si_mp-149_cpd/     ← 如果该相曾作为目标相，CPD 产物也缓存
      target_vertices.yaml
      standard_energies.yaml

优先级: 本地缓存 > 联网 MP API。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache root ──────────────────────────────────────────────────────────

CACHE_ROOT = Path.home() / ".vasp_sop"
MP_CACHE = CACHE_ROOT / "mp_cache"
POSCAR_CACHE = MP_CACHE / "poscars"
CALC_CACHE = CACHE_ROOT / "calc_cache"



# ══════════════════════════════════════════════════════════════════════════
# VASP calculation cache
# ══════════════════════════════════════════════════════════════════════════

_CALC_RESULT_FILES = ("OUTCAR", "CONTCAR", "vasprun.xml", "calc_results.json")
_CPD_RESULT_FILES = ("target_vertices.yaml", "standard_energies.yaml",
                     "composition_energies.yaml")


def _calc_key(formula: str, mpid: str) -> str:
    return f"{formula}_mp-{mpid}"


def calc_results_get(formula: str, mpid: str) -> Optional[Path]:
    """Return path to cached calc dir if OUTCAR exists, else None."""
    d = CALC_CACHE / _calc_key(formula, mpid)
    return d if (d / "OUTCAR").is_file() else None


def calc_results_put(formula: str, mpid: str, src_dir: Path) -> None:
    """Copy VASP outputs from *src_dir* to calc cache."""
    d = CALC_CACHE / _calc_key(formula, mpid)
    d.mkdir(parents=True, exist_ok=True)
    for fname in _CALC_RESULT_FILES:
        src = src_dir / fname
        if src.is_file():
            shutil.copy2(str(src), str(d / fname))
    # Also grab crisp output/ subdir
    output_dir = src_dir / "output"
    if output_dir.is_dir():
        for fname in _CALC_RESULT_FILES:
            src = output_dir / fname
            if src.is_file():
                shutil.copy2(str(src), str(d / fname))
    logger.debug("Cached calc results for %s (mp-%s)", formula, mpid)


def calc_cpd_get(formula: str, mpid: str) -> Optional[Path]:
    """Return path to cached CPD results dir if target_vertices exists."""
    d = CALC_CACHE / f"{_calc_key(formula, mpid)}_cpd"
    return d if (d / "target_vertices.yaml").is_file() else None


def calc_cpd_put(formula: str, mpid: str, cpd_root: Path) -> None:
    """Cache CPD results (target_vertices, standard_energies, …)."""
    d = CALC_CACHE / f"{_calc_key(formula, mpid)}_cpd"
    d.mkdir(parents=True, exist_ok=True)
    for fname in _CPD_RESULT_FILES:
        src = cpd_root / fname
        if src.is_file():
            shutil.copy2(str(src), str(d / fname))
    logger.debug("Cached CPD results for %s (mp-%s)", formula, mpid)


# ══════════════════════════════════════════════════════════════════════════
# Integrate with pipeline: copy results to cache after completion
# ══════════════════════════════════════════════════════════════════════════


def cache_target_results(
    formula: str, mpid: str, target_dir: Path, cpd_root: Path,
) -> None:
    """Called after structure_opt + CPD complete — writes to global cache."""
    calc_results_put(formula, mpid, target_dir)
    calc_cpd_put(formula, mpid, cpd_root)
    logger.info("Cached %s (mp-%s): VASP + CPD results", formula, mpid)
