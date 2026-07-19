"""Materials Project query and analysis tools.

Provides functions to download competing phases from Materials Project,
manage local caches, and infer VASP parameters from downloaded structures.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pymatgen.core import Composition

from vasp_sop.core.cache import MP_CACHE, POSCAR_CACHE
from vasp_sop.core.jobs import run_local

logger = logging.getLogger(__name__)

_MP_FLAG = "mp_flag"  # legacy compatibility marker; mp_state.json is authoritative
_MP_COMBO_SCHEMA = ".schema-v3"
_MP_STATE_SCHEMA = 2
_MOLECULE_RESOURCE_VERSION = "diatomic-reference-v2"
_MOLECULE_PHASES = {
    "H": "mol_H2",
    "N": "mol_N2",
    "O": "mol_O2",
    "F": "mol_F2",
    "Cl": "mol_Cl2",
}


def _expected_molecule_dirs(elements: list[str]) -> set[str]:
    return {
        _MOLECULE_PHASES[element]
        for element in set(elements)
        if element in _MOLECULE_PHASES
    }


def _validate_expected_molecule_dirs(target_dir: Path, elements: list[str]) -> None:
    missing = _expected_molecule_dirs(elements) - set(_phase_dir_names(target_dir))
    if missing:
        raise RuntimeError(
            "MP fetch did not create expected molecular references: "
            + ", ".join(sorted(missing))
        )


# ══════════════════════════════════════════════════════════════════════════
# Combined MP download cache (by element set)
# ══════════════════════════════════════════════════════════════════════════


def _combo_key(elements: list[str]) -> str:
    return "-".join(sorted(set(elements)))


def mp_combo_get(elements: list[str]) -> Optional[Path]:
    """Return a cache only when schema and expected molecular phases match."""
    d = MP_CACHE / _combo_key(elements)
    phase_dirs = (
        {child.name for child in d.iterdir() if child.is_dir()} if d.is_dir() else set()
    )
    expected_molecules = _expected_molecule_dirs(elements)
    return (
        d
        if d.is_dir()
        and (d / ".done").is_file()
        and (d / _MP_COMBO_SCHEMA).is_file()
        and expected_molecules <= phase_dirs
        else None
    )


def mp_combo_put(elements: list[str], src_root: Path) -> Path:
    """Stage a complete MP combo cache and replace any legacy phase set."""
    key = _combo_key(elements)
    dst = MP_CACHE / key
    staging = MP_CACHE / f".{key}.staging"
    MP_CACHE.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        shutil.rmtree(str(staging))
    staging.mkdir()
    try:
        for child in src_root.iterdir():
            if child.is_dir() and (
                "_mp-" in child.name or child.name.startswith("mol_")
            ):
                shutil.copytree(str(child), str(staging / child.name))
        (staging / ".done").touch()
        (staging / _MP_COMBO_SCHEMA).touch()
        if dst.exists():
            shutil.rmtree(str(dst))
        shutil.move(str(staging), str(dst))
    except Exception:
        if staging.exists():
            shutil.rmtree(str(staging))
        raise
    logger.debug("Cached MP combo %s -> %s", key, dst)
    return dst


def mp_combo_restore(elements: list[str], dst_root: Path) -> None:
    """Restore cached MP phases and remove stale managed phases."""
    src = MP_CACHE / _combo_key(elements)
    if not src.is_dir():
        return

    source_phases = [
        child
        for child in src.iterdir()
        if child.is_dir() and child.name not in {".done", _MP_COMBO_SCHEMA}
    ]
    managed_source = {
        child.name
        for child in source_phases
        if "_mp-" in child.name or child.name.startswith("mol_")
    }
    for child in dst_root.iterdir():
        if (
            child.is_dir()
            and ("_mp-" in child.name or child.name.startswith("mol_"))
            and child.name not in managed_source
        ):
            shutil.rmtree(str(child))

    for child in source_phases:
        dst = dst_root / child.name
        if dst.exists():
            shutil.rmtree(str(dst))
        shutil.copytree(str(child), str(dst))
    logger.info("MP cache: restored combo %s to %s", _combo_key(elements), dst_root)


# ══════════════════════════════════════════════════════════════════════════
# MP phase list cache (per-formula)
# ══════════════════════════════════════════════════════════════════════════


def mp_phases_get(formula: str) -> Optional[list[dict]]:
    """Return cached MP phase list for *formula*, or None."""
    path = MP_CACHE / f"{formula}_phases.json"
    if path.is_file():
        import json

        with open(path) as f:
            return json.load(f)
    return None


def mp_phases_put(formula: str, phases: list[dict]) -> None:
    """Cache MP phase list for *formula*."""
    import json

    MP_CACHE.mkdir(parents=True, exist_ok=True)
    with open(MP_CACHE / f"{formula}_phases.json", "w") as f:
        json.dump(phases, f, indent=2)
    logger.debug("Cached MP phases for %s (%d phases)", formula, len(phases))


def mp_poscar_get(mpid: str) -> Optional[Path]:
    """Return path to cached POSCAR for *mpid*, or None."""
    poscar = POSCAR_CACHE / mpid / "POSCAR"
    return poscar if poscar.is_file() else None


def mp_poscar_put(mpid: str, src_dir: Path) -> None:
    """Copy POSCAR + POTCAR from *src_dir* to cache."""
    dst = POSCAR_CACHE / mpid
    dst.mkdir(parents=True, exist_ok=True)
    for f in ("POSCAR", "POTCAR"):
        src = src_dir / f
        if src.is_file():
            shutil.copy2(str(src), str(dst / f))
    logger.debug("Cached POSCAR for mp-%s", mpid)


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════


def get_intrinsic_elements(formula: str) -> list[str]:
    """Parse a chemical formula into its constituent element symbols."""
    return list(Composition(formula).as_dict().keys())


def _fix_dirnames(cpd_root: Path) -> None:
    """Replace ``(`` → ``[`` and ``)`` → ``]`` in directory names.

    The MP download may produce directories containing parentheses,
    which cause issues for shell commands and YAML parsing.
    """
    for child in list(cpd_root.iterdir()):
        if child.is_dir() and ("(" in child.name or ")" in child.name):
            new_name = child.name.replace("(", "[").replace(")", "]")
            dst = child.with_name(new_name)
            if not dst.exists():
                shutil.move(str(child), str(dst))
                logger.info("Renamed %s -> %s", child.name, new_name)


_MP_STATE = "mp_state.json"
_MP_STATE_SCHEMA = 2


def _phase_dir_names(target_dir: Path) -> list[str]:
    return sorted(
        child.name
        for child in target_dir.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def _read_valid_mp_state(target_dir: Path, elements: list[str]) -> dict | None:
    state_path = target_dir / _MP_STATE
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    expected_elements = sorted(set(elements))
    phase_dirs = state.get("phase_dirs")
    if not (
        isinstance(state, dict)
        and state.get("schema") == _MP_STATE_SCHEMA
        and state.get("molecule_resource_version") == _MOLECULE_RESOURCE_VERSION
        and state.get("status") == "completed"
        and state.get("elements") == expected_elements
        and isinstance(phase_dirs, list)
        and phase_dirs
        and phase_dirs == _phase_dir_names(target_dir)
        and _expected_molecule_dirs(elements) <= set(phase_dirs)
    ):
        return None
    return state


def _write_mp_state(
    target_dir: Path,
    elements: list[str],
    *,
    source: str,
    cache: bool,
) -> None:
    _validate_expected_molecule_dirs(target_dir, elements)
    payload = {
        "schema": _MP_STATE_SCHEMA,
        "molecule_resource_version": _MOLECULE_RESOURCE_VERSION,
        "status": "completed",
        "elements": sorted(set(elements)),
        "phase_dirs": _phase_dir_names(target_dir),
        "source": source,
        "cache": cache,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    state_path = target_dir / _MP_STATE
    tmp_path = target_dir / f".{_MP_STATE}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp_path, state_path)


def fetch_candidate_phases(
    elements: list[str],
    target_dir: Path,
    use_cache: bool = True,
) -> Path:
    """Download competing phases from Materials Project.

    Runs ``pydefect_vasp mp`` or restores from the global combo cache.

    Args:
        elements: Chemical element symbols (e.g. ``['Ga', 'N']``).
        target_dir: Directory to write phase directories into (typically ``cpd/``).
        use_cache: If True, check and store in global combo cache.

    Returns:
        *target_dir* (for chaining).
    """
    state = _read_valid_mp_state(target_dir, elements)
    if state is not None:
        logger.info("MP fetch already done (valid %s).", _MP_STATE)
        return target_dir

    if (target_dir / _MP_FLAG).is_file():
        logger.warning("Ignoring legacy %s without valid %s.", _MP_FLAG, _MP_STATE)

    if use_cache:
        cached = mp_combo_get(elements)
        if cached:
            logger.info("MP combo cache HIT for %s, restoring ...", elements)
            mp_combo_restore(elements, target_dir)
            _fix_dirnames(target_dir)
            _write_mp_state(
                target_dir,
                elements,
                source="mp_combo_cache",
                cache=True,
            )
            return target_dir
        logger.info("MP combo cache MISS for %s, querying ...", elements)

    cmd = f"pydefect_vasp mp -e {' '.join(elements)} --e_above_hull 0.0005"
    logger.info("Running: %s", cmd)
    run_local(cmd, cwd=target_dir)
    _fix_dirnames(target_dir)
    _validate_expected_molecule_dirs(target_dir, elements)

    if use_cache:
        mp_combo_put(elements, target_dir)
    _write_mp_state(
        target_dir,
        elements,
        source="pydefect_vasp mp",
        cache=False,
    )
    return target_dir


def list_phases(
    cpd_root: Path,
    intrinsic_elements: list[str],
) -> dict[str, dict]:
    """Scan CPD directories and return info for relevant phases.

    Only directories whose composition contains at least one intrinsic
    element are kept.

    Returns:
        ``{dirname: {"formula": str, "mpid": str | None}}``
    """
    info: dict[str, dict] = {}
    for child in sorted(cpd_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name

        if "_mp-" in name:
            formula, mpid = name.split("_mp-", 1)
            mpid = f"mp-{mpid}"
        elif name.startswith("mol_"):
            formula = name[len("mol_") :]
            mpid = None
        else:
            continue

        composition = Composition(formula)
        elements = list(composition.as_dict().keys())
        # Keep only phases containing intrinsic elements
        if len(elements) == 1 or any(e in elements for e in intrinsic_elements):
            info[name] = {"formula": formula, "mpid": mpid}

    return info


def list_potcar_variants(
    formula: str,
    dopants: list[str],
) -> dict[str, list[str]]:
    """Enumerate available PAW_PBE POTCAR variants per element."""
    from pymatgen.core import SETTINGS

    potcar_dir = Path(SETTINGS.get("PMG_VASP_PSP_DIR", "")) / "POT_GGA_PAW_PBE_54"
    if not potcar_dir.is_dir():
        return {}

    elements = set(re.findall(r"[A-Z][a-z]?", formula)) | set(dopants)
    variants: dict[str, list[str]] = {}
    for el in sorted(elements):
        matches = sorted(
            d.name
            for d in potcar_dir.iterdir()
            if d.is_dir() and re.match(rf"^{re.escape(el)}(_|$)", d.name, re.IGNORECASE)
        )
        if matches:
            variants[el] = matches
    return variants


def detect_encut(potcar_path: Path) -> Optional[float]:
    """Detect ENCUT = 1.3 × max(ENMAX) from a POTCAR file."""
    if not potcar_path.is_file():
        return None
    max_enmax = 0.0
    try:
        text = potcar_path.read_text()
        for enmax in re.findall(r"ENMAX\s*=\s*([\d.]+)", text):
            max_enmax = max(max_enmax, float(enmax))
    except Exception:
        return None
    return round(max_enmax * 1.3, 1) if max_enmax > 0 else None


_DTFU_FALLBACK = frozenset(
    {
        "Sc",
        "Ti",
        "V",
        "Cr",
        "Mn",
        "Fe",
        "Co",
        "Ni",
        "Cu",
        "Zn",
        "Y",
        "Zr",
        "Nb",
        "Mo",
        "Tc",
        "Ru",
        "Rh",
        "Pd",
        "Ag",
        "Cd",
        "Hf",
        "Ta",
        "W",
        "Re",
        "Os",
        "Ir",
        "Pt",
        "Au",
        "Hg",
        "Ce",
        "Pr",
        "Nd",
        "Pm",
        "Sm",
        "Eu",
        "Gd",
        "Tb",
        "Dy",
        "Ho",
        "Er",
        "Tm",
        "Yb",
        "U",
    }
)


def needs_hubbard_u(poscar_path: Path) -> bool:
    """Return True if any species in POSCAR needs DFT+U."""
    try:
        from pymatgen.core import Structure

        s = Structure.from_file(str(poscar_path))
        return any(el in _DTFU_FALLBACK for el in s.symbol_set)
    except Exception:
        return False
