"""Configuration loading, validation, and generation for the pipeline.

.. code:: yaml

   project:
     formula: GaN
     dopant_elements: [Mg]
     poscar_src: "MP mp-830"     # 推理结果，含竞争相列表
   parameters:
     functional: pbesol
     encut: null                  # 自动检测
     hubbard_u: false             # auto: 无 TM/f-electron
     pp: []                       # auto: 从 POTCAR 目录排序
   supercell: {min_atoms: 200, max_atoms: 600}
   defects:
     interstitials: false
     complex_n: 1
     max_distance: 5.0
     interstitial_indices: []  # 0-based indices into the dos_extrema candidate list
   corrections:
     O2: 1.374
     Cl2: 1.228
     F2: 0.924
   energy_adjust_step: 0.01
"""

from __future__ import annotations

import json
import logging
import re
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
        "pp": [],
    },
    "supercell": {"min_atoms": 200, "max_atoms": 600},
    "defects": {
        "interstitials": False,
        "complex_n": 1,
        "max_distance": 5.0,
    },
    "corrections": {
        "O2": 1.374,
        "Cl2": 1.228,
        "F2": 0.924,
    },
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

    interstitial: bool = False
    interstitial_indices: list[int] = field(default_factory=list)
    complex_defect_order: int = 1
    remote_cutoff: float = 5.0

    functional: str = "pbesol"
    encut: Optional[float] = None
    hubbard_u: bool = False
    potcar_overrides: list[str] = field(default_factory=list)

    supercell_min_atoms: int = 200
    supercell_max_atoms: int = 600

    molecule_corrections: dict[str, float] = field(default_factory=lambda: {
        "Cl2": 1.228, "O2": 1.374, "F2": 0.924,
    })

    energy_adjust_step: float = 0.01
    custom_poscar_path: Optional[Path] = None

    # runtime
    root: Path = Path(".")

    def __post_init__(self) -> None:
        self.formula = self.formula.strip()
        if not self.formula:
            raise ValueError("formula must be non-empty, e.g. 'GaN'.")
        if self.supercell_min_atoms < 1:
            raise ValueError("supercell_min_atoms must be >= 1.")
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
        return cls(
            root=root,
            formula=p.get("formula", ""),
            dopant_elements=p.get("dopant_elements", []),
            poscar_src=p.get("poscar_src", ""),
            functional=params.get("functional", "pbesol"),
            encut=params.get("encut"),
            hubbard_u=params.get("hubbard_u", False),
            potcar_overrides=params.get("pp", []),
            supercell_min_atoms=sc.get("min_atoms", 200),
            supercell_max_atoms=sc.get("max_atoms", 600),
            interstitial=d.get("interstitials", False),
            interstitial_indices=list(d.get("interstitial_indices", [])),
            complex_defect_order=d.get("complex_n", 1),
            remote_cutoff=d.get("max_distance", 5.0),
            molecule_corrections={
                "O2": corr.get("O2", 1.374),
                "Cl2": corr.get("Cl2", 1.228),
                "F2": corr.get("F2", 0.924),
            },
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
                "pp": list(self.potcar_overrides),
            },
            "supercell": {
                "min_atoms": self.supercell_min_atoms,
                "max_atoms": self.supercell_max_atoms,
            },
            "defects": {
                "interstitials": self.interstitial,
                "interstitial_indices": list(self.interstitial_indices),
                "complex_n": self.complex_defect_order,
                "max_distance": self.remote_cutoff,
            },
            "corrections": dict(self.molecule_corrections),
            "energy_adjust_step": self.energy_adjust_step,
        }

    def to_yaml(self, path: Path) -> None:
        """Dump clean plan (no comments) to *path*."""
        with open(path, "w") as f:
            yaml.dump(self.to_plan(), f, default_flow_style=None,
                      sort_keys=False, allow_unicode=True)

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
        ③ Touch ``mp_flag`` so the CPD stage skips re-running the query.
        ④ Detect ENCUT from POTCAR, DFT+U from element set, POTCAR variants.
        ⑤ Apply kwargs overrides, write plan.yaml.
    """
    root = Path(project_dir)
    plan = _deep_copy(DEFAULT_PLAN)
    plan["project"]["formula"] = formula
    plan["project"]["dopant_elements"] = list(dopant_elements or [])
    plan["parameters"]["functional"] = functional

    from vasp_sop.core.jobs import run_local
    import re

    # ① Load competing phases (rely on pydefect_vasp mp — it's fast and
    #    handles molecule phases like mol_N2 that the MP cache misses)
    cpd_root = root / "cpd"
    cpd_root.mkdir(parents=True, exist_ok=True)
    mp_flag = cpd_root / "mp_flag"

    if not mp_flag.is_file():
        elements = re.findall(r"[A-Z][a-z]?", formula)
        elements += dopant_elements or []
        run_local(
            f"pydefect_vasp mp -e {' '.join(elements)} --e_above_hull 0.0005",
            cwd=cpd_root, timeout=120,
        )
        # Replace parens for shell safety
        for child in list(cpd_root.iterdir()):
            if child.is_dir() and ("(" in child.name or ")" in child.name):
                child.rename(child.with_name(
                    child.name.replace("(", "[").replace(")", "]")
                ))
        mp_flag.touch()

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

            phases.append({
                "mpid": mpid or "?",
                "spg": spg,
                "formula": comp,
                "a": round(s.lattice.a, 3),
                "b": round(s.lattice.b, 3),
                "c": round(s.lattice.c, 3),
                "is_target": is_target,
            })

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


    # ③ ENCUT (from POTCAR in target dir if available)
    if target_dir:
        potcar = target_dir / "POTCAR"
        if potcar.exists():
            inferred = _detect_encut_from_potcar(potcar)
            if inferred:
                plan["parameters"]["encut"] = inferred

    # ④ DFT+U
    if unitcell_poscar.exists():
        plan["parameters"]["hubbard_u"] = _infer_hubbard_u(unitcell_poscar)

    # ⑤ POTCAR variants
    variants = _query_potcar_variants(formula, dopant_elements or [])
    if variants:
        plan["_potcar_variants"] = variants
        plan["parameters"]["pp"] = [
            variants[el][0] if isinstance(variants[el], list) else variants[el]
            for el in sorted(variants)
        ]

    # ⑥ Submit structure_opt VASP ahead of pipeline (skip if already submitted)
    pre_submit_file = cpd_root / ".target_submit.json"
    already_submitted = pre_submit_file.is_file()
    if target_dir and _crisp_available() and not _structure_opt_done(target_dir) and not already_submitted:
        from vasp_sop.core.jobs import submit_vasp, _vasp_input_ready

        if not _vasp_input_ready(target_dir):
            run_local(
                f"vise vs -x {functional} -k 2 "
                f"--options set_hubbard_u True -uis NSW 50",
                cwd=target_dir, timeout=300,
            )
        job = submit_vasp(target_dir)
        with open(pre_submit_file, "w") as f:
            json.dump({"task_name": job.task_name, "work_dir": str(target_dir)}, f)
        logger.info(
            "Structure optimisation pre-submitted: crisp task %s",
            job.task_name,
        )

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

    yaml_str = yaml.dump(plan, default_flow_style=None,
                         sort_keys=False, allow_unicode=True)
    lines = list(yaml_str.splitlines(keepends=True))

    # Find insertion points
    poscar_line: int | None = None
    pp_line: int | None = None
    indent = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("poscar_src:"):
            poscar_line = i
            indent = line[:len(line) - len(line.lstrip())]
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
        phase_comment.append(
            f'{indent}#   poscar_src: "MP mp-xxx"\n'
        )
        phase_comment.append(
            f'{indent}#   poscar_src: "./path/to/POSCAR"\n'
        )
        for c in reversed(phase_comment):
            lines.insert(poscar_line + 1, c)
        if pp_line is not None:
            pp_line += len(phase_comment)

    # Insert POTCAR variant comments after pp
    if potcar_variants and pp_line is not None:
        pp_indent = lines[pp_line][:len(lines[pp_line]) - len(lines[pp_line].lstrip())]
        potcar_comment = [f"{pp_indent}# Available POTCAR variants:\n"]
        for el, variants in potcar_variants.items():
            potcar_comment.append(
                f"{pp_indent}#   {el}: {', '.join(variants)}\n"
            )
        for c in reversed(potcar_comment):
            lines.insert(pp_line + 1, c)

    with open(path, "w") as f:
        f.write("".join(lines))
    return path


# ══════════════════════════════════════════════════════════════════════════
# Inference steps
# ══════════════════════════════════════════════════════════════════════════




def _query_potcar_variants(
    formula: str, dopants: list[str],
) -> dict[str, list[str]]:
    """Enumerate available PAW_PBE POTCAR variants per element."""
    from pymatgen.core import SETTINGS

    potcar_dir = (
        Path(SETTINGS.get("PMG_VASP_PSP_DIR", "")) / "POT_GGA_PAW_PBE_54"
    )
    if not potcar_dir.is_dir():
        return {}

    elements = set(re.findall(r"[A-Z][a-z]?", formula)) | set(dopants)
    variants: dict[str, list[str]] = {}
    for el in sorted(elements):
        matches = sorted(
            d.name for d in potcar_dir.iterdir()
            if d.is_dir() and re.match(
                rf"^{re.escape(el)}(_|$)", d.name, re.IGNORECASE
            )
        )
        if matches:
            variants[el] = matches
    return variants

def _detect_encut_from_potcar(potcar: Path) -> Optional[float]:
    """Detect ENCUT = 1.3 × max(ENMAX) from a POTCAR file."""
    if not potcar.is_file():
        return None
    max_enmax = 0.0
    try:
        text = potcar.read_text()
        for enmax in re.findall(r"ENMAX\s*=\s*([\d.]+)", text):
            max_enmax = max(max_enmax, float(enmax))
    except Exception:
        return None
    return round(max_enmax * 1.3, 1) if max_enmax > 0 else None


def _infer_hubbard_u(poscar_path: Path) -> bool:
    """Return True if any species in POSCAR needs DFT+U."""
    _DFTU_FALLBACK = frozenset({
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
        "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
        "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
        "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy",
        "Ho", "Er", "Tm", "Yb", "U",
    })
    try:
        from pymatgen.core import Structure
        s = Structure.from_file(str(poscar_path))
        return any(el in _DFTU_FALLBACK for el in s.symbol_set)
    except Exception:
        return False


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
    return {
        "project": {
            "formula": flat.get("formula", ""),
            "dopant_elements": flat.get("dopant_elements", []),
            "poscar_src": "",
        },
        "parameters": {
            "functional": flat.get("functional", "pbesol"),
            "encut": None,
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
            "O2": flat.get("molecule_corrections", {}).get("O2", 1.374) if isinstance(flat.get("molecule_corrections"), dict) else 1.374,
            "Cl2": flat.get("molecule_corrections", {}).get("Cl2", 1.228) if isinstance(flat.get("molecule_corrections"), dict) else 1.228,
            "F2": flat.get("molecule_corrections", {}).get("F2", 0.924) if isinstance(flat.get("molecule_corrections"), dict) else 0.924,
        },
        "energy_adjust_step": flat.get("energy_adjust_step", 0.01),
    }


def read_plan(project_dir: str | Path) -> dict:
    """Read plan.yaml from *project_dir*."""
    path = Path(project_dir) / PLAN_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"{PLAN_FILENAME} not found in {project_dir}")
    with open(path) as f:
        return yaml.safe_load(f)


def _crisp_available() -> bool:
    """Check if crisp CLI is on PATH (cached)."""
    from vasp_sop.core.jobs import _crisp_available as _ca
    return _ca()


def _structure_opt_done(target_dir: Path) -> bool:
    """Return True if target VASP already ran (OUTCAR exists)."""
    return (target_dir / "OUTCAR").is_file() or (target_dir / "output" / "OUTCAR").is_file()
