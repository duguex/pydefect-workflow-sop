
"""Defect structure generation — supercell, enumeration, VASP inputs."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path

import yaml

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.io import prepare_inputs
from vasp_sop.defect import pydefect_adapter as _pdad

logger = logging.getLogger(__name__)

_DOS_EXTREMA = "../unitcell/dos/volumetric_data_local_extrema.json"


def build_all(
    defect_root: Path,
    target_dir: Path,
    config: PipelineConfig,
) -> None:
    """Build supercell, enumerate defects, generate VASP inputs (all local).

    Uses POSCAR (not CONTCAR) from *target_dir* — VASP hasn't run yet
    at this point in the pipeline.  The unrelaxed lattice is fine for
    supercell sizing; the defect VASP calculations will relax the atoms.
    """
    defect_root.mkdir(parents=True, exist_ok=True)
    poscar = target_dir / "POSCAR"
    contcar = target_dir / "CONTCAR"
    if not poscar.is_file():
        raise FileNotFoundError(f"Target POSCAR not found at {poscar}.")
    # Prefer CONTCAR (relaxed) when available; otherwise fall back to POSCAR.
    # The supercell sizing from the unrelaxed lattice is fine, but the defect
    # VASP input generation uses the relaxed cell parameters for better accuracy.
    uc_contcar = contcar if contcar.is_file() else poscar
    logger.info("Building supercell from %s", uc_contcar.name)
    # ── Config-fingerprint guard ───────────────────────────────────
    # Detect plan.yaml changes that affect the build.  If the current
    # config differs from the last build, clear all flag files so the
    # builder re-generates with the new settings.
    _check_rebuild(defect_root, config)

    _build_supercell(defect_root, uc_contcar, config)
    _handle_interstitials(defect_root, config)
    _generate_defect_list(defect_root, config)
    _generate_structures(defect_root)
    _generate_vasp_inputs(defect_root, config)

    _fix_defect_nelect(defect_root)
    # Write fingerprint *after* successful build.
    _write_fingerprint(defect_root, config)


# ══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════


def _build_supercell(defect_root: Path, uc_contcar: Path, config: PipelineConfig) -> None:
    """Construct the supercell — dispatches to pydefect or doped based on config."""
    sc_info = defect_root / "supercell_info.json"
    if sc_info.is_file():
        logger.info("Supercell info already exists, skipping supercell construction.")
        return

    import time as _time
    # Ensure NFS visibility before subprocess.run(cwd=...)
    _time.sleep(0.5)

    if config.supercell_tool == "doped":
        _build_supercell_doped(defect_root, uc_contcar, config)
    else:
        _build_supercell_pydefect(defect_root, uc_contcar, config)


def _build_supercell_pydefect(defect_root: Path, uc_contcar: Path, config: PipelineConfig) -> None:
    """Construct the supercell via pydefect CLI.

    Falls back to atom-count bounds (``--min_atoms``/``--max_atoms``).
    Note: this fallback does NOT honor ``config.supercell_min_distance`` —
    pydefect's CLI has no ``--min_distance`` flag. This is by design: the
    ``doped`` happy path is the canonical way to satisfy a minimum
    image-distance constraint. See issue #15.
    """
    _pdad.make_supercell(defect_root, uc_contcar, config)


def _build_supercell_doped(defect_root: Path, uc_contcar: Path, config: PipelineConfig) -> None:
    """Construct the supercell via doped, bypassing pydefect's atom-count floor.

    Uses ``doped.generation.get_ideal_supercell_matrix`` to find a small matrix,
    builds the supercell, then writes a pydefect-compatible ``supercell_info.json``.
    """
    try:
        from doped.generation import get_ideal_supercell_matrix
    except ImportError:
        logger.warning("doped not available, falling back to pydefect supercell.")
        _build_supercell_pydefect(defect_root, uc_contcar, config)
        return

    from pymatgen.core.structure import Structure

    import numpy as np

    uc = Structure.from_file(str(uc_contcar))
    min_image_distance = config.supercell_min_distance
    matrix = get_ideal_supercell_matrix(
        uc, min_image_distance=min_image_distance,
    )

    if matrix is None:
        logger.warning(
            "get_ideal_supercell_matrix returned None for %s, "
            "falling back to pydefect supercell.",
            uc_contcar,
        )
        _build_supercell_pydefect(defect_root, uc_contcar, config)
        return

    sc = uc * matrix

    # ── Build symmetry-based site groups ──────────────────────────────
    # Delegate to vise.StructureSymmetrizer — it groups equivalent sites,
    # sorts `equivalent_atoms` indices, and handles edge cases (centering,
    # time-reversal, angle tolerance) that the prior hand-rolled loop did
    # not. This resolves issues #19 (re-implementation) and #21 (sort order).
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    from vise.util.structure_symmetrizer import StructureSymmetrizer

    sga = SpacegroupAnalyzer(sc, symprec=0.1)
    spg = sga.get_space_group_symbol()
    sites = StructureSymmetrizer(sc, symprec=0.1).sites

    # ── Build pydefect-compatible SupercellInfo ───────────────────────
    from pydefect.input_maker.supercell_info import SupercellInfo

    sc_info = SupercellInfo(
        structure=sc,
        space_group=spg,
        transformation_matrix=matrix.tolist(),
        sites=sites,
        interstitials=[],
        unitcell_structure=uc,
    )
    sc_info.to_json_file(str(defect_root / "supercell_info.json"))


def _handle_interstitials(defect_root: Path, config: PipelineConfig) -> None:
    """Handle interstitial site placement if enabled."""
    if not config.interstitial:
        return

    sc_info = defect_root / "supercell_info.json"
    if not sc_info.is_file():
        return

    with open(sc_info) as f:
        sc_data = json.load(f)

    interstitials = sc_data.get("interstitials", [])
    if interstitials:
        logger.info("Interstitials already defined in supercell_info.json, skipping.")
        return

    dos_extrema = (config.root / _DOS_EXTREMA).resolve()
    if not dos_extrema.is_file():
        logger.warning("DOS extrema file not found at %s, skipping interstitial placement.", dos_extrema)
        return

    logger.info("Candidates for interstitials (from %s):", dos_extrema)
    _pdad.print_dos_extrema(defect_root, dos_extrema)

    if not config.interstitial_indices:
        raise RuntimeError(
            "Interstitials requested but no `interstitial_indices` provided. "
            "Check the candidate list above and set "
            "`defects.interstitial_indices` in plan.yaml."
        )

    interstitial_sites = " ".join(config.interstitial_indices)
    _pdad.atom_indices(defect_root, dos_extrema, interstitial_sites)



def _generate_defect_list(defect_root: Path, config: PipelineConfig) -> None:
    """Generate ``defect_in.yaml`` using doped charge prediction or pydefect fallback."""
    defect_in = defect_root / "defect_in.yaml"
    if defect_in.is_file():
        logger.info("defect_in.yaml already exists, skipping defect list generation.")
        return

    use_doped = config.charge_state_gen_kwargs.get("use_doped", True)
    method = "pydefect"

    if use_doped:
        try:
            from doped.generation import (
                guess_defect_charge_states,
                get_vacancy_charge_states,
            )
            method = "doped"
        except ImportError:
            logger.warning(
                "doped not available for charge state prediction, "
                "falling back to pydefect ds."
            )

    if method == "doped":
        _generate_defect_list_doped(defect_root, config)
    else:
        _generate_defect_list_pydefect(defect_root, config)

    if defect_in.is_file():
        with open(defect_in) as f:
            data = yaml.safe_load(f)
        logger.info("Defect list (method=%s):", method)
        for defect, valence in (data or {}).items():
            logger.info("  %s: %s", defect, valence)


def _generate_defect_list_pydefect(defect_root: Path, config: PipelineConfig) -> None:
    """Fallback: run ``pydefect ds`` to produce ``defect_in.yaml``."""
    _pdad.defect_list(defect_root, config.dopant_elements)


def _generate_defect_list_doped(defect_root: Path, config: PipelineConfig) -> None:
    """Use doped's probability model to predict charge states and write defect_in.yaml.

    Reads supercell_info.json for host structure info, calls doped's
    guess_defect_charge_states / get_vacancy_charge_states, and writes
    the predicted charge states to defect_in.yaml.
    """
    from doped.generation import (
        guess_defect_charge_states,
        get_vacancy_charge_states,
    )

    sc_info_path = defect_root / "supercell_info.json"
    if not sc_info_path.is_file():
        logger.warning(
            "supercell_info.json not found, falling back to pydefect ds."
        )
        _generate_defect_list_pydefect(defect_root, config)
        return

    with open(sc_info_path) as f:
        sc_data = json.load(f)

    probability_threshold = config.charge_state_gen_kwargs.get(
        "probability_threshold", 0.0075
    )
    padding = config.charge_state_gen_kwargs.get("padding", 1)

    try:
        # Use doped's charge state prediction
        defect_charges = guess_defect_charge_states(
            sc_info_path=str(sc_info_path),
            probability_threshold=probability_threshold,
            padding=padding,
        )

        # Convert to pydefect-compatible defect_in.yaml format
        defect_in_data = {}
        for defect_name, charges in defect_charges.items():
            if isinstance(charges, (list, tuple)):
                defect_in_data[defect_name] = list(charges)
            else:
                defect_in_data[defect_name] = charges

        defect_in = defect_root / "defect_in.yaml"
        with open(defect_in, "w") as f:
            yaml.dump(defect_in_data, f, default_flow_style=None, sort_keys=False)

        logger.info(
            "doped charge state prediction: %d defects, threshold=%.4f, padding=%d",
            len(defect_in_data), probability_threshold, padding,
        )
    except Exception as exc:
        logger.warning(
            "doped charge state prediction failed (%s), falling back to pydefect ds.",
            exc,
        )
        _generate_defect_list_pydefect(defect_root, config)


def _generate_structures(defect_root: Path) -> None:
    """Run ``pydefect_vasp de`` to generate individual defect structures."""
    flag = defect_root / "defect_generate_flag"
    if flag.is_file():
        logger.info("Defect structures already generated, skipping.")
        return

    _pdad.defect_structures(defect_root)
    flag.touch()


def _generate_vasp_inputs(defect_root: Path, config: PipelineConfig) -> None:
    """Generate VASP inputs for every defect directory.

    Generates INCAR/POTCAR/KPOINTS once via ``prepare_inputs``, then
    copies them to remaining directories to avoid N repeated subprocess
    calls (each ``vise vs`` starts a fresh Python interpreter).
    """
    from vasp_sop.vasp.io import prepare_inputs, input_ready
    from shutil import copy2
    from tqdm import tqdm
    from vasp_sop.defect import is_valid_defect_dir

    dirs = [child for child in defect_root.iterdir()
            if child.is_dir() and (child.name == "perfect" or is_valid_defect_dir(child))]
    if not dirs:
        return

    # Phase 1: ensure first directory has full inputs (subprocess call)
    first = dirs[0]
    if not input_ready(first):
        try:
            prepare_inputs(first, config,
                           kspacing=0.1, task_type="defect",
                           extra_uis="SIGMA 0.02 LORBIT 11")
        except Exception:
            pass

    # Phase 2: copy shared files to remaining directories
    shared = ["INCAR", "POTCAR", "KPOINTS"]
    skip = []
    for d in tqdm(dirs[1:], desc="VASP inputs", unit=" dir"):
        if input_ready(d):
            continue
        for f in shared:
            src = first / f
            if src.is_file():
                copy2(str(src), str(d / f))
        # Verify: POSCAR is per-directory, so input_ready needs it
        if not input_ready(d):
            try:
                prepare_inputs(d, config,
                               kspacing=0.1, task_type="defect",
                               extra_uis="SIGMA 0.02 LORBIT 11")
            except Exception:
                pass


def construct_complex_defects(defect_root: Path, config: PipelineConfig) -> None:
    """Build combined defects via ``pydefect.complex.ComplexDefectMaker``.

    Delegates to the library for geometry enumeration, composition
    assignment, structure generation, and deduplication.
    """
    complex_flag = defect_root / "complex_flag"
    if complex_flag.is_file():
        logger.info("Complex defects already constructed, skipping.")
        return

    sc_info = defect_root / "supercell_info.json"
    if not sc_info.is_file():
        logger.warning("supercell_info.json not found at %s, skipping complex defects.", sc_info)
        return

    from pydefect.complex import ComplexDefectMaker

    maker = ComplexDefectMaker.from_supercell_info(
        str(sc_info),
        dopants=config.dopant_elements or None,
        max_distance=config.remote_cutoff,
    )

    for order in range(2, config.complex_defect_order + 1):
        logger.info("Generating complex defects of order %d", order)
        geoms = maker.make_all_n_body(n=order)
        entries = maker.generate_entries(order, dopants=config.dopant_elements or None)
        maker.write(entries, str(defect_root), merge=True)


def _config_fingerprint(config: PipelineConfig) -> str:
    """Return a short hash of config fields that affect the build."""
    relevant = {
        "supercell_tool": config.supercell_tool,
        "supercell_min_distance": config.supercell_min_distance,
        "supercell_min_atoms": config.supercell_min_atoms,
        "supercell_max_atoms": config.supercell_max_atoms,
        "interstitial": config.interstitial,
        "interstitial_indices": config.interstitial_indices,
        "dopant_elements": config.dopant_elements,
        "complex_defect_order": config.complex_defect_order,
        "remote_cutoff": config.remote_cutoff,
        "formula": config.formula,
    }
    raw = json.dumps(relevant, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _check_rebuild(defect_root: Path, config: PipelineConfig) -> None:
    """Compare config fingerprint against last build; clear flags on mismatch."""
    fp_path = defect_root / ".build_fingerprint"
    if not fp_path.is_file():
        return  # first build, nothing to compare
    old_fp = fp_path.read_text().strip()
    new_fp = _config_fingerprint(config)
    if old_fp == new_fp:
        return
    logger.info(
        "Config fingerprint changed (%s → %s), clearing build flags.",
        old_fp, new_fp,
    )
    for name in ("supercell_info.json", "defect_in.yaml",
                  "defect_generate_flag", "complex_flag"):
        p = defect_root / name
        if p.is_file():
            p.unlink()
            logger.info("  Cleared %s", name)


def _write_fingerprint(defect_root: Path, config: PipelineConfig) -> None:
    """Persist the current config fingerprint so next build can detect changes."""
    fp = _config_fingerprint(config)
    (defect_root / ".build_fingerprint").write_text(fp + "\n")


def _fix_defect_nelect(defect_root: Path) -> None:
    """Per‑defect NELECT patch (Σ N_i·ZVAL_i − q).

    Must run AFTER _generate_vasp_inputs, because that function copies
    the first directory's INCAR (with host‑centric NELECT) to every
    defect directory.  This post‑process step fixes each directory to
    the correct NELECT for its specific defect and charge state.
    """
    from vasp_sop.vasp.io import read_incar, patch_incar
    import re

    # ZVAL per POTCAR variant — must match plan.yaml `pp:` order.
    # Cs_sv:9, Pb_d:4, Br:7, Bi_d:5  (standard PAW_PBE suffixes)
    ZVAL: dict[str, float] = {"Cs": 9.0, "Pb": 4.0, "Br": 7.0, "Bi": 5.0}

    # Regex to extract q from directory name  e.g. Bi_Pb1_-1 → -1
    Q_RE = re.compile(r"_(-?\d+)$")

    for wd in sorted(defect_root.iterdir()):
        if not wd.is_dir():
            continue

        # ── Parse species counts from POSCAR ────────────────────────
        poscar = wd / "POSCAR"
        if not poscar.is_file():
            continue
        text = poscar.read_text()
        lines = text.splitlines()

        # Locate species line (first line of all‑caps symbols)
        species_line = None
        for i, ln in enumerate(lines[:8]):
            toks = ln.split()
            if toks and all(re.match(r"^[A-Z][a-z]?$", t) for t in toks):
                species_line = toks
                species_idx = i
                break
        if species_line is None:
            continue
        # Next line: integer counts
        counts_line = lines[species_idx + 1].split()
        if len(counts_line) != len(species_line):
            continue
        if not all(c.isdigit() for c in counts_line):
            continue
        counts = dict(zip(species_line, map(int, counts_line)))

        # ── Calculate base NELECT = Σ N_i·ZVAL_i ──────────────────
        base = sum(counts.get(el, 0) * ZVAL.get(el, 0) for el in counts)

        # ── Determine q and target NELECT ─────────────────────────--
        name = wd.name
        if name == "perfect":
            target = base  # no charge adjustment
        else:
            m = Q_RE.search(name)
            if not m:
                continue
            q = int(m.group(1))
            target = base - q

        # ── Idempotent patch ──────────────────────────────────────-
        patch_incar(wd, NELECT=target)
