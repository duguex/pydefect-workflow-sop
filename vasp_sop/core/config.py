"""Configuration loading, validation, and generation for the pipeline.

.. code:: yaml

   project:
     formula: GaN
     dopant_elements: [Mg]
     poscar_src: "MP mp-830"     # 推理结果，含竞争相列表
   parameters:
     functional: pbesol
     encut: null                  # 自动检测
     hubbard_u: false             # retired (ADR 0012): DFT+U always on, vise auto-adapts by element
     pp: []                       # auto: 从 POTCAR 目录排序
   supercell: {min_atoms: 200, max_atoms: 600}
   defects:
     interstitials: false
     complex_n: 1
     max_distance: 5.0
     interstitial_indices: []  # 0-based indices into the dos_extrema candidate list
   corrections:
     H2: 0.358
     N2: 0.722
     O2: 1.374
     F2: 0.924
     Cl2: 1.228
   correction_policy: custom_molecular_reference
   energy_adjust_step: 0.01
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ── Default plan (clean data, no comments) ──────────────────────────────

DEFAULT_PLAN: dict = {
    "project": {
        "formula": "",
        "dopant_elements": [],
        "poscar_src": "",
    },
    "parameters": {
        "functional": "pbesol",
        "encut": None,
        "hubbard_u": False,
        "soc": False,
        "stage2_soc": False,   # ADR 0014: two-phase SOC (non-SOC first, SOC supplement)
        "pp": [],
    },
    "supercell": {"tool": "doped", "min_distance": 10.0},
    "defects": {
        "interstitials": False,
        "complex_n": 1,
        "max_distance": 5.0,
    },
    # Custom molecular-reference shifts: 2 × |MP2020 element coefficient|.
    "corrections": {
        "H2": 0.358,
        "N2": 0.722,
        "O2": 1.374,
        "F2": 0.924,
        "Cl2": 1.228,
    },
    "correction_policy": "custom_molecular_reference",
    "energy_adjust_step": 0.01,
}


@dataclass
class PipelineConfig:
    """Complete pipeline configuration (flat view for internal use).

    Loaded from the plan dict produced by :func:`generate_config`.
    """

    formula: str = ""
    dopant_elements: list[str] = field(default_factory=list)
    poscar_src: str = ""

    # Scope of the system's pipeline: "defects" (default — full defect
    # workflow) or "chemical-environment" (competing phases + chemical
    # potentials only; no unit-cell or defect calculations).
    scope: str = "defects"

    interstitial: bool = False
    interstitial_indices: list[int] = field(default_factory=list)
    complex_defect_order: int = 1
    remote_cutoff: float = 5.0

    functional: str = "pbesol"
    encut: Optional[float] = None
    hubbard_u: bool = False
    soc: bool = False
    stage2_soc: bool = False  # ADR 0014: auto SOC supplement
    potcar_overrides: list[str] = field(default_factory=list)

    supercell_tool: str = "doped"
    supercell_min_distance: float = 10.0
    supercell_min_atoms: int = 200
    supercell_max_atoms: int = 600

    molecule_corrections: dict[str, float] = field(
        default_factory=lambda: {
            "H2": 0.358,
            "N2": 0.722,
            "O2": 1.374,
            "F2": 0.924,
            "Cl2": 1.228,
        }
    )
    correction_policy: str = "custom_molecular_reference"

    energy_adjust_step: float = 0.01
    custom_poscar_path: Optional[Path] = None

    # Junk dir filter opt-in for defect_new/ — #100
    include_defect_new: bool = False

    # runtime
    root: Path = Path(".")

    def __post_init__(self) -> None:
        self.formula = self.formula.strip()
        if not self.formula:
            raise ValueError("formula must be non-empty, e.g. 'GaN'.")
        if self.scope not in ("defects", "chemical-environment"):
            raise ValueError(
                f"scope must be 'defects' or 'chemical-environment', got {self.scope!r}."
            )
        if self.correction_policy != "custom_molecular_reference":
            raise ValueError(
                "correction_policy must be 'custom_molecular_reference'; "
                "MP2020 anion corrections are not implemented by this pipeline."
            )
        if self.supercell_min_atoms < 1:
            raise ValueError("supercell_min_atoms must be >= 1.")
        if self.supercell_tool not in ("pydefect", "doped"):
            raise ValueError(
                f"supercell_tool must be 'pydefect' or 'doped', got {self.supercell_tool!r}."
            )
        if self.supercell_max_atoms < self.supercell_min_atoms:
            raise ValueError(
                f"supercell_max_atoms ({self.supercell_max_atoms}) must be >= "
                f"supercell_min_atoms ({self.supercell_min_atoms})."
            )
        if self.complex_defect_order < 1:
            raise ValueError("complex_defect_order must be >= 1.")
        if self.remote_cutoff <= 0:
            raise ValueError("remote_cutoff must be positive.")
        if self.energy_adjust_step <= 0:
            raise ValueError("energy_adjust_step must be positive.")

    # ═══════════════════════════════════════════════════════════════
    # Plan ↔ Config
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def from_yaml(cls, path: Path, root: Path = Path(".")) -> PipelineConfig:
        """Load plan from a YAML file.

        Handles both nested (new) and flat (legacy) formats.
        """
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path} does not contain a valid mapping.")
        # ── Backward compat: flat → nested ───────────────────────────
        if "formula" in data:
            data = _flat_to_nested(data)
        return cls.from_plan(data, root=root)

    @classmethod
    def from_plan(cls, plan: dict, root: Path = Path(".")) -> PipelineConfig:
        """Reconstruct from the nested plan dict."""
        p = plan.get("project", {})
        params = plan.get("parameters", {})
        sc = plan.get("supercell", {})
        d = plan.get("defects", {})
        corr = plan.get("corrections", {})
        # Tolerate a toplevel stage2_soc (written that way by the ADR 0014
        # rollout, 2026-08-10) — silently ignoring it silently disabled the
        # two-phase SOC supplement for the whole 2026 batch.
        stage2_soc = params.get("stage2_soc", False)
        if not stage2_soc and plan.get("stage2_soc", False):
            import logging

            logging.getLogger(__name__).warning(
                "toplevel stage2_soc in plan (root=%s): treating as true; "
                "move it under 'parameters:' for schema compliance",
                root,
            )
            stage2_soc = True
        return cls(
            root=root,
            formula=p.get("formula", ""),
            dopant_elements=p.get("dopant_elements", []),
            poscar_src=p.get("poscar_src", ""),
            scope=p.get("scope", "defects"),
            functional=params.get("functional", "pbesol"),
            encut=params.get("encut"),
            soc=params.get("soc", False),
            stage2_soc=stage2_soc,
            hubbard_u=params.get("hubbard_u", False),
            potcar_overrides=params.get("pp", []),
            supercell_min_atoms=sc.get("min_atoms", 200),
            supercell_max_atoms=sc.get("max_atoms", 600),
            supercell_tool=sc.get("tool", "doped"),
            supercell_min_distance=sc.get("min_distance", 10.0),
            interstitial=d.get("interstitials", False),
            interstitial_indices=list(d.get("interstitial_indices", [])),
            complex_defect_order=d.get("complex_n", 1),
            remote_cutoff=d.get("max_distance", 5.0),
            include_defect_new=d.get("include_defect_new", False),
            molecule_corrections={
                "H2": corr.get("H2", 0.358),
                "N2": corr.get("N2", 0.722),
                "O2": corr.get("O2", 1.374),
                "F2": corr.get("F2", 0.924),
                "Cl2": corr.get("Cl2", 1.228),
            },
            correction_policy=plan.get(
                "correction_policy", "custom_molecular_reference"
            ),
            energy_adjust_step=plan.get("energy_adjust_step", 0.01),
        )

    def to_plan(self) -> dict:
        """Export as nested plan dict (data only, no comments)."""
        return {
            "project": {
                "formula": self.formula,
                "dopant_elements": list(self.dopant_elements),
                "poscar_src": self.poscar_src,
            },
            "parameters": {
                "functional": self.functional,
                "encut": self.encut,
                "hubbard_u": self.hubbard_u,
                "soc": self.soc,
                "stage2_soc": self.stage2_soc,
                "pp": list(self.potcar_overrides),
            },
            "supercell": {
                "tool": self.supercell_tool,
                # Emit all three keys so that round-trip never silently drops
                # either the atom-count bounds (used by pydefect) or the
                # image-distance bound (used by doped). Downstream code reads
                # only the key it cares about; the other is harmless.
                "min_atoms": self.supercell_min_atoms,
                "max_atoms": self.supercell_max_atoms,
                "min_distance": self.supercell_min_distance,
            },
            "defects": {
                "interstitials": self.interstitial,
                "interstitial_indices": list(self.interstitial_indices),
                "complex_n": self.complex_defect_order,
                "max_distance": self.remote_cutoff,
                "include_defect_new": self.include_defect_new,
            },
            "corrections": dict(self.molecule_corrections),
            "correction_policy": self.correction_policy,
            "energy_adjust_step": self.energy_adjust_step,
        }

    def to_yaml(self, path: Path) -> None:
        """Dump clean plan (no comments) to *path*."""
        with open(path, "w") as f:
            yaml.dump(
                self.to_plan(),
                f,
                default_flow_style=None,
                sort_keys=False,
                allow_unicode=True,
            )

    @classmethod
    def from_legacy_json(cls, path: Path, root: Path = Path(".")) -> PipelineConfig:
        """Migrate from the legacy ``info.json`` format."""
        with open(path) as f:
            data = json.load(f)
        # ``iindex`` in the legacy format is a list of strings (e.g. "0", "1")
        # — convert each to int so downstream code can join/iterate uniformly.
        raw_iindex = data.get("iindex", [])
        try:
            interstitial_indices = [int(x) for x in raw_iindex]
        except (TypeError, ValueError):
            interstitial_indices = []
        return cls(
            formula=data.get("obj", ""),
            dopant_elements=data.get("dopant_element", []),
            interstitial=data.get("interstitial", False),
            interstitial_indices=interstitial_indices,
            complex_defect_order=data.get("complex_defect", 1),
            remote_cutoff=data.get("remote", 5.0),
            potcar_overrides=data.get("pp", []),
            root=root,
        )


# ══════════════════════════════════════════════════════════════════════════
# config generation (inference + dynamic-comment YAML)
# ══════════════════════════════════════════════════════════════════════════

PLAN_FILENAME = "plan.yaml"


def generate_config(
    project_dir: str | Path,
    formula: str,
    dopant_elements: list[str] | None = None,
    poscar_src: str | None = None,
    functional: str = "pbesol",
    **kwargs,
) -> Path:
    """Infer defaults, write plan.yaml with dynamic comments.

    Steps:
        ① Run ``pydefect_vasp mp`` (same command the CPD stage uses)
           — single MP query, downloads all competing phases.
        ② Parse downloaded directory names and POSCARs for YAML annotations.
        ③ Record the validated ``mp_state.json`` MP manifest.
        ④ Detect ENCUT from POTCAR, DFT+U from element set, POTCAR variants.
        ⑤ Apply kwargs overrides, write plan.yaml.
    """
    root = Path(project_dir)
    plan = _deep_copy(DEFAULT_PLAN)
    plan["project"]["formula"] = formula
    plan["project"]["dopant_elements"] = list(dopant_elements or [])
    plan["parameters"]["functional"] = functional

    from vasp_sop.materials import (
        fetch_candidate_phases,
        get_intrinsic_elements,
        list_potcar_variants,
        detect_encut,
        needs_hubbard_u,
    )

    # ① Load competing phases from Materials Project
    cpd_root = root / "cpd"
    cpd_root.mkdir(parents=True, exist_ok=True)

    elements = get_intrinsic_elements(formula) + (dopant_elements or [])
    fetch_candidate_phases(elements, cpd_root, use_cache=True)

    # ② Parse phase info for YAML annotations and POSCAR for inference
    phases: list[dict] = []
    unitcell_poscar = root / "unitcell" / "structure_opt" / "POSCAR"
    target_found = False
    target_dir: Path | None = None

    from pymatgen.core import Structure, Composition as PmgComp

    target_comp = PmgComp(formula)

    for child in sorted(cpd_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        poscar_file = child / "POSCAR"
        if not poscar_file.exists():
            continue

        # Extract mp-ID from dirname (GaN_mp-804 → 804, or GaN_mp-Ga_mp-xxx → ?)
        mpid = None
        if "_mp-" in name:
            mpid = name.split("_mp-", 1)[1]

        try:
            s = Structure.from_file(str(poscar_file))
            spg = s.get_space_group_info()[0]
            comp = s.composition.reduced_formula
            is_target = PmgComp(comp) == target_comp

            phases.append(
                {
                    "mpid": mpid or "?",
                    "spg": spg,
                    "formula": comp,
                    "a": round(s.lattice.a, 3),
                    "b": round(s.lattice.b, 3),
                    "c": round(s.lattice.c, 3),
                    "is_target": is_target,
                }
            )

            if is_target and not target_found:
                target_found = True
                target_dir = child.resolve()
                # Copy POSCAR to unitcell/structure_opt/ for inference
                unitcell_poscar.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(poscar_file), str(unitcell_poscar))
                plan["project"]["poscar_src"] = (
                    f"MP mp-{mpid}" if mpid else f"local: {name}"
                )
        except Exception:
            continue

    phases.sort(key=lambda p: (0 if p["is_target"] else 1, p.get("mpid", "")))

    # ②b Fallback: if MP didn't return the exact target formula, download it directly
    if not target_found:
        try:
            from mp_api.client import MPRester as _MPRester
            import os as _os

            _key = _os.environ.get("MP_API_KEY") or _os.environ.get("PMG_MAPI_KEY")
            if _key:
                logger.info(
                    "Target phase %s not in MP element query, querying exact formula ...",
                    formula,
                )
                with _MPRester(_key) as _mpr:
                    _docs = _mpr.materials.summary.search(
                        formula=formula,
                        fields=["material_id", "formula_pretty"],
                    )
                    if _docs:
                        _mpid = _docs[0].material_id
                        _target_dir = cpd_root / f"{formula}_{_mpid}"
                        if not _target_dir.is_dir():
                            _target_dir.mkdir(parents=True)
                            _struct = _mpr.get_structure_by_material_id(_mpid)
                            _struct.to(
                                fmt="poscar", filename=str(_target_dir / "POSCAR")
                            )
                        target_found = True
                        target_dir = _target_dir.resolve()
                        unitcell_poscar.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(_target_dir / "POSCAR"), str(unitcell_poscar))
                        plan["project"]["poscar_src"] = f"MP {_mpid}"
                        logger.info("Downloaded target phase %s (%s)", formula, _mpid)
        except Exception as _exc:
            logger.warning("Failed to download target phase %s: %s", formula, _exc)

    # ③ ENCUT (from POTCAR in target dir if available)
    if target_dir:
        potcar = target_dir / "POTCAR"
        if potcar.exists():
            inferred = detect_encut(potcar)
            if inferred:
                plan["parameters"]["encut"] = inferred

    # ④ DFT+U
    if unitcell_poscar.exists():
        plan["parameters"]["hubbard_u"] = needs_hubbard_u(unitcell_poscar)

    # ⑤ POTCAR variants
    variants = list_potcar_variants(formula, dopant_elements or [])
    if variants:
        plan["_potcar_variants"] = variants
        plan["parameters"]["pp"] = [
            variants[el][0] if isinstance(variants[el], list) else variants[el]
            for el in sorted(variants)
        ]

    # ⑥ kwargs overrides
    for k, v in kwargs.items():
        _set_nested(plan, k, v)

    # ⑦ Write plan.yaml with phase annotations
    path = _write_plan_yaml(root, plan, phases)
    return path


# ══════════════════════════════════════════════════════════════════════════
# YAML writer with dynamic comments
# ══════════════════════════════════════════════════════════════════════════


def _write_plan_yaml(root: Path, plan: dict, phases: list[dict]) -> Path:
    """Dump *plan* to YAML, injecting dynamic comments from inference."""
    path = root / PLAN_FILENAME

    potcar_variants = plan.pop("_potcar_variants", {})

    yaml_str = yaml.dump(
        plan, default_flow_style=None, sort_keys=False, allow_unicode=True
    )
    lines = list(yaml_str.splitlines(keepends=True))

    # Find insertion points
    poscar_line: int | None = None
    pp_line: int | None = None
    indent = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("poscar_src:"):
            poscar_line = i
            indent = line[: len(line) - len(line.lstrip())]
        if line.strip().startswith("pp:"):
            pp_line = i
    if phases and poscar_line is not None:
        phase_comment = [f"{indent}# Available phases from MP:\n"]
        for i, p in enumerate(phases):
            default = " (default)" if i == 0 else ""
            phase_comment.append(
                f"{indent}# - {p['formula']} (mp-{p['mpid']}): "
                f"{p['spg']}, "
                f"a={p['a']:.3f} b={p['b']:.3f} c={p['c']:.3f}{default}\n"
            )
        phase_comment.append(
            f"{indent}# To use a different phase, change poscar_src:\n"
        )
        phase_comment.append(f'{indent}#   poscar_src: "MP mp-xxx"\n')
        phase_comment.append(f'{indent}#   poscar_src: "./path/to/POSCAR"\n')
        for c in reversed(phase_comment):
            lines.insert(poscar_line + 1, c)
        if pp_line is not None:
            pp_line += len(phase_comment)

    # Insert POTCAR variant comments after pp
    if potcar_variants and pp_line is not None:
        pp_indent = lines[pp_line][: len(lines[pp_line]) - len(lines[pp_line].lstrip())]
        potcar_comment = [f"{pp_indent}# Available POTCAR variants:\n"]
        for el, variants in potcar_variants.items():
            potcar_comment.append(f"{pp_indent}#   {el}: {', '.join(variants)}\n")
        for c in reversed(potcar_comment):
            lines.insert(pp_line + 1, c)

    with open(path, "w") as f:
        f.write("".join(lines))
    return path


# ══════════════════════════════════════════════════════════════════════════
# Inference steps
# ══════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


def _deep_copy(d: dict) -> dict:
    import copy

    return copy.deepcopy(d)


def _set_nested(d: dict, key: str, value) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _flat_to_nested(flat: dict) -> dict:
    """Convert a legacy flat config to the nested plan format."""
    corrections = flat.get("molecule_corrections", {})
    if not isinstance(corrections, dict):
        corrections = {}
    return {
        "project": {
            "formula": flat.get("formula", ""),
            "dopant_elements": flat.get("dopant_elements", []),
            "poscar_src": "",
        },
        "parameters": {
            "functional": flat.get("functional", "pbesol"),
            "encut": None,
            "soc": flat.get("soc", False),
            "hubbard_u": False,
            "pp": flat.get("potcar_overrides", []),
        },
        "supercell": {
            "min_atoms": flat.get("supercell_min_atoms", 200),
            "max_atoms": flat.get("supercell_max_atoms", 600),
        },
        "defects": {
            "interstitials": flat.get("interstitial", False),
            "interstitial_indices": flat.get("interstitial_indices", []),
            "complex_n": flat.get("complex_defect_order", 1),
            "max_distance": flat.get("remote_cutoff", 5.0),
        },
        "corrections": {
            "H2": corrections.get("H2", 0.358),
            "N2": corrections.get("N2", 0.722),
            "O2": corrections.get("O2", 1.374),
            "F2": corrections.get("F2", 0.924),
            "Cl2": corrections.get("Cl2", 1.228),
        },
        "correction_policy": flat.get(
            "correction_policy", "custom_molecular_reference"
        ),
        "energy_adjust_step": flat.get("energy_adjust_step", 0.01),
    }


def read_plan(project_dir: str | Path) -> dict:
    """Read plan.yaml from *project_dir*."""
    path = Path(project_dir) / PLAN_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{PLAN_FILENAME} not found in {project_dir}")
    with open(path) as f:
        return yaml.safe_load(f)
