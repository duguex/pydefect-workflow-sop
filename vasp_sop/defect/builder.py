
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

    # Post-build charge verification: every defect INCAR must carry the
    # correct NELECT (or none for neutral dirs).  A violation means the
    # inputs are broken and must not be submitted (charge errors are
    # silent killers — they ran to "completion" with wrong electron
    # counts in the 2025 tree).
    problems = verify_nelect(defect_root, config)
    if problems:
        for p in problems[:20]:
            logger.error("NELECT verify: %s", p)
        raise RuntimeError(
            f"NELECT verification failed for {len(problems)} "
            f"directory/directories (first: {problems[0] if problems else ''})"
        )

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
    """Generate ``defect_in.yaml`` via ``pydefect ds`` (with dopants).

    Charge-state prediction always goes through pydefect. doped is used
    only for supercell construction (``supercell.tool: doped``), never
    for defect/charge enumeration.
    """
    defect_in = defect_root / "defect_in.yaml"
    if defect_in.is_file():
        logger.info("defect_in.yaml already exists, skipping defect list generation.")
        return

    _generate_defect_list_pydefect(defect_root, config)

    if defect_in.is_file():
        with open(defect_in) as f:
            data = yaml.safe_load(f)
        logger.info("Defect list (method=pydefect):")
        for defect, valence in (data or {}).items():
            logger.info("  %s: %s", defect, valence)


def _generate_defect_list_pydefect(defect_root: Path, config: PipelineConfig) -> None:
    """Run ``pydefect ds`` to produce ``defect_in.yaml``."""
    _pdad.defect_list(defect_root, config.dopant_elements)


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

    Each defect directory gets its inputs from vise's Python API with its
    own charge (NELECT = ΣNᵢZVALᵢ − q computed by vise from POTCAR).
    POSCARs are per-directory already; INCAR/POTCAR/KPOINTS are generated
    per directory so species and charge are respected (no cross-dir copy
    of the host INCAR — that is what broke NELECT before).
    """
    from vasp_sop.vasp.io import prepare_inputs, input_ready
    from tqdm import tqdm
    from vasp_sop.defect import is_valid_defect_dir
    import re

    Q_RE = re.compile(r"_(-?\d+)$")
    dirs = [child for child in defect_root.iterdir()
            if child.is_dir() and (child.name == "perfect" or is_valid_defect_dir(child))]
    if not dirs:
        return

    for d in tqdm(dirs, desc="VASP inputs", unit=" dir"):
        if input_ready(d):
            continue
        q = 0.0
        if d.name != "perfect":
            m = Q_RE.search(d.name)
            if m:
                q = float(m.group(1))
        try:
            prepare_inputs(d, config,
                           kspacing=0.1, task_type="defect",
                           extra_uis="SIGMA 0.02 LORBIT 11",
                           charge=q)
        except Exception as exc:
            logger.warning("%s: input generation failed: %s", d.name, exc)


def verify_nelect(defect_root: Path, config: PipelineConfig) -> list[str]:
    """Verify NELECT in every defect INCAR against POSCAR×ZVAL − q.

    Returns a list of problem descriptions (empty when all correct).
    Neutral dirs (q=0) must have NO NELECT line (VASP default); charged
    dirs must carry exactly ΣNᵢZVALᵢ − q.  ZVALs come from each dir's
    own POTCAR, falling back to plan.yaml pp variants.
    """
    from vasp_sop.vasp.io import _pick_psp_variant
    from vasp_sop.defect import is_valid_defect_dir
    import re

    _PSP = Path("/mnt/shared/VASP_POT/POT_GGA_PAW_PBE")
    problems: list[str] = []
    Q_RE = re.compile(r"_(-?\d+)$")
    pp_zval: dict[str, float] = {}
    for v in config.potcar_overrides:
        cand = _pick_psp_variant(v.split("_")[0], psp=_PSP, encut=None)
        if cand is not None:
            zv = _potcar_zvals(cand / "POTCAR")
            if zv:
                pp_zval[v.split("_")[0]] = zv.get(cand.name, zv.get(v.split("_")[0]))

    def _element_fallback(el: str) -> float | None:
        """Element-name fallback: simple name first, then any non-GW variant."""
        simple = _PSP / el / "POTCAR"
        if simple.is_file():
            zv = _potcar_zvals(simple)
            return zv.get(el)
        for cand in sorted(_PSP.iterdir()):
            if cand.is_dir() and cand.name.split("_")[0] == el and "_GW" not in cand.name:
                zv = _potcar_zvals(cand / "POTCAR")
                return zv.get(el) or zv.get(cand.name.split("_")[0])
        return None

    for wd in sorted(defect_root.iterdir()):
        if not wd.is_dir():
            continue
        if not (wd / "POSCAR").is_file():
            continue
        comp = _poscar_composition(wd)
        if comp is None:
            problems.append(f"{wd.name}: cannot parse POSCAR composition")
            continue
        zv: dict[str, float] = {}
        if (wd / "POTCAR").is_file():
            zv = {k.split("_")[0]: v for k, v in _potcar_zvals(wd / "POTCAR").items()}
        for el in comp:
            if el not in zv:
                if el in pp_zval and pp_zval[el]:
                    zv[el] = pp_zval[el]
                else:
                    z = _element_fallback(el)
                    if z:
                        zv[el] = z
        if not all(el in zv for el in comp):
            problems.append(
                f"{wd.name}: missing ZVAL for {[e for e in comp if e not in zv]}"
            )
            continue
        base = sum(n * zv[el] for el, n in comp.items())
        m = Q_RE.search(wd.name)
        q = int(m.group(1)) if m else 0
        correct = base - q
        incar = wd / "INCAR"
        if not incar.is_file():
            problems.append(f"{wd.name}: no INCAR")
            continue
        m2 = re.search(r"NELECT\s*=\s*(-?\d+\.?\d*)", incar.read_text())
        actual = float(m2.group(1)) if m2 else None
        if q == 0:
            if actual is not None and actual != correct:
                problems.append(
                    f"{wd.name}: neutral but NELECT={actual:.0f} "
                    f"(should be absent, default {correct:.0f})"
                )
        elif actual is None:
            problems.append(
                f"{wd.name}: charged q={q} but NELECT missing "
                f"(should be {correct:.0f})"
            )
        elif actual != correct:
            problems.append(
                f"{wd.name}: NELECT={actual:.0f} != {correct:.0f} (q={q})"
            )
    return problems


def _potcar_zvals(potcar: Path) -> dict[str, float]:
    """Element → ZVAL from POTCAR header blocks, in file order.

    Each PAW_PBE block opens with a TITEL line naming its element
    (variant suffixes stripped); the ZVAL line that follows belongs to
    that element.  Elements without a ZVAL line are omitted.
    """
    import re as _re

    zvals: dict[str, float] = {}
    try:
        text = potcar.read_text()
    except OSError:
        return zvals
    current: str | None = None
    for line in text.splitlines():
        m_t = _re.search(r"TITEL\s*=\s*PAW_PBE\s+(\S+)", line)
        if m_t:
            current = m_t.group(1).split("_")[0]
            continue
        m_z = _re.search(r"ZVAL\s*=\s*([\d.]+)", line)
        if m_z and current is not None and current not in zvals:
            zvals[current] = float(m_z.group(1))
    return zvals


_MAGNETIC_ELEMENTS = {"Fe", "Co", "Ni", "Gd", "Mn", "Cr", "Eu", "Ce"}


def verify_inputs(defect_root: Path, config: PipelineConfig) -> list[str]:
    """Verify VASP input completeness/consistency for every defect dir.

    Checks (errors are submission-blocking; warnings are advisory):
      [ERR] missing INCAR/POSCAR/POTCAR/KPOINTS
      [ERR] POSCAR unparsable (species line / counts / coordinate count)
      [ERR] POTCAR species set/order differs from POSCAR
      [ERR] INCAR missing relaxation tags (NSW/IBRION/EDIFFG) for relax tasks
      [WARN] ENCUT below 1.3×ENMAX (VASP convention) for any POTCAR block
      [WARN] magnetic element present (Fe/Gd/…) but ISPIN not set
      [ERR] KPOINTS missing or unparsable
    Returns a list of "name: [ERR|WARN] message" strings (empty if clean).
    """
    from vasp_sop.defect import is_valid_defect_dir
    import re

    problems: list[str] = []
    for wd in sorted(defect_root.iterdir()):
        if not wd.is_dir():
            continue
        # Only defect calculation dirs (perfect + named defects); skip
        # symmetry/test subdirs that are not part of the calc set.
        if wd.name != "perfect" and not is_valid_defect_dir(wd):
            continue
        name = wd.name
        # ── File completeness ───────────────────────────────────────
        for f in ("POSCAR", "INCAR", "POTCAR", "KPOINTS"):
            if not (wd / f).is_file():
                problems.append(f"{name}: [ERR] missing {f}")
        if not (wd / "POSCAR").is_file():
            continue

        # ── POSCAR ─────────────────────────────────────────────────
        comp = _poscar_composition(wd)
        if comp is None:
            problems.append(f"{name}: [ERR] POSCAR species/counts unparsable")
            continue
        n_atoms = sum(comp.values())
        lines = (wd / "POSCAR").read_text().splitlines()
        coords = [ln for ln in lines[8:] if ln.strip()]
        # POSCAR may carry one velocity row per atom after the coordinates
        # (VASP MD/velocity output; zero rows = zero velocities — legal).
        # Valid layouts: N coords, or N coords + N velocities.
        if len(coords) < n_atoms or len(coords) > 2 * n_atoms:
            problems.append(
                f"{name}: [ERR] POSCAR has {len(coords)} coordinate/velocity "
                f"rows for {n_atoms} atoms (expected {n_atoms} or {2 * n_atoms})"
            )

        # ── POTCAR vs POSCAR ────────────────────────────────────────
        if (wd / "POTCAR").is_file():
            pot_species = [k for k in _potcar_zvals(wd / "POTCAR")]
            pos_species = list(comp.keys())
            if pot_species != pos_species:
                problems.append(
                    f"{name}: [ERR] POTCAR species {pot_species} != "
                    f"POSCAR {pos_species} (order matters)"
                )

        # ── INCAR ───────────────────────────────────────────────────
        if (wd / "INCAR").is_file():
            inc = (wd / "INCAR").read_text()
            for tag in ("NSW", "IBRION", "EDIFFG"):
                if not re.search(rf"^\s*{tag}\s*=", inc, re.M):
                    problems.append(f"{name}: [ERR] INCAR missing {tag}")
            m_encut = re.search(r"^\s*ENCUT\s*=\s*([\d.]+)", inc, re.M)
            if m_encut and (wd / "POTCAR").is_file():
                encut = float(m_encut.group(1))
                enmax = _potcar_enmax_max(wd / "POTCAR")
                if enmax and encut < enmax - 1e-6:
                    problems.append(
                        f"{name}: [WARN] ENCUT={encut:.1f} below max ENMAX "
                        f"={enmax:.1f} (VASP hard floor; 1.3×ENMAX is the "
                        f"conservative convention)"
                    )
            has_mag = any(el in _MAGNETIC_ELEMENTS for el in comp)
            if has_mag and not re.search(r"^\s*ISPIN\s*=\s*2", inc, re.M):
                problems.append(f"{name}: [WARN] magnetic element(s) but ISPIN≠2")

        # ── KPOINTS ────────────────────────────────────────────────
        if (wd / "KPOINTS").is_file():
            kp = (wd / "KPOINTS").read_text().splitlines()
            if not kp or not kp[0].strip():
                problems.append(f"{name}: [ERR] KPOINTS empty")
    return problems


def _potcar_enmax_max(potcar: Path) -> float | None:
    """Maximum ENMAX across POTCAR blocks (the binding cutoff)."""
    import re

    try:
        text = potcar.read_text()
    except OSError:
        return None
    vals = [float(m.group(1)) for m in re.finditer(r"ENMAX\s*=\s*([\d.]+)", text)]
    return max(vals) if vals else None


def _poscar_composition(wd: Path) -> dict[str, int] | None:
    import re

    poscar = wd / "POSCAR"
    if not poscar.is_file():
        return None
    lines = poscar.read_text().splitlines()
    sp = None
    for i, ln in enumerate(lines[:8]):
        toks = ln.split()
        if toks and all(re.match(r"^[A-Z][a-z]?$", t) for t in toks):
            sp = i
            break
    if sp is None:
        return None
    try:
        return dict(zip(lines[sp].split(), map(int, lines[sp + 1].split())))
    except (IndexError, ValueError):
        return None


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


def _potcar_zvals(potcar: Path) -> dict[str, float]:
    """Element → ZVAL from the POTCAR header blocks, in file order.

    Walks the file sequentially: each PAW_PBE block opens with a TITEL
    line naming its element; the ZVAL line that follows belongs to that
    element.  Elements without a ZVAL line are omitted.
    """
    import re as _re

    zvals: dict[str, float] = {}
    try:
        text = potcar.read_text()
    except OSError:
        return zvals
    current: str | None = None
    for line in text.splitlines():
        m_t = _re.search(r"TITEL\s*=\s*PAW_PBE\s+(\S+)", line)
        if m_t:
            # POTCAR TITEL carries the variant name (Zr_sv, Ba_sv, Gd_3);
            # key by the base element symbol so POSCAR species match.
            current = m_t.group(1).split("_")[0]
            continue
        m_z = _re.search(r"ZVAL\s*=\s*([\d.]+)", line)
        if m_z and current is not None and current not in zvals:
            zvals[current] = float(m_z.group(1))
    return zvals


def _fix_defect_nelect(defect_root: Path) -> None:
    """Per‑defect NELECT patch (Σ N_i·ZVAL_i − q).

    Must run AFTER _generate_vasp_inputs, because that function copies
    the first directory's INCAR (with host‑centric NELECT) to every
    defect directory.  This post‑process step fixes each directory to
    the correct NELECT for its specific defect and charge state.

    ZVALs come from the directory's own POTCAR header (each PAW_PBE
    block carries its ZVAL) — never hardcoded, so any element works.
    """
    from vasp_sop.vasp.io import read_incar, patch_incar
    import re

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

        # ── ZVAL per element from this dir's POTCAR ────────────────
        potcar = wd / "POTCAR"
        zval_by_element = _potcar_zvals(potcar) if potcar.is_file() else {}

        # ── Calculate base NELECT = Σ N_i·ZVAL_i ──────────────────
        base = 0.0
        for el, n in counts.items():
            zv = zval_by_element.get(el)
            if zv is None:
                logger.warning(
                    "%s: no ZVAL for %s in POTCAR — NELECT may be wrong",
                    wd.name, el,
                )
                zv = 0.0
            base += n * zv

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
        patch_incar(wd, NELECT=int(round(target)))
