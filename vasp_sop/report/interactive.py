"""Interactive formation-energy + chemical-potential HTML report.

Generates a self-contained HTML page from pydefect post-processing outputs:
  - defect/defect_energy_summary.json
  - cpd/chem_pot_diag.json
  - cpd/target_vertices.yaml

The page renders formation-energy vs Fermi-level plots that respond to
chemical-potential dragging inside the stability region, matching the static
``pydefect pe`` PDF plots in a live, interactive form.

Supports:
  - 1-vertex (unary):     static plot (no CPD interactivity)
  - N-vertex (N >= 2):    topological map of the exact N-dim stability
                          polytope (true N-dim edges and facets); clicks
                          select vertex / facet-interior / edge μ exactly

Public API
----------
``generate_interactive_html(system_dir) -> Path | None``
    Read inputs from *system_dir* and write ``formation_energy_interactive.html``.
    Returns ``None`` when the system is unsupported (e.g. single-vertex).

The renderer never touches pydefect's raw dict shape: inputs arrive as
:class:`~vasp_sop.defect.pydefect_adapter.DefectSummary` and
:class:`~vasp_sop.defect.pydefect_adapter.CpdDiagram` value objects produced
by the adapter, so an upstream schema rename fails loudly at the adapter
instead of silently blanking the page.
"""

from __future__ import annotations

import json
import logging
import math
import re

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Any

# ── colour palette (stable, color-blind-safe scientific series) ─────
_COLORS: list[str] = [
    "#0072B2", "#D55E00", "#CC79A7", "#009E73", "#56B4E9", "#E69F00",
    "#6A5ACD", "#8C564B", "#17BECF", "#BCBD22", "#4C78A8", "#F58518",
    "#54A24B", "#B279A2", "#72B7B2", "#E45756", "#79706E", "#FF9DA6",
    "#9D755D", "#BAB0AC", "#59A14F", "#EDC948", "#AF7AA1", "#76B7B2",
]

# ── elements whose defects are considered *doped* (vs intrinsic) ───
_DOPANT_ELEMENTS: set[str] = {
    "Bi", "Be", "Mg", "Al", "Si", "Ge", "Sn", "Ti", "V", "Cr", "Mn",
    "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "As", "Se", "Zr", "Nb", "Mo",
    "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sb", "Te", "Hf", "Ta", "W",
    "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb",
}


# ═════════════════════════════════════════════════════════════════════
# Input extraction (typed — records come from the pydefect adapter)
# ═════════════════════════════════════════════════════════════════════

def _load_inputs(system_dir: Path) -> tuple[Any, Any]:
    """Return (DefectSummary, CpdDiagram) parsed by the adapter."""
    from vasp_sop.defect.pydefect_adapter import defect_summary, cpd_diagram

    summary_path = system_dir / "defect" / "defect_energy_summary.json"
    de = defect_summary(summary_path)
    cpd = cpd_diagram(system_dir / "cpd", summary_path)
    if de is None or cpd is None:
        raise ValueError("missing interactive report inputs (summary or CPD)")
    return de, cpd


# ═════════════════════════════════════════════════════════════════════
# Chemical-potential data
# ═════════════════════════════════════════════════════════════════════

def _extract_vertex_data(
    cpd: Any,
) -> tuple[list[dict[str, float]], list[str], str, list[str], list[dict[str, list[str]]]]:
    """Return (vertex_mu, vertex_names, host_name, vertex_elements, vertex_phases).

    *vertex_mu*: ``[{elem: μ, ...}]`` — ALL element chemical potentials per
                 cyclically-ordered vertex.
    *vertex_names*: vertex labels (e.g. ``["A", "C", "D", "B"]``).
    *vertex_elements*: ordered list of host elements (from CPD).
    *vertex_phases*: per-vertex ``{"competing": [...], "impurity": [...]}``
                 (the phases that pin this vertex / the dopant phases that
                 are unstable at it).
    """
    rcp_raw: dict = cpd.rel_chem_pots

    # target phase name
    host_name = cpd.target

    # vertex elements (e.g. ["Br", "Cs", "Pb"] or ["Ba", "O"])
    vertex_elements: list[str] = list(cpd.vertex_elements)

    # Collect per-vertex mu dicts — only keys with numeric chemical potentials
    _meta_keys = {"target", "chem_pot", "competing_phases", "impurity_phases"}
    def _mu_dict(v: Any) -> dict[str, float]:
        if not isinstance(v, dict):
            return {}
        # target-vertices records nest μ under ``chem_pot``; the summary's
        # rel_chem_pots are flat. Accept both.
        src = v.get("chem_pot")
        if isinstance(src, dict):
            v = src
        return {k: float(vv) for k, vv in v.items()
                if k not in _meta_keys and isinstance(vv, (int, float))}

    def _phase_list(v: Any, key: str) -> list[str]:
        if isinstance(v, dict):
            raw = v.get(key)
            if isinstance(raw, list):
                return [str(x) for x in raw if x is not None]
        return []

    vert_names = [k for k, v in rcp_raw.items()
                  if isinstance(v, dict) and _mu_dict(v)]
    raw_vert_mu: list[dict[str, float]] = [
        _mu_dict(rcp_raw[vn]) for vn in vert_names
    ]
    vertex_phases: list[dict[str, list[str]]] = [
        {
            "competing": _phase_list(rcp_raw[vn], "competing_phases"),
            "impurity": _phase_list(rcp_raw[vn], "impurity_phases"),
        }
        for vn in vert_names
    ]

    # Build 2D coordinates for cyclic sort
    if len(vertex_elements) >= 2:
        ax0, ax1 = vertex_elements[0], vertex_elements[1]
    elif raw_vert_mu:
        # Fallback: use first two keys in any vertex
        keys0 = list(raw_vert_mu[0].keys())
        ax0, ax1 = keys0[0], keys0[1] if len(keys0) > 1 else keys0[0]
        vertex_elements = [ax0, ax1] + [
            k for k in keys0 if k not in (ax0, ax1)
        ]
    else:
        raise ValueError("chem_pot_diag: no vertex data available")

    nv = len(raw_vert_mu)
    coords_2d = [(v.get(ax0, 0.0), v.get(ax1, 0.0)) for v in raw_vert_mu]

    # Cyclic sort
    if nv > 1:
        cent0 = sum(c[0] for c in coords_2d) / nv
        cent1 = sum(c[1] for c in coords_2d) / nv
        order = sorted(
            range(nv),
            key=lambda i: math.atan2(
                coords_2d[i][1] - cent1, coords_2d[i][0] - cent0,
            ),
        )
    else:
        order = [0]

    vertex_mu = [raw_vert_mu[i] for i in order]
    vertex_names = [vert_names[i] for i in order]
    vertex_phases = [vertex_phases[i] for i in order]

    return vertex_mu, vertex_names, host_name, vertex_elements, vertex_phases


# ═════════════════════════════════════════════════════════════════════
# Defect local structure extraction
# ═════════════════════════════════════════════════════════════════════

def _extract_defect_structure(
    system_dir: Path, defect_name: str, charge: int, cutoff: float = 5.0
) -> dict[str, Any] | None:
    """Extract local structure around a defect.
    
    Reads defect_entry.json, identifies the defect center atom, and extracts
    all atoms within cutoff distance. Returns JSON with atoms list and metadata.
    """
    import numpy as np
    
    # Find defect directory
    defect_dir = system_dir / "defect" / f"{defect_name}_{charge}"
    if not defect_dir.exists():
        for d in (system_dir / "defect").iterdir():
            if d.is_dir() and d.name.startswith(defect_name):
                defect_dir = d
                break
        else:
            return None
    
    defect_entry_path = defect_dir / "defect_entry.json"
    if not defect_entry_path.exists():
        return None
    
    with open(defect_entry_path) as f:
        defect_data = json.load(f)
    
    structure = defect_data["structure"]
    sites = structure["sites"]
    
    # Parse defect name to identify center atom
    parts = defect_name.split("_")
    defect_species = parts[0] if len(parts) >= 2 else defect_name
    host_site = parts[1] if len(parts) >= 2 else None
    
    # Find defect center atom
    defect_center_idx = None
    for i, site in enumerate(sites):
        label = site.get("label", "")
        species = site["species"][0]["element"]
        
        if defect_species != "Va" and species == defect_species:
            if host_site and host_site in label:
                defect_center_idx = i
                break
            elif not host_site:
                defect_center_idx = i
                break
    
    if defect_center_idx is None:
        defect_center_idx = 0
    
    center_xyz = np.array(sites[defect_center_idx]["xyz"])
    
    # Extract atoms within cutoff
    atoms = []
    for i, site in enumerate(sites):
        xyz = np.array(site["xyz"])
        distance = float(np.linalg.norm(xyz - center_xyz))
        
        if distance <= cutoff or i == defect_center_idx:
            atoms.append({
                "element": site["species"][0]["element"],
                "x": round(float(xyz[0]), 4),
                "y": round(float(xyz[1]), 4),
                "z": round(float(xyz[2]), 4),
                "is_defect": i == defect_center_idx,
                "is_neighbor": distance <= cutoff and i != defect_center_idx,
                "distance": round(distance, 4),
                "label": site.get("label", ""),
            })
    
    return {
        "atoms": atoms,
        "metadata": {
            "defect_name": defect_name,
            "charge": charge,
            "formula": defect_data.get("formula", ""),
            "cutoff": cutoff,
            "num_atoms": len(atoms),
        },
    }


# ═════════════════════════════════════════════════════════════════════
# Defect data (filtered, corrected)
# ═════════════════════════════════════════════════════════════════════

def _build_defects(summary: Any) -> dict[str, dict[str, Any]]:
    """Return {name: {charges: [{q, e0}, ...], delta: {elem: Δn}}}.

    Corrections are already aggregated into each ``FormationEnergy`` by the
    adapter (the renderer never re-sums them); shallow charge states are
    filtered (matching pydefect's ``allow_shallow=False`` default).
    """
    shallow: dict[str, list[int]] = {
        d.name: [fe.charge for fe in d.formation_energies if fe.is_shallow]
        for d in summary.defects
    }

    defects: dict[str, dict[str, Any]] = {}
    for d in summary.defects:
        skip_qs = shallow.get(d.name, [])
        filtered: list[dict[str, float]] = []
        for fe in d.formation_energies:
            if fe.charge in skip_qs:
                continue
            filtered.append({
                "q": float(fe.charge),
                "e0": round(fe.formation_energy + fe.correction, 6),
            })
        if filtered:
            defects[d.name] = {"charges": filtered, "delta": dict(d.atom_io)}
    return defects


# ═════════════════════════════════════════════════════════════════════
# Readout chemistry: ion valence (compound-inferred) + magnetization
# ═════════════════════════════════════════════════════════════════════

# Candidate formal oxidation states per element, used only to SOLVE the
# host compound's charge neutrality — the host formula is the source of
# truth, not this list. A substitution's ion valence is ox(host site) + q
# (the displayed 价态, which flips across charge transitions).
_COMMON_VALENCES: dict[str, list[int]] = {
    "O": [-2], "Y": [3], "Ti": [4], "Ba": [2], "Al": [3], "Ca": [2],
    "Sr": [2], "Ga": [3], "La": [3], "Zr": [4], "Sn": [4], "Gd": [3],
    "Sb": [5], "W": [6], "Sc": [3], "B": [3], "Fe": [2, 3], "Bi": [3],
    "H": [1], "N": [-3], "F": [-1], "Cl": [-1], "S": [-2], "Se": [-2],
    "Te": [-2], "P": [5], "Si": [4], "Ge": [4], "Mn": [2, 3, 4],
    "Cr": [2, 3, 6], "V": [3, 5], "Co": [2, 3], "Ni": [2, 3], "Cu": [1, 2],
    "Zn": [2], "Nb": [5], "Mo": [6], "Hf": [4], "Ta": [5], "Li": [1],
    "Na": [1], "K": [1], "Mg": [2], "In": [3], "Cd": [2], "Ce": [3, 4],
    "Pr": [3], "Nd": [3], "Sm": [3], "Eu": [2, 3], "Er": [3], "Tm": [3],
    "Yb": [2, 3], "Lu": [3], "Be": [2], "As": [5], "Ag": [1], "Au": [1, 3],
    "Pt": [2, 4], "Pd": [2, 4], "Ru": [3, 4], "Rh": [3], "Ir": [3, 4],
    "Os": [4], "Re": [4, 7], "Tc": [4, 7], "Hg": [1, 2], "Pb": [2, 4],
    "Th": [4], "U": [4, 6],
}

_SITE_ELEMENT_RE = re.compile(r"^([A-Z][a-z]?)(\d+)$")
_FORMULA_TOKEN_RE = re.compile(r"([A-Z][a-z]?)(\d*)")


def _formula_elements(formula: str) -> dict[str, int]:
    """Element counts of a host formula, with bracket groups expanded.

    ``Gd2GaSbO7`` -> ``{Gd: 2, Ga: 1, Sb: 1, O: 7}``;
    ``Sr[FeO2]2`` -> ``{Sr: 1, Fe: 2, O: 4}``. Returns ``{}`` when the
    formula is empty or unparseable.
    """
    text = formula.strip()
    if not text:
        return {}
    counts: dict[str, int] = {}
    # Expand bracket groups first: [..]N repeats the group N times.
    while "[" in text:
        m = re.search(r"\[([^\[\]]*)\](\d*)", text)
        if not m:
            return {}
        mult = int(m.group(2)) if m.group(2) else 1
        text = text[:m.start()] + (m.group(1) * mult) + text[m.end():]
    for m in _FORMULA_TOKEN_RE.finditer(text):
        sym = m.group(1)
        n = int(m.group(2)) if m.group(2) else 1
        counts[sym] = counts.get(sym, 0) + n
    return counts


def _infer_host_valences(formula: str) -> dict[str, int]:
    """Infer each host element's formal oxidation state from the formula.

    Solves charge neutrality (Σ count·valence = 0) with O fixed at −2,
    trying each element's candidate valences in order; the first neutral
    combination wins. Returns ``{}`` when no combination balances (mixed
    or unknown chemistry) — callers then fall back to ``?`` labels.
    """
    counts = _formula_elements(formula)
    if not counts:
        return {}
    elements = [e for e in counts if e != "O"]
    if not elements:
        return {}
    candidates = [
        _COMMON_VALENCES.get(e, [0]) for e in elements
    ]
    o_count = counts.get("O", 0)
    target = 2 * o_count  # O fixed at −2

    import itertools
    for combo in itertools.product(*candidates):
        total = sum(n * v for n, v in zip((counts[e] for e in elements), combo))
        if total == target:
            return {e: v for e, v in zip(elements, combo)}
    return {}


def _ion_valence_template(
    name: str, host_valences: dict[str, int],
) -> dict[str, Any]:
    """How to render a defect's ion valence from its charge state q.

    Returns ``{"p": species, "h": host-site valence}`` for a substitution
    X_Yn (label = X^(h+q)), ``{"p": species, "h": None}`` for an
    interstitial X_iN (label = X^q — charge conservation), ``{"v": True}``
    for a vacancy Va_Xn (label = q itself), or ``{"p": "?", "h": None}``
    when the site element has no inferred valence.
    """
    parts = name.split("_")
    if len(parts) < 2:
        return {"p": "?", "h": None}
    head = parts[0]
    if head == "Va":
        return {"v": True}
    site = parts[1]
    if re.match(r"^i\d+$", site):
        return {"p": head, "h": None}
    m = _SITE_ELEMENT_RE.match(site)
    if m:
        host_ox = host_valences.get(m.group(1))
        if host_ox is not None:
            return {"p": head, "h": host_ox}
    return {"p": "?", "h": None}


def _load_magnetizations(
    system_dir: Path, names: list[str],
) -> dict[str, dict[int, float]]:
    """Read total magnetization (signed μB) per charge state from disk.

    Returns ``{defect name: {charge q: magnetization}}`` by walking
    ``defect/<name>_<q>/calc_results.json``. Directories without a
    readable ``calc_results.json`` (never analyzed / unconverged) and
    non-defect dirs (``perfect``) contribute nothing — the readout then
    renders ``—`` for those charge states.
    """
    import json as _json

    defect_dir = system_dir / "defect"
    if not defect_dir.is_dir():
        return {}
    mags: dict[str, dict[int, float]] = {}
    for d in defect_dir.iterdir():
        if not d.is_dir():
            continue
        m = re.match(r"^(.*)_(-?\d+)$", d.name)
        if not m:
            continue
        name, q = m.group(1), int(m.group(2))
        if name not in names:
            continue
        cr = d / "calc_results.json"
        if not cr.is_file():
            continue
        try:
            data = _json.loads(cr.read_text())
            mag = data.get("magnetization")
        except Exception:
            continue
        if isinstance(mag, (int, float)):
            mags.setdefault(name, {})[q] = round(float(mag), 3)
    return mags


def _defect_kind(name: str) -> str:
    """Site-independent defect kind: ``Va_O1``/``Va_O13`` -> ``Va_O``.

    Mirrors the legend grouping (``defectBase`` in the generated JS):
    trailing site digits are stripped.
    """
    return re.sub(r"\d+$", "", name)


def _kind_colors(names: list[str]) -> list[str]:
    """One color per defect KIND, shared by every site of that kind.

    ``Va_O1`` and ``Va_O13`` draw with the same color; ``Bi_Ti1`` differs
    from ``Va_O1``. Colors are assigned in order of first appearance.
    """
    by_kind: dict[str, str] = {}
    for n in names:
        k = _defect_kind(n)
        if k not in by_kind:
            by_kind[k] = _COLORS[len(by_kind) % len(_COLORS)]
    return [by_kind[_defect_kind(n)] for n in names]


def _sort_defect_names(defects: dict[str, Any]) -> list[str]:
    def _key(name: str) -> tuple[int, str]:
        for elem in _DOPANT_ELEMENTS:
            if name.startswith(elem + "_"):
                return (0, name)
        return (5, name)
    return sorted(defects, key=_key)


# ═════════════════════════════════════════════════════════════════════
# Display-name typesetting (subscript / superscript segments)
# ═════════════════════════════════════════════════════════════════════

_SUBSCRIPT_TRANS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUPERSCRIPT_TRANS = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def _dopant_elements(system_dir: Path) -> list[str]:
    """Return dopant elements from ``plan.yaml`` (e.g. ``["Bi"]``).

    Empty when the plan is absent or has no ``dopant_elements``.  These
    elements mark exogenous defects (``Bi_*``) that must be excluded from
    the intrinsic charge-neutrality Fermi-level balance.
    """
    import yaml as _yaml

    plan = system_dir / "plan.yaml"
    if not plan.is_file():
        return []
    try:
        loaded = _yaml.safe_load(plan.read_text())
    except Exception:
        return []
    if not isinstance(loaded, dict):
        return []
    dopants = loaded.get("dopant_elements")
    if not isinstance(dopants, list):
        return []
    return [str(e) for e in dopants]


def _formula_subscripts(text: str) -> str:
    """Chemical-formula display: every digit becomes a Unicode subscript.

    ``CaAl4O7`` -> ``CaAl₄O₇``, ``Sr[FeO2]2`` -> ``Sr[FeO₂]₂``.
    """
    return text.translate(_SUBSCRIPT_TRANS)


def _formula_html(text: str) -> str:
    """HTML title rendering: digit runs wrapped in ``<sub>``."""
    return re.sub(r"\d+", lambda m: f"<sub>{m.group(0)}</sub>", text)


def _defect_segments(name: str) -> list[list[str]]:
    """Typeset a defect name as [style, text] segments for rendering.

    ``Al_Ca1_-1`` -> ``[["n","Al"],["s","Ca1"],["p","-1"]]`` (normal /
    subscript / superscript); ``Bi_Pb1`` (no charge part) ->
    ``[["n","Bi"],["s","Pb1"]]``; anything unparseable renders as one
    normal segment. The site keeps its letters: Unicode has no
    subscript Latin letters, so sub/sup must be drawn with a smaller
    font and an offset baseline (canvas) or ``<sub>/<sup>`` (HTML).
    """
    parts = name.split("_")
    if parts and parts[0] == "1":  # legacy ``1_`` prefix convention
        parts = parts[1:]
    if len(parts) >= 3 and re.fullmatch(r"[+-]?\d+|\d+[+-]", parts[-1]):
        return [
            ["n", parts[0]],
            ["s", "_".join(parts[1:-1])],
            ["p", parts[-1]],
        ]
    if len(parts) == 2:
        return [["n", parts[0]], ["s", parts[1]]]
    return [["n", name]]


# ═════════════════════════════════════════════════════════════════════
# JS helpers — CPD geometry: 2D seed projection
# ═════════════════════════════════════════════════════════════════════


def _cpd_canvas_js(
    n_vertices: int,
    poly_2d: list[list[float]],
    vertex_mu: list[dict[str, float]],
    vertex_names: list[str],
    vertex_phases: list[dict[str, list[str]]],
    host_elements: list[str],
) -> str:
    """Return the CPD canvas JS block for *n_vertices*.

    Draws the target phase's stability region as a 2D non-Euclidean
    diagram of the N-dim polytope (no axes — μ values live in the panel
    below): the region's shape/outline plus the competing phase that
    bounds each boundary segment, derived from the polytope topology.

    Geometry lives in the HOST-element subspace: impurity-element μ
    (Bi, Fe, Mn, …) is a per-vertex branch of the impurity equilibrium,
    not an intrinsic dimension of the stability region. In that subspace
    the region is 2D for 3 host elements (drawn in its exact shape) and
    3D for 4+ hosts (spring-layout topological embedding; outer outline
    = 2D hull of the drawing, whose edges are true polytope edges).

    Boundary labels: the competing phases of an edge = intersection of
    the endpoint phase lists (1 phase on a 2D-region edge, 2 on a 3D
    region edge). Selection is face-level exact: vertex click = exact
    vertex μ, region click = N-dim barycentric combination of that face's
    vertices, edge click = endpoint interpolation. Every canvas selection
    is inside the polytope by construction — 区域外 can only come from
    the per-element sliders.
    """
    js = json.dumps

    common = f"""
var cc = document.getElementById("cpd"), cctx = cc.getContext("2d");
var cW = 600, cH = 450, cP = {{l:35,r:10,t:20,b:25}};
/* ── HiDPI: backing store scaled by devicePixelRatio; logical coords
 * remain cW×cH so all drawing code below is resolution-independent. ── */
var DPR = window.devicePixelRatio || 1;
cc.width = cW * DPR; cc.height = cH * DPR;
cc.style.width = cW + "px"; cc.style.height = cH + "px";
cctx.scale(DPR, DPR);
var FONT_SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Inter','Helvetica Neue',Arial,'Noto Sans',sans-serif";
var FONT_MONO = "'JetBrains Mono','Fira Code',ui-monospace,SFMono-Regular,Consolas,'Liberation Mono',Menlo,monospace";
var POLY = {js(poly_2d)};   // 2D projection — spring-layout seed only
var VPHASES = {js(vertex_phases)};
var VERTEX_MU = {js(vertex_mu)};
var HOST_ELS = {js(host_elements)};
var selectionMode = "区域内插值";
var pickPath = "slider";   // provenance of the current mu: vertex|edge|facet|fan|slider

// Exact stability-region geometry in mu space: the vertices' convex hull in
// its r-dim affine subspace. Membership = every facet signed distance >= 0;
// the boundary distance is their minimum (positive inside, negative out).
// Tolerances are scale-relative (1e-4 of the largest vertex difference)
// and defined BEFORE buildHull runs. For hulls with near-coplanar vertices
// the facet planes are ill-conditioned: the inside/outside verdict stays
// exact, but the boundary distance may deviate by up to the gap (~0.02 eV).
var HULL=buildHull();
function buildHull(){{
  // Geometry lives in the HOST-element subspace: impurity μ is a per-vertex
  // branch of the impurity equilibrium, not an intrinsic region dimension —
  // including it inflates the hull rank with sliver facets.
  var el=HOST_ELS.filter(function(e){{return VERTEX_MU.some(function(v){{return v[e]!==undefined;}});}});
  var n=VERTEX_MU.length,v0=VERTEX_MU[0],basis=[];
  function proj(mu){{
    var w=el.map(function(e){{return (mu[e]!==undefined?mu[e]:0)-(v0[e]!==undefined?v0[e]:0);}});
    return basis.map(function(b){{var dot=0;for(var k=0;k<w.length;k++)dot+=w[k]*b[k];return dot;}});
  }}
  var maxNrm=0;
  for(var i=1;i<n;i++){{
    var w0=el.map(function(e){{return (VERTEX_MU[i][e]!==undefined?VERTEX_MU[i][e]:0)-(v0[e]!==undefined?v0[e]:0);}});
    var n0=Math.sqrt(w0.reduce(function(s,x){{return s+x*x;}},0));
    if(n0>maxNrm)maxNrm=n0;
  }}
  // Rank tolerance is coarse (1e-3): batch drift puts vertices a few meV off
  // the true region subspace; facet/verdict tolerance is tight (1e-4) and
  // applies inside the projected subspace where that drift is gone.
  var RTOL=1e-3*maxNrm,HTOL=1e-4*maxNrm;
  for(var i=1;i<n;i++){{
    var w=el.map(function(e){{return (VERTEX_MU[i][e]!==undefined?VERTEX_MU[i][e]:0)-(v0[e]!==undefined?v0[e]:0);}});
    basis.forEach(function(b){{var dot=0;for(var k=0;k<w.length;k++)dot+=w[k]*b[k];for(var k=0;k<w.length;k++)w[k]-=dot*b[k];}});
    var nrm=Math.sqrt(w.reduce(function(s,x){{return s+x*x;}},0));
    if(nrm>RTOL){{for(var k=0;k<w.length;k++)w[k]/=nrm;basis.push(w);}}
  }}
  var r=basis.length,U=VERTEX_MU.map(proj),facets=[];
  if(r===0){{
    facets=[];
  }}else if(r===1){{
    var L=Math.sqrt(U[1].reduce(function(s,x,i){{return s+x*x;}},0));
    facets=[{{b:L,verts:[0,1]}}];
  }}else{{
    function checkFacet(F){{
      var base=U[F[0]],span=[];
      for(var i3=1;i3<F.length;i3++){{
        var w=U[F[i3]].slice();
        for(var k3=0;k3<w.length;k3++)w[k3]-=base[k3];
        span.forEach(function(b){{var dot=0;for(var k3=0;k3<w.length;k3++)dot+=w[k3]*b[k3];for(var k3=0;k3<w.length;k3++)w[k3]-=dot*b[k3];}});
        var nrm=Math.sqrt(w.reduce(function(s,x){{return s+x*x;}},0));
        if(nrm>1e-7){{for(var k3=0;k3<w.length;k3++)w[k3]/=nrm;span.push(w);}}
      }}
      if(span.length!==r-1)return;
      // Normal seed: the sum of ALL vertex coordinates.
      var nv=U[0].slice();
      for(var i4=1;i4<U.length;i4++)for(var k4=0;k4<nv.length;k4++)nv[k4]+=U[i4][k4];
      span.forEach(function(b){{var dot=0;for(var k=0;k<nv.length;k++)dot+=nv[k]*b[k];for(var k=0;k<nv.length;k++)nv[k]-=dot*b[k];}});
      var nn=Math.sqrt(nv.reduce(function(s,x){{return s+x*x;}},0));
      if(nn<1e-7)return;
      for(var k=0;k<nv.length;k++)nv[k]/=nn;
      var c=0;for(var k=0;k<nv.length;k++)c+=nv[k]*U[F[0]][k];
      var mn=Infinity,mx=-Infinity;
      U.forEach(function(u){{var s=0;for(var k=0;k<nv.length;k++)s+=nv[k]*u[k];s-=c;if(s<mn)mn=s;if(s>mx)mx=s;}});
      if(mn<-HTOL){{
        if(mx>HTOL)return;                 // plane cuts the hull: not a facet
        for(var k=0;k<nv.length;k++)nv[k]*=-1;c=-c;mn=-mx;  // all on minus side: flip
      }}
      var fv=[];
      U.forEach(function(u,i){{var s=0;for(var k=0;k<nv.length;k++)s+=nv[k]*u[k];s-=c;if(Math.abs(s)<=HTOL)fv.push(i);}});
      facets.push({{n:nv,c:c,verts:fv}});
    }}
    function combos(start,k,cur){{
      if(cur.length===k){{checkFacet(cur);return;}}
      for(var j=start;j<=n-k+cur.length;j++){{cur.push(j);combos(j+1,k,cur);cur.pop();}}
    }}
    combos(0,r,[]);
    // Clean up near-coplanar slivers: a facet through the same vertex group
    // is enumerated once per r-subset with planes wobbling by the vertex
    // scatter — dedupe by VERTEX SET (all vertices within tol of the plane),
    // then drop subfaces (a facet whose vertices all lie on a bigger facet).
    // Verdict-unaffected: duplicates/subfaces carry the same plane distances.
    var seenFaces={{}};
    facets=facets.filter(function(f){{
      var key=f.verts.slice().sort(function(a,b){{return a-b;}}).join(",");
      if(seenFaces[key])return false;
      seenFaces[key]=true;return true;
    }});
    facets=facets.filter(function(f){{
      return !facets.some(function(g){{
        return g!==f&&g.verts.length>f.verts.length&&f.verts.every(function(v){{return g.verts.indexOf(v)>=0;}});
      }});
    }});
  }}
  return {{proj:proj,facets:facets,r:r,tol:HTOL}};
}}
function hullState(mu){{
  var u=HULL.proj(mu);
  if(HULL.r===0){{
    var d=Math.sqrt(u.reduce(function(s,x){{return s+x*x;}},0));
    return {{inside:d<=HULL.tol,dist:d}};
  }}
  if(HULL.r===1){{
    var L=HULL.facets[0].b,t=u[0]/L;
    if(t<0||t>1)return {{inside:false,dist:Math.min(-t*L,(t-1)*L)}};
    return {{inside:true,dist:Math.min(t*L,(1-t)*L)}};
  }}
  var dist=Infinity,inside=true;
  HULL.facets.forEach(function(f){{
    var s=-f.c;for(var k=0;k<f.n.length;k++)s+=f.n[k]*u[k];
    if(s<dist)dist=s;
    if(s< -HULL.tol)inside=false;
  }});
  return {{inside:inside,dist:dist}};
}}

// ---- topological map: true adjacency of the N-dim polytope ----
// ALL_FACET_VERTS: every hull facet — drives the layout, the full edge set
// and the region fill, so the drawing shows the true polytope structure.
// FACET_VERTS: the REAL facets only (competing phase stable on the facet =
// non-empty intersection of the vertex phase lists) — drives click picking
// and the marker, where near-coplanar slivers would make a mu match several
// faces (pickMu and markerPos stay mutually exact). The VERDICT (hullState)
// uses ALL facets from buildHull.
var ALL_FACET_VERTS = HULL.facets.map(function(f){{return f.verts||[];}});
var FACET_VERTS = ALL_FACET_VERTS.filter(function(fv){{
  var inter=(VPHASES[fv[0]].competing||[]);
  for(var i2=1;i2<fv.length;i2++){{
    var L2=VPHASES[fv[i2]].competing||[];
    inter=inter.filter(function(x){{return L2.indexOf(x)>=0;}});
  }}
  return inter.length>0;
}});
function computeEdges(FV){{
  var e=[];
  for(var i=0;i<VERTEX_MU.length;i++)for(var j=i+1;j<VERTEX_MU.length;j++){{
    var T=FV.filter(function(F){{return F.indexOf(i)>=0&&F.indexOf(j)>=0;}});
    if(!T.length)continue;
    var common=T[0].slice();
    T.forEach(function(F){{common=common.filter(function(x){{return F.indexOf(x)>=0;}});}});
    if(common.length===2)e.push([i,j]);
  }}
  return e;
}}
var ALL_EDGES = computeEdges(ALL_FACET_VERTS);

// ---- fixed-mu section: constrain element chemical potentials ----
// Fixing host-element mu values cuts the stability polytope by the
// hyperplane (mu_fixed = c): the section is the convex polygon of the
// edge×hyperplane intersections (each intersection = one polytope edge,
// linearly interpolated in full mu space). d = (N_host-1) - n_fixed is the
// section dimension: 2 → polygon, 1 → segment, 0 → point, empty → no
// stability region under the constraint. Section edges inherit the
// competing phase of the target face containing both support edges.
var FIXED = {{}};
var SECTION = null;
function buildSection(){{
  var fe=[],cv={{}};
  for(var e in FIXED)if(HOST_ELS.indexOf(e)>=0&&FIXED[e]!==undefined&&FIXED[e]!==null){{fe.push(e);cv[e]=FIXED[e];}}
  if(!fe.length)return null;
  var free=HOST_ELS.filter(function(e){{return fe.indexOf(e)<0;}});
  var seen={{}},pts=[];
  ALL_EDGES.forEach(function(ed){{
    var a=VERTEX_MU[ed[0]],b=VERTEX_MU[ed[1]];
    var t=null,ok=true;
    fe.forEach(function(e){{
      var va=a[e],vb=b[e];
      if(va===undefined||vb===undefined){{ok=false;return;}}
      var den=vb-va;
      var ti=Math.abs(den)>1e-12?(cv[e]-va)/den:(Math.abs(cv[e]-va)<1e-9?0:null);
      if(ti===null){{ok=false;return;}}
      if(t===null)t=ti;else if(Math.abs(t-ti)>1e-6)ok=false;
    }});
    if(!ok||t===null)return;
    if(t<-1e-9||t>1+1e-9)return;
    var t2=Math.max(0,Math.min(1,t));
    var mu={{}};
    for(var el in a)mu[el]=a[el]*(1-t2)+(b[el]!==undefined?b[el]:a[el])*t2;
    var key=free.map(function(e){{return mu[e]!==undefined?mu[e].toFixed(5):"x";}}).join(",");
    if(seen[key])return;
    seen[key]=true;
    pts.push({{mu:mu,ed:ed,t:t2}});
  }});
  var S={{fe:fe,cv:cv,free:free,pts:pts,d:HOST_ELS.length-1-fe.length,empty:pts.length===0}};
  if(!S.empty){{
    if(S.d===2){{
      var xs=pts.map(function(p){{return p.mu[S.free[0]]||0;}});
      var ys=pts.map(function(p){{return p.mu[S.free[1]]||0;}});
      var x0=Math.min.apply(null,xs),x1=Math.max.apply(null,xs);
      var y0=Math.min.apply(null,ys),y1=Math.max.apply(null,ys);
      S.box={{x0:x0,y0:y0,sx:(x1-x0)||1,sy:(y1-y0)||1}};
      var pad=0.07,span=1-2*pad;
      S.xy=pts.map(function(p){{return[pad+((p.mu[S.free[0]]||0)-x0)/S.box.sx*span,pad+((p.mu[S.free[1]]||0)-y0)/S.box.sy*span];}});
      S.order=hull2D(pts.map(function(p,i){{return i;}}),S.xy);
    }}else if(S.d===1){{
      var vals=pts.map(function(p){{return p.mu[S.free[0]]||0;}});
      var m0=Math.min.apply(null,vals),m1=Math.max.apply(null,vals);
      S.box={{x0:m0,y0:0,sx:(m1-m0)||1,sy:1}};
      var pad1=0.08,span1=1-2*pad1;
      var xy1=pts.map(function(p){{return[pad1+((p.mu[S.free[0]]||0)-m0)/S.box.sx*span1,0.5];}});
      S.xy=xy1;
      S.order=pts.map(function(p,i){{return i;}}).sort(function(a,b){{return xy1[a][0]-xy1[b][0];}});
    }}else{{
      S.box={{x0:0,y0:0,sx:1,sy:1}};
      S.xy=pts.map(function(){{return[0.5,0.5];}});
      S.order=[];
    }}
  }}
  return S;
}}
function sectionLabel(oi,oj){{
  var ed1=SECTION.pts[SECTION.order[oi]].ed,ed2=SECTION.pts[SECTION.order[oj]].ed;
  var cand=ALL_FACET_VERTS.filter(function(F){{
    return F.indexOf(ed1[0])>=0&&F.indexOf(ed1[1])>=0&&F.indexOf(ed2[0])>=0&&F.indexOf(ed2[1])>=0;
  }});
  if(!cand.length)return "";
  var F=cand[0];
  var inter=(VPHASES[F[0]].competing||[]);
  for(var i2=1;i2<F.length;i2++){{var L2=VPHASES[F[i2]].competing||[];inter=inter.filter(function(x){{return L2.indexOf(x)>=0;}});}}
  return inter.join(" · ");
}}
function muFromSection(w){{
  // w: weights over SECTION.pts (index → weight); returns full mu with fixed values
  var mu={{}};
  for(var e in SECTION.cv)mu[e]=SECTION.cv[e];
  SECTION.pts.forEach(function(pt,i){{
    if(!w[i])return;
    for(var e2 in pt.mu){{
      if(SECTION.cv[e2]!==undefined)continue;
      mu[e2]=(mu[e2]||0)+w[i]*pt.mu[e2];
    }}
  }});
  return mu;
}}
function pickSection(px,py){{
  var n=SECTION.pts.length,ord=SECTION.order;
  var best=-1,bd=Infinity;
  SECTION.xy.forEach(function(l,i){{
    var q=layPx(l),d=Math.hypot(px-q[0],py-q[1]);
    if(d<bd){{bd=d;best=i;}}
  }});
  if(bd<=14){{pickPath="vertex";selectionMode="截面顶点";var w=new Array(n).fill(0);w[best]=1;return muFromSection(w);}}
  if(SECTION.d===2){{
    for(var k=1;k<ord.length-1;k++){{
      var tb=triBary(layPx(SECTION.xy[ord[0]]),layPx(SECTION.xy[ord[k]]),layPx(SECTION.xy[ord[k+1]]),px,py);
      if(tb&&tb[0]>=-1e-9&&tb[1]>=-1e-9&&tb[2]>=-1e-9){{
        var w=new Array(n).fill(0);
        w[ord[0]]=tb[0];w[ord[k]]=tb[1];w[ord[k+1]]=tb[2];
        pickPath="facet";selectionMode="区域内插值";
        return muFromSection(w);
      }}
    }}
  }}
  var bestT=-1,bestE=-1,be=Infinity;
  for(var e2=0;e2<ord.length;e2++){{
    var a=layPx(SECTION.xy[ord[e2]]),b2=layPx(SECTION.xy[ord[(e2+1)%ord.length]]);
    var dx=b2[0]-a[0],dy=b2[1]-a[1],len2=dx*dx+dy*dy;
    var t=len2>1e-9?((px-a[0])*dx+(py-a[1])*dy)/len2:0;
    t=Math.max(0,Math.min(1,t));
    var qx=a[0]+t*dx,qy=a[1]+t*dy;
    var d=Math.hypot(px-qx,py-qy);
    if(d<be){{be=d;bestT=t;bestE=e2;}}
  }}
  if(bestE>=0&&be<=8){{
    var w=new Array(n).fill(0);
    w[ord[bestE]]=1-bestT;w[ord[(bestE+1)%ord.length]]=bestT;
    pickPath="edge";selectionMode="边界插值";
    return muFromSection(w);
  }}
  if(best>=0){{pickPath="vertex";selectionMode="截面顶点";var w=new Array(n).fill(0);w[best]=1;return muFromSection(w);}}
  return null;
}}
function secPos(mu){{
  // position of mu's free part in the section drawing, clamped to the section
  if(SECTION.empty)return layPx([0.5,0.5]);
  var fx=mu[SECTION.free[0]],fy=mu[SECTION.free[1]];
  if(fx===undefined||fy===undefined)return layPx([0.5,0.5]);
  var p=layPx([0.07+((fx-SECTION.box.x0)/SECTION.box.sx)*0.86,0.07+((fy-SECTION.box.y0)/SECTION.box.sy)*0.86]);
  if(SECTION.d===1)return layPx([0.08+((fx-SECTION.box.x0)/SECTION.box.sx)*0.84,0.5]);
  if(SECTION.d===0)return layPx([0.5,0.5]);
  // clamp into the section polygon
  var ord=SECTION.order;
  for(var k=1;k<ord.length-1;k++){{
    var tb=triBary(layPx(SECTION.xy[ord[0]]),layPx(SECTION.xy[ord[k]]),layPx(SECTION.xy[ord[k+1]]),p[0],p[1]);
    if(tb&&tb[0]>=-1e-9&&tb[1]>=-1e-9&&tb[2]>=-1e-9)return p;
  }}
  var best=null,bestD=Infinity;
  for(var e2=0;e2<ord.length;e2++){{
    var a=layPx(SECTION.xy[ord[e2]]),b2=layPx(SECTION.xy[ord[(e2+1)%ord.length]]);
    var dx=b2[0]-a[0],dy=b2[1]-a[1],len2=dx*dx+dy*dy;
    var t=len2>1e-9?((p[0]-a[0])*dx+(p[1]-a[1])*dy)/len2:0;
    t=Math.max(0,Math.min(1,t));
    var qx=a[0]+t*dx,qy=a[1]+t*dy;
    var d=(p[0]-qx)*(p[0]-qx)+(p[1]-qy)*(p[1]-qy);
    if(d<bestD){{bestD=d;best=[qx,qy];}}
  }}
  return best||p;
}}

function springLayout(seed,edges){{
  var n=seed.length,pos=seed.map(function(p){{return [p[0],p[1]];}});
  if(n<2)return [[0.5,0.5]];
  var k=Math.sqrt(1/n),t=k*2;
  for(var it=0;it<120;it++){{
    var disp=pos.map(function(){{return [0,0];}});
    for(var i=0;i<n;i++)for(var j=i+1;j<n;j++){{
      var dx=pos[i][0]-pos[j][0],dy=pos[i][1]-pos[j][1];
      var d=Math.sqrt(dx*dx+dy*dy)||1e-6;
      var f=k*k/d;
      disp[i][0]+=dx/d*f;disp[i][1]+=dy/d*f;
      disp[j][0]-=dx/d*f;disp[j][1]-=dy/d*f;
    }}
    edges.forEach(function(e){{
      var dx=pos[e[0]][0]-pos[e[1]][0],dy=pos[e[0]][1]-pos[e[1]][1];
      var d=Math.sqrt(dx*dx+dy*dy)||1e-6;
      var f=d*d/k;
      disp[e[0]][0]-=dx/d*f;disp[e[0]][1]-=dy/d*f;
      disp[e[1]][0]+=dx/d*f;disp[e[1]][1]+=dy/d*f;
    }});
    pos.forEach(function(p,i){{
      var dm=Math.sqrt(disp[i][0]*disp[i][0]+disp[i][1]*disp[i][1])||1e-6;
      var step=Math.min(dm,t);
      p[0]+=disp[i][0]/dm*step;p[1]+=disp[i][1]/dm*step;
    }});
    t*=0.95;
  }}
  var x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  pos.forEach(function(p){{if(p[0]<x0)x0=p[0];if(p[0]>x1)x1=p[0];if(p[1]<y0)y0=p[1];if(p[1]>y1)y1=p[1];}});
  var sx=(x1-x0)||1,sy=(y1-y0)||1;
  return pos.map(function(p){{return [0.06+0.88*(p[0]-x0)/sx,0.06+0.88*(p[1]-y0)/sy];}});
}}
var EDGES, LAY, FACET_HULLS, ALL_FACET_HULLS;
if(HULL.r===2){{
  // True region shape (2D geometry of the region) as the seed; genuine
  // regions can be extreme slivers (2nd singular value down to ~0.006 of the
  // 1st — the region is a thin band), so the map is non-Euclidean: spring
  // layout of the polygon cycle (seed = true shape) keeps it legible.
  var raw2=VERTEX_MU.map(function(v){{return HULL.proj(v);}});
  var x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  raw2.forEach(function(p){{if(p[0]<x0)x0=p[0];if(p[0]>x1)x1=p[0];if(p[1]<y0)y0=p[1];if(p[1]>y1)y1=p[1];}});
  var sx2=(x1-x0)||1,sy2=(y1-y0)||1,pad2=0.06,span2=1-2*pad2;
  var seed2=raw2.map(function(p){{return[pad2+(p[0]-x0)/sx2*span2,pad2+(p[1]-y0)/sy2*span2];}});
  var order2=hull2D(raw2.map(function(p,i){{return i;}}),seed2);
  EDGES=[];for(var k2=0;k2<order2.length;k2++)EDGES.push([order2[k2],order2[(k2+1)%order2.length]]);
  LAY=springLayout(seed2,EDGES);
  var hull2f=hull2D(LAY.map(function(p,i){{return i;}}),LAY);
  FACET_HULLS=[];for(var k3=1;k3<hull2f.length-1;k3++)FACET_HULLS.push([hull2f[0],hull2f[k3],hull2f[k3+1]]);
  ALL_FACET_HULLS=FACET_HULLS;
}}else{{
  // Layout and the full edge set use ALL true edges — a vertex whose edges
  // were filtered to display faces alone would be pushed to the drawing
  // hull by the repulsion, producing non-edge chords on the outline.
  EDGES=ALL_EDGES;
  LAY=springLayout(POLY,EDGES);
  FACET_HULLS=FACET_VERTS.map(function(F){{return hull2D(F,LAY);}});
  ALL_FACET_HULLS=ALL_FACET_VERTS.map(function(F){{return hull2D(F,LAY);}});
}}
function hull2D(idx,pts){{
  var P=pts||LAY;
  var pts2=idx.map(function(i){{return {{i:i,x:P[i][0],y:P[i][1]}};}});
  if(pts2.length<3)return idx.slice();
  pts2.sort(function(a,b){{return a.x!==b.x?a.x-b.x:a.y-b.y;}});
  function cross(o,a,b){{return (a.x-o.x)*(b.y-o.y)-(a.y-o.y)*(b.x-o.x);}}
  var lo=[];
  pts2.forEach(function(p){{
    while(lo.length>=2&&cross(lo[lo.length-2],lo[lo.length-1],p)<=0)lo.pop();
    lo.push(p);
  }});
  var up=[];
  for(var i=pts2.length-1;i>=0;i--){{
    var p=pts2[i];
    while(up.length>=2&&cross(up[up.length-2],up[up.length-1],p)<=0)up.pop();
    up.push(p);
  }}
  lo.pop();up.pop();
  return lo.concat(up).map(function(p){{return p.i;}});
}}
function outerSegments(){{
  // The drawing's outline = 2D hull of the layout; each hull edge is a true
  // polytope edge (verified across all 13 live systems), with collinear
  // intermediate vertices chained so every outline piece is an edge.
  var hull=hull2D(LAY.map(function(p,i){{return i;}}),LAY),segs=[];
  for(var k=0;k<hull.length;k++){{
    var a=hull[k],b=hull[(k+1)%hull.length];
    var ax=LAY[a][0],ay=LAY[a][1],bx=LAY[b][0],by=LAY[b][1];
    var mids=[];
    LAY.forEach(function(l,i){{
      if(i===a||i===b)return;
      var cr=(bx-ax)*(l[1]-ay)-(by-ay)*(l[0]-ax);
      if(Math.abs(cr)>1e-6)return;
      var t=((l[0]-ax)*(bx-ax)+(l[1]-ay)*(by-ay))/((bx-ax)*(bx-ax)+(by-ay)*(by-ay));
      if(t>1e-9&&t<1-1e-9)mids.push([t,i]);
    }});
    mids.sort(function(x,y){{return x[0]-y[0];}});
    var chain=[a];mids.forEach(function(m){{chain.push(m[1]);}});chain.push(b);
    for(var c=0;c<chain.length-1;c++){{
      var p=chain[c],q=chain[c+1];
      var isE=EDGES.some(function(e){{return(e[0]===p&&e[1]===q)||(e[0]===q&&e[1]===p);}});
      segs.push({{i:p,j:q,edge:isE}});
    }}
  }}
  return segs;
}}
var OUTER=outerSegments();
function edgePhases(i,j){{
  var a=VPHASES[i].competing||[],b=VPHASES[j].competing||[];
  return a.filter(function(x){{return b.indexOf(x)>=0;}});
}}
function drawEdgeLabel(i,j){{
  var ph=edgePhases(i,j).join(" · ");
  if(!ph)return;
  var a=layPx(LAY[i]),b=layPx(LAY[j]);
  var mx=(a[0]+b[0])/2,my=(a[1]+b[1])/2;
  var dx=b[0]-a[0],dy=b[1]-a[1],L=Math.sqrt(dx*dx+dy*dy)||1;
  var nx=-dy/L,ny=dx/L;
  var cx=0,cy=0;LAY.forEach(function(l){{cx+=l[0];cy+=l[1];}});cx/=LAY.length;cy/=LAY.length;
  var px=cP.l+cx*(cW-cP.l-cP.r),py=cP.t+cy*(cH-cP.t-cP.b);
  if((mx-px)*nx+(my-py)*ny<0){{nx=-nx;ny=-ny;}}
  var tx=mx+nx*11,ty=my+ny*11;
  cctx.font="600 11px "+FONT_SANS;cctx.textAlign="center";cctx.textBaseline="middle";
  var w=cctx.measureText(ph).width+8;
  cctx.fillStyle="rgba(255,255,255,0.88)";
  cctx.fillRect(tx-w/2,ty-8,w,16);
  cctx.fillStyle="#374151";cctx.fillText(ph,tx,ty+1);
}}
function fitAffine(){{
  var r=HULL.r;
  if(r===0)return {{M:[[0],[0]],o:[0.5,0.5]}};
  var U=VERTEX_MU.map(function(v){{return HULL.proj(v);}});
  var m=r+1,ATA=[],ATL=[[],[]];
  for(var a=0;a<m;a++){{ATA.push([]);for(var b=0;b<m;b++)ATA[a].push(0);}}
  for(var d=0;d<2;d++)for(var a=0;a<m;a++)ATL[d].push(0);
  U.forEach(function(u,i){{
    var row=u.slice();row.push(1);
    for(var a=0;a<m;a++)for(var b=0;b<m;b++)ATA[a][b]+=row[a]*row[b];
    for(var a=0;a<m;a++){{ATL[0][a]+=row[a]*LAY[i][0];ATL[1][a]+=row[a]*LAY[i][1];}}
  }});
  function solve(rhs){{
    var A=ATA.map(function(r){{return r.slice();}}),b=rhs.slice();
    for(var col=0;col<m;col++){{
      var piv=col;
      for(var rr=col+1;rr<m;rr++)if(Math.abs(A[rr][col])>Math.abs(A[piv][col]))piv=rr;
      var t=A[col];A[col]=A[piv];A[piv]=t;t=b[col];b[col]=b[piv];b[piv]=t;
      if(Math.abs(A[col][col])<1e-12)continue;
      for(var rr=col+1;rr<m;rr++){{
        var f=A[rr][col]/A[col][col];
        for(var cc2=col;cc2<m;cc2++)A[rr][cc2]-=f*A[col][cc2];
        b[rr]-=f*b[col];
      }}
    }}
    var x=new Array(m).fill(0);
    for(var rr=m-1;rr>=0;rr--){{
      var s=b[rr];
      for(var cc2=rr+1;cc2<m;cc2++)s-=A[rr][cc2]*x[cc2];
      x[rr]=Math.abs(A[rr][rr])>1e-12?s/A[rr][rr]:0;
    }}
    return x;
  }}
  var b0=solve(ATL[0]),b1=solve(ATL[1]);
  var M=[[],[]];
  for(var k=0;k<r;k++){{M[0].push(b0[k]);M[1].push(b1[k]);}}
  return {{M:M,o:[b0[r],b1[r]]}};
}}
var AFF=fitAffine();
function layPx(p){{return [cP.l+p[0]*(cW-cP.l-cP.r),cP.t+p[1]*(cH-cP.t-cP.b)];}}
function invLay(x,y){{return [(x-cP.l)/(cW-cP.l-cP.r),(y-cP.t)/(cH-cP.t-cP.b)];}}
function faceWeights(pts,u){{
  // Barycentric weights of u inside the convex face polygon pts (r-dim,
  // coplanar), via a fan from pts[0] in a 2D basis of the face's span.
  if(pts.length===2){{
    var den=0,dot=0;
    for(var k=0;k<u.length;k++){{var dd=pts[1][k]-pts[0][k];den+=dd*dd;dot+=(u[k]-pts[0][k])*dd;}}
    var t=den>1e-12?dot/den:-1;
    if(t<-1e-6||t>1+1e-6)return null;
    return [1-t,t];
  }}
  if(pts.length<3)return null;
  var e0=pts[1].map(function(v,k){{return v-pts[0][k];}});
  var b0=Math.sqrt(e0.reduce(function(s,x){{return s+x*x;}},0));
  if(b0<1e-12)return null;
  e0=e0.map(function(x){{return x/b0;}});
  var e1=null;
  for(var i2=2;i2<pts.length;i2++){{
    var w=pts[i2].map(function(v,k){{return v-pts[0][k];}});
    var dot=0;for(var k=0;k<w.length;k++)dot+=w[k]*e0[k];
    for(var k=0;k<w.length;k++)w[k]-=dot*e0[k];
    var nr=Math.sqrt(w.reduce(function(s,x){{return s+x*x;}},0));
    if(nr>1e-9){{e1=w.map(function(x){{return x/nr;}});break;}}
  }}
  if(!e1)return null;
  function to2(p){{var w=p.map(function(v,k){{return v-pts[0][k];}});var d1=0,d2=0;
    for(var k=0;k<w.length;k++){{d1+=w[k]*e0[k];d2+=w[k]*e1[k];}}return[d1,d2];}}
  var q=to2(u),P=pts.map(to2);
  for(var k=1;k<P.length-1;k++){{
    var tb=triBary(P[0],P[k],P[k+1],q[0],q[1]);
    if(tb&&tb[0]>=-1e-9&&tb[1]>=-1e-9&&tb[2]>=-1e-9){{
      var w2=new Array(pts.length).fill(0);
      w2[0]=tb[0];w2[k]=tb[1];w2[k+1]=tb[2];
      // u must lie ON the facet plane (it is a combo of this facet's vertices),
      // not merely inside its 2D projection: drifted near-coplanar facets warp
      // slightly and would otherwise swallow points of neighboring facets.
      var res=0;
      for(var c=0;c<pts[0].length;c++){{
        var s=0;for(var k2=0;k2<pts.length;k2++)s+=w2[k2]*pts[k2][c];
        var dr=u[c]-s;res+=dr*dr;
      }}
      if(Math.sqrt(res)<=0.1*HULL.tol)return w2;
    }}
  }}
  return null;
}}
function insideDrawnHull(p,order){{
  for(var k=1;k<order.length-1;k++){{
    var tb=triBary(layPx(LAY[order[0]]),layPx(LAY[order[k]]),layPx(LAY[order[k+1]]),p[0],p[1]);
    if(tb&&tb[0]>=-1e-9&&tb[1]>=-1e-9&&tb[2]>=-1e-9)return true;
  }}
  return false;
}}
function closestBoundary(p,order){{
  var best=null,bd=Infinity;
  for(var k=0;k<order.length;k++){{
    var a=layPx(LAY[order[k]]),b=layPx(LAY[order[(k+1)%order.length]]);
    var dx=b[0]-a[0],dy=b[1]-a[1],len2=dx*dx+dy*dy;
    var t=len2>1e-9?((p[0]-a[0])*dx+(p[1]-a[1])*dy)/len2:0;
    t=Math.max(0,Math.min(1,t));
    var qx=a[0]+t*dx,qy=a[1]+t*dy;
    var d=(p[0]-qx)*(p[0]-qx)+(p[1]-qy)*(p[1]-qy);
    if(d<bd){{bd=d;best=[qx,qy];}}
  }}
  return best||p;
}}
function markerPos(mu){{
  if(SECTION)return secPos(mu);
  var u=HULL.proj(mu);
  var ins=hullState(mu).inside;
  if(ins){{
    // Exact inverse of pickMu: locate u in the true-space face fan and map
    // the barycentric weights through the drawn polygons. Near-coplanar
    // sliver facets make u match several facets, so pick the LARGEST drawn
    // area (the real facet) — same rule as pickMu — keeping clicks/drags
    // exactly on the cursor.
    var bestW=null,bestA=-1;
    for(var fi=0;fi<FACET_HULLS.length;fi++){{
      var F=FACET_HULLS[fi];
      if(F.length<2)continue;
      var w=faceWeights(F.map(function(vi){{return HULL.proj(VERTEX_MU[vi]);}}),u);
      if(w){{
        var A=drawnArea(F);
        if(A>bestA){{bestA=A;bestW=[F,w];}}
      }}
    }}
    // Gap selections (pickMu's drawn-hull fan): same fan here. A fan mu can
    // lie exactly on a facet plane (silhouette fan triangle coplanar with a
    // facet), so the provenance (pickPath) decides which face to map through.
    var orderG=hull2D(LAY.map(function(pp,i){{return i;}}),LAY);
    var wg=(orderG.length>=3)?faceWeights(orderG.map(function(vi){{return HULL.proj(VERTEX_MU[vi]);}}),u):null;
    if(pickPath==="fan"&&wg){{
      var x=0,y=0;
      orderG.forEach(function(vi,k){{var q=layPx(LAY[vi]);x+=wg[k]*q[0];y+=wg[k]*q[1];}});
      return [x,y];
    }}
    if(bestW){{
      var x=0,y=0;
      bestW[0].forEach(function(vi,k){{var q=layPx(LAY[vi]);x+=bestW[1][k]*q[0];y+=bestW[1][k]*q[1];}});
      return [x,y];
    }}
    // Edge selections: a mu exactly on a true edge whose facets were
    // filtered out maps through the edge's own drawn segment.
    for(var ei=0;ei<ALL_EDGES.length;ei++){{
      var E=ALL_EDGES[ei];
      var we=faceWeights([HULL.proj(VERTEX_MU[E[0]]),HULL.proj(VERTEX_MU[E[1]])],u);
      if(we){{
        var q0=layPx(LAY[E[0]]),q1=layPx(LAY[E[1]]);
        return [q0[0]+we[1]*(q1[0]-q0[0]),q0[1]+we[1]*(q1[1]-q0[1])];
      }}
    }}
    if(wg){{
      var x=0,y=0;
      orderG.forEach(function(vi,k){{var q=layPx(LAY[vi]);x+=wg[k]*q[0];y+=wg[k]*q[1];}});
      return [x,y];
    }}
  }}
  // Outside the region, or an interior point on no face (sliders): affine
  // image clamped to the drawn region — the marker never contradicts the
  // verdict (outside mu sits on the boundary with the red ring + 区域外 card).
  var x=AFF.o[0],y=AFF.o[1];
  if(HULL.r>0){{
    var uu=HULL.proj(mu);
    for(var k=0;k<HULL.r;k++){{x+=AFF.M[0][k]*uu[k];y+=AFF.M[1][k]*uu[k];}}
  }}
  var p=layPx([x,y]);
  var order=hull2D(LAY.map(function(pp,i){{return i;}}),LAY);
  if(!ins)return closestBoundary(p,order);
  if(order.length>=3&&insideDrawnHull(p,order))return p;
  return closestBoundary(p,order);
}}
function drawnArea(F){{
  var a=0;
  for(var k=1;k<F.length-1;k++){{
    var p0=layPx(LAY[F[0]]),p1=layPx(LAY[F[k]]),p2=layPx(LAY[F[k+1]]);
    a+=Math.abs((p1[0]-p0[0])*(p2[1]-p0[1])-(p1[1]-p0[1])*(p2[0]-p0[0]));
  }}
  return a;
}}
function selectedVertex(mu){{
  if(!mu)return -1;
  var p=markerPos(mu),best=-1,dist=Infinity;
  LAY.forEach(function(l,i){{
    var q=layPx(l),dx=q[0]-p[0],dy=q[1]-p[1];
    var d=Math.sqrt(dx*dx+dy*dy);if(d<dist){{dist=d;best=i;}}
  }});
  return dist<=14?best:-1;
}}
function triBary(a,b,c,px,py){{
  var v0x=c[0]-a[0],v0y=c[1]-a[1],v1x=b[0]-a[0],v1y=b[1]-a[1];
  var d00=v0x*v0x+v0y*v0y,d01=v0x*v1x+v0y*v1y,d11=v1x*v1x+v1y*v1y;
  var d20=(px-a[0])*v0x+(py-a[1])*v0y,d21=(px-a[0])*v1x+(py-a[1])*v1y;
  var den=d00*d11-d01*d01;
  if(Math.abs(den)<1e-12)return null;
  var v=(d11*d20-d01*d21)/den,w=(d00*d21-d01*d20)/den;
  // v = weight of c (v0 = c-a), w = weight of b (v1 = b-a)
  return [1-v-w,w,v];
}}
function facetMu(F,px,py){{
  var pts=F.map(function(i){{return layPx(LAY[i]);}});
  if(pts.length<3)return null;
  var w=new Array(F.length).fill(0);
  for(var k=1;k<pts.length-1;k++){{
    var tb=triBary(pts[0],pts[k],pts[k+1],px,py);
    if(tb&&tb[0]>=-1e-9&&tb[1]>=-1e-9&&tb[2]>=-1e-9){{
      w[0]=tb[0];w[k]=tb[1];w[k+1]=tb[2];
      var mu={{}};
      F.forEach(function(vi,kk){{for(var e in VERTEX_MU[vi])mu[e]=(mu[e]||0)+w[kk]*VERTEX_MU[vi][e];}});
      return mu;
    }}
  }}
  return null;
}}
function pickMu(px,py){{
  if(SECTION&&!SECTION.empty){{
    var mu=pickSection(px,py);
    if(mu)return mu;
  }}
  var p=invLay(px,py),scx=cW-cP.l-cP.r,scy=cH-cP.t-cP.b;
  var best=-1,bd=Infinity;
  LAY.forEach(function(l,i){{
    var dx=(l[0]-p[0])*scx,dy=(l[1]-p[1])*scy;
    var d=Math.sqrt(dx*dx+dy*dy);
    if(d<bd){{bd=d;best=i;}}
  }});
  if(bd<=14){{
    pickPath="vertex";
    selectionMode="当前顶点 V"+(best+1);
    return JSON.parse(JSON.stringify(VERTEX_MU[best]));
  }}
  var bestFace=null,bestFaceA=-1;
  for(var fi=0;fi<FACET_HULLS.length;fi++){{
    var mu=facetMu(FACET_HULLS[fi],px,py);
    if(mu){{
      var A=drawnArea(FACET_HULLS[fi]);
      if(A>bestFaceA){{bestFaceA=A;bestFace=mu;}}
    }}
  }}
  if(bestFace){{pickPath="facet";selectionMode="区域内插值";return bestFace;}}
  var bestT=-1,bestE=-1,be=Infinity;
  EDGES.forEach(function(e,ei){{
    var a=layPx(LAY[e[0]]),b=layPx(LAY[e[1]]);
    var dx=b[0]-a[0],dy=b[1]-a[1],len2=dx*dx+dy*dy;
    var t=len2>1e-9?((px-a[0])*dx+(py-a[1])*dy)/len2:0;
    t=Math.max(0,Math.min(1,t));
    var qx=a[0]+t*dx,qy=a[1]+t*dy;
    var d=Math.sqrt((px-qx)*(px-qx)+(py-qy)*(py-qy));
    if(d<be){{be=d;bestT=t;bestE=ei;}}
  }});
  if(bestE>=0&&be<=8){{
    pickPath="edge";
    selectionMode="边界插值";
    var e=EDGES[bestE],va=VERTEX_MU[e[0]],vb=VERTEX_MU[e[1]],mu={{}};
    for(var el in va)mu[el]=va[el]*(1-bestT)+vb[el]*bestT;
    return mu;
  }}
  // Click inside the drawn region but in a gap between drawn facets (r>=3):
  // interpolate within the drawn-hull fan (silhouette vertices) — the mu maps
  // back to exactly this click position, so the marker tracks the cursor.
  var orderH=hull2D(LAY.map(function(l,i){{return i;}}),LAY);
  var inH=orderH.length>=3?insideDrawnHull([px,py],orderH):true;
  if(inH&&orderH.length>=3){{
    for(var kk=1;kk<orderH.length-1;kk++){{
      var tb2=triBary(layPx(LAY[orderH[0]]),layPx(LAY[orderH[kk]]),layPx(LAY[orderH[kk+1]]),px,py);
      if(tb2&&tb2[0]>=-1e-9&&tb2[1]>=-1e-9&&tb2[2]>=-1e-9){{
        var w4=new Array(orderH.length).fill(0);
        w4[0]=tb2[0];w4[kk]=tb2[1];w4[kk+1]=tb2[2];
        var mu4={{}};
        orderH.forEach(function(vi,kk4){{for(var e in VERTEX_MU[vi])mu4[e]=(mu4[e]||0)+w4[kk4]*VERTEX_MU[vi][e];}});
        pickPath="fan";
        selectionMode="区域内插值";
        return mu4;
      }}
    }}
  }}
  if(best>=0){{
    pickPath="vertex";
    selectionMode="当前顶点 V"+(best+1);
    return JSON.parse(JSON.stringify(VERTEX_MU[best]));
  }}
  return null;
}}
"""

    if n_vertices == 1:
        # Static: no CPD map; the single vertex is the only chemical condition
        return common + f"""
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  cctx.fillStyle="#64748b";cctx.font="500 13px "+FONT_SANS;cctx.textAlign="center";
  cctx.fillText("mu fixed at vertex {vertex_names[0]}",cW/2,cH/2);
}}
var curMu=JSON.parse(JSON.stringify(VERTEX_MU[0]));
"""

    return common + f"""
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  if(SECTION){{
    if(SECTION.empty){{
      cctx.fillStyle="#64748b";cctx.font="500 13px "+FONT_SANS;cctx.textAlign="center";
      cctx.fillText("约束下无稳定区",cW/2,cH/2-10);
      var fixDesc=SECTION.fe.map(function(e){{return "μ_"+e+"="+SECTION.cv[e].toFixed(2);}}).join(" · ");
      cctx.font="400 12px "+FONT_SANS;
      cctx.fillText(fixDesc,cW/2,cH/2+12);
      return;
    }}
    if(SECTION.d===2){{
      var ord=SECTION.order;
      cctx.beginPath();
      ord.forEach(function(vi,i){{var p=layPx(SECTION.xy[vi]);i===0?cctx.moveTo(p[0],p[1]):cctx.lineTo(p[0],p[1]);}});
      cctx.closePath();
      cctx.fillStyle="rgba(29,78,216,0.10)";cctx.fill();
      cctx.strokeStyle="#475569";cctx.lineWidth=2;cctx.stroke();
      for(var e2=0;e2<ord.length;e2++){{
        var ph=sectionLabel(e2,(e2+1)%ord.length);
        if(!ph)continue;
        var a=layPx(SECTION.xy[ord[e2]]),b2=layPx(SECTION.xy[ord[(e2+1)%ord.length]]);
        var mx=(a[0]+b2[0])/2,my=(a[1]+b2[1])/2;
        var dx=b2[0]-a[0],dy=b2[1]-a[1],L=Math.sqrt(dx*dx+dy*dy)||1;
        var nx=-dy/L,ny=dx/L;
        var cx=0,cy=0;SECTION.xy.forEach(function(l){{cx+=l[0];cy+=l[1];}});cx/=SECTION.xy.length;cy/=SECTION.xy.length;
        var px0=cP.l+cx*(cW-cP.l-cP.r),py0=cP.t+cy*(cH-cP.t-cP.b);
        if((mx-px0)*nx+(my-py0)*ny<0){{nx=-nx;ny=-ny;}}
        var tx=mx+nx*11,ty=my+ny*11;
        cctx.font="600 11px "+FONT_SANS;cctx.textAlign="center";cctx.textBaseline="middle";
        var w=cctx.measureText(ph).width+8;
        cctx.fillStyle="rgba(255,255,255,0.88)";cctx.fillRect(tx-w/2,ty-8,w,16);
        cctx.fillStyle="#374151";cctx.fillText(ph,tx,ty+1);
      }}
      SECTION.xy.forEach(function(l){{var p=layPx(l);cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(p[0],p[1],3.2,0,2*Math.PI);cctx.fill();}});
    }}else if(SECTION.d===1){{
      var ord=SECTION.order;
      var a=layPx(SECTION.xy[ord[0]]),b2=layPx(SECTION.xy[ord[ord.length-1]]);
      cctx.strokeStyle="#475569";cctx.lineWidth=2;
      cctx.beginPath();cctx.moveTo(a[0],a[1]);cctx.lineTo(b2[0],b2[1]);cctx.stroke();
      var ph=sectionLabel(0,ord.length-1);
      if(ph){{
        cctx.font="600 11px "+FONT_SANS;cctx.textAlign="center";cctx.textBaseline="middle";
        var w=cctx.measureText(ph).width+8;
        cctx.fillStyle="rgba(255,255,255,0.88)";cctx.fillRect((a[0]+b2[0])/2-w/2,(a[1]+b2[1])/2-8,w,16);
        cctx.fillStyle="#374151";cctx.fillText(ph,(a[0]+b2[0])/2,(a[1]+b2[1])/2+1);
      }}
      SECTION.xy.forEach(function(l){{var p=layPx(l);cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(p[0],p[1],3.2,0,2*Math.PI);cctx.fill();}});
    }}else{{
      var p=layPx([0.5,0.5]);
      cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(p[0],p[1],4,0,2*Math.PI);cctx.fill();
    }}
    if(mu){{
      var p=secPos(mu);
      cctx.beginPath();cctx.arc(p[0],p[1],6,0,2*Math.PI);
      cctx.fillStyle="#1d4ed8";cctx.fill();
      cctx.strokeStyle=hullState(mu).inside?"#fff":"#e11d48";
      cctx.lineWidth=2;cctx.stroke();
    }}
    return;
  }}
  if(HULL.r===1){{
    // binary: the stability region IS the segment; label endpoints too
    var a0=layPx(LAY[0]),b0=layPx(LAY[1]);
    cctx.strokeStyle="#475569";cctx.lineWidth=2;
    cctx.beginPath();cctx.moveTo(a0[0],a0[1]);cctx.lineTo(b0[0],b0[1]);cctx.stroke();
    drawEdgeLabel(0,1);
    [0,1].forEach(function(vi){{
      var ph=(VPHASES[vi].competing||[]).join(" · ");
      var p=layPx(LAY[vi]);
      cctx.font="600 11px "+FONT_SANS;cctx.textAlign="center";cctx.textBaseline="middle";
      var w=cctx.measureText(ph).width+8;
      var tx=p[0],ty=p[1]+(vi===0?-16:16);
      cctx.fillStyle="rgba(255,255,255,0.88)";cctx.fillRect(tx-w/2,ty-8,w,16);
      cctx.fillStyle="#374151";cctx.fillText(ph,tx,ty+1);
    }});
  }}else if(HULL.r===2){{
    // exact 2D region shape: filled polygon, labeled boundary edges
    var order2=hull2D(LAY.map(function(p,i){{return i;}}),LAY);
    cctx.beginPath();
    order2.forEach(function(vi,i){{var p=layPx(LAY[vi]);i===0?cctx.moveTo(p[0],p[1]):cctx.lineTo(p[0],p[1]);}});
    cctx.closePath();
    cctx.fillStyle="rgba(29,78,216,0.10)";cctx.fill();
    cctx.strokeStyle="#475569";cctx.lineWidth=2;cctx.stroke();
    EDGES.forEach(function(e){{drawEdgeLabel(e[0],e[1]);}});
  }}else{{
    ALL_FACET_HULLS.forEach(function(F){{
      if(F.length<3)return;
      cctx.beginPath();
      F.forEach(function(vi,i){{
        var p=layPx(LAY[vi]);
        i===0?cctx.moveTo(p[0],p[1]):cctx.lineTo(p[0],p[1]);
      }});
      cctx.closePath();
      cctx.fillStyle="rgba(29,78,216,0.07)";
      cctx.fill();
    }});
    cctx.strokeStyle="#d8e0ea";cctx.lineWidth=1;
    EDGES.forEach(function(e){{
      var a=layPx(LAY[e[0]]),b=layPx(LAY[e[1]]);
      cctx.beginPath();cctx.moveTo(a[0],a[1]);cctx.lineTo(b[0],b[1]);cctx.stroke();
    }});
    // The outline = the drawn region's boundary. True polytope edges get the
    // bold stroke + competing-phase label (endpoint intersection; empty only
    // while the live batch's vertex phases are mid-drift). A rare layout
    // chord (non-edge outline segment, e.g. 8-vertex BaAl2B2O7) stays light
    // and unlabeled — it is a drawing artifact, not a phase boundary.
    OUTER.forEach(function(s){{
      var a=layPx(LAY[s.i]),b=layPx(LAY[s.j]);
      cctx.strokeStyle=s.edge?"#475569":"#94a3b8";
      cctx.lineWidth=s.edge?2:1.5;
      cctx.beginPath();cctx.moveTo(a[0],a[1]);cctx.lineTo(b[0],b[1]);cctx.stroke();
      if(s.edge)drawEdgeLabel(s.i,s.j);
    }});
  }}
  LAY.forEach(function(l,i){{
    var p=layPx(l);
    cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(p[0],p[1],3.2,0,2*Math.PI);cctx.fill();
  }});
  if(mu){{
    var p=markerPos(mu);
    cctx.beginPath();cctx.arc(p[0],p[1],6,0,2*Math.PI);
    cctx.fillStyle="#1d4ed8";cctx.fill();
    cctx.strokeStyle=hullState(mu).inside?"#fff":"#e11d48";
    cctx.lineWidth=2;cctx.stroke();
  }}
}}
function ptrPos(e){{var r=cc.getBoundingClientRect();return[e.clientX-r.left,e.clientY-r.top];}}
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var p=ptrPos(e);var mu=pickMu(p[0],p[1]);if(mu)update(mu);}});
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var p=ptrPos(e);var mu=pickMu(p[0],p[1]);if(mu)update(mu);}});
var curMu={{}};
VERTEX_MU.forEach(function(vm){{for(var e in vm)if(vm[e]!==undefined)curMu[e]=(curMu[e]||0)+vm[e]/VERTEX_MU.length;}});
"""

# ═════════════════════════════════════════════════════════════════════
# HTML page template
# ═════════════════════════════════════════════════════════════════════

_COMMON_HTML_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Defect thermodynamics</title>
<style>
/* ── Design tokens (duplicated from crisp webui light theme) ────────
 * Source: crisp/frontend/src/styles/tokens.css :root[data-theme="light"].
 * Self-contained: no external stylesheet fetch — the report must render
 * identically when served through the webui iframe and when the HTML file
 * is opened from disk. Update both places in lockstep.
 * ─────────────────────────────────────────────────────────────────── */
:root{{
  --surface-base:#f7f8fa;--surface-raised:#fff;--surface-sunken:#eef0f4;
  --surface-divider:#d6dae3;
  --text-primary:#0f172a;--text-secondary:#475569;--text-muted:#64748b;
  --text-inverse:#fff;
  --accent-primary:#1d4ed8;--accent-primary-hover:#1e40af;
  --accent-soft:#dbeafe;
  --tone-info-text:#1d4ed8;--tone-info-soft:#dbeafe;
  --radius-sm:4px;--radius-md:8px;--radius-lg:12px;
  --space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;
  --space-5:20px;--space-6:24px;
  --shadow-sm:0 1px 2px rgba(15,23,42,.06);
  --shadow-md:0 4px 12px rgba(15,23,42,.08);
  --shadow-lg:0 12px 32px rgba(15,23,42,.12);
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Inter",
    "Helvetica Neue",Arial,"Noto Sans",sans-serif;
  --font-mono:"JetBrains Mono","Fira Code",ui-monospace,SFMono-Regular,
    Consolas,"Liberation Mono",Menlo,monospace;
  --fs-xs:.75rem;--fs-sm:.875rem;--fs-base:1rem;
  --fs-lg:1.125rem;--fs-xl:1.25rem;
  --fw-regular:400;--fw-medium:500;--fw-semibold:600;--fw-bold:700;
  --lh-tight:1.2;--lh-normal:1.5
}}
/* Internal aliases — so existing class rules and canvas-color constants
 * below keep reading familiar names while inheriting the webui values. */
:root{{
  --ink:var(--text-primary);--muted:var(--text-muted);
  --line:var(--surface-divider);--grid:#e9edf2;
  --card:var(--surface-raised);--canvas:var(--surface-base);
  --accent:var(--accent-primary)
}}

*{{box-sizing:border-box}}
html,body{{min-height:100%;margin:0}}
body{{
  font-family:var(--font-sans);font-size:var(--fs-base);
  line-height:var(--lh-normal);color:var(--text-primary);
  background:var(--surface-base);
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
  padding:var(--space-5)
}}
h2{{margin:0;font-size:var(--fs-xl);font-weight:var(--fw-semibold);line-height:var(--lh-tight);letter-spacing:-.01em}}
h3{{font-size:var(--fs-sm);font-weight:var(--fw-semibold);line-height:var(--lh-tight)}}

.report-head{{display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-3);margin:0 0 var(--space-4)}}
.report-kicker{{font-size:var(--fs-xs);font-weight:var(--fw-bold);letter-spacing:.08em;text-transform:uppercase;color:var(--accent-primary)}}

.report-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-5);align-items:start;position:relative;max-width:1240px;margin:0 auto}}
.report-card{{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);overflow:hidden}}
.report-card__head{{display:flex;align-items:baseline;justify-content:space-between;gap:var(--space-2);padding:var(--space-3) var(--space-4);border-bottom:1px solid var(--line)}}
.report-card__hint{{font-size:var(--fs-xs);color:var(--text-muted)}}
.report-card__body{{padding:var(--space-4)}}

.cpd-card__body{{display:flex;flex-direction:column;align-items:center;gap:var(--space-3)}}
canvas{{display:block;background:var(--canvas);border:1px solid var(--line);border-radius:var(--radius-md)}}

.selection-card{{width:100%;border:1px solid var(--line);border-radius:var(--radius-md);background:var(--surface-sunken);padding:var(--space-3)}}
.selection-card__head{{display:flex;justify-content:space-between;gap:var(--space-2);align-items:baseline;margin-bottom:var(--space-2)}}
.selection-card__title{{font-size:var(--fs-sm);font-weight:var(--fw-bold);color:var(--text-primary)}}
.selection-card__state{{font-size:var(--fs-xs);color:var(--accent-primary);font-weight:var(--fw-semibold)}}
.selection-card__constraints{{font-size:var(--fs-sm);line-height:var(--lh-normal);color:var(--text-secondary);min-height:18px}}
.selection-card__mu{{margin:var(--space-2) 0;font-family:var(--font-mono);font-size:var(--fs-xs);line-height:var(--lh-normal);color:var(--text-secondary);word-break:break-word;font-variant-numeric:tabular-nums}}

.mupanel{{font-size:var(--fs-sm);color:var(--text-secondary)}}
.mupanel-title{{font-size:var(--fs-xs);font-weight:var(--fw-bold);color:var(--text-muted);margin:var(--space-1) 0 var(--space-2);letter-spacing:.04em;text-transform:uppercase}}
.murow{{display:grid;grid-template-columns:30px 42px 1fr 42px 34px;gap:var(--space-2);align-items:center;margin:3px 0}}
.muel{{font-weight:var(--fw-bold);color:var(--text-primary)}}
.mumin,.mumax{{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-muted);text-align:right;font-variant-numeric:tabular-nums}}
.muslider{{width:100%;height:16px;accent-color:var(--accent-primary);margin:0;cursor:pointer;transition:opacity .15s ease}}
.muslider:hover{{opacity:.85}}
.muslider:active{{opacity:1}}
.mufix{{font-size:var(--fs-xs);line-height:1;padding:var(--space-1) 0;border:1px solid var(--line);border-radius:var(--radius-sm);background:var(--card);color:var(--text-muted);cursor:pointer;font-weight:var(--fw-bold);transition:all .12s ease}}
.mufix:hover{{border-color:var(--accent-primary);color:var(--accent-primary)}}
.mufix:focus-visible{{outline:none;box-shadow:var(--focus-ring)}}
.mufix.on{{background:var(--accent-primary);border-color:var(--accent-primary);color:var(--text-inverse)}}
.mufix.on:hover{{background:var(--accent-primary-hover)}}
.muslider:disabled{{opacity:.45;cursor:not-allowed}}

.fe-workspace{{display:block}}
.fe-plot{{min-width:0;position:relative}}

.fe-tip{{position:absolute;z-index:40;display:none;overflow-y:auto;background:var(--card);border:1px solid var(--line);border-radius:var(--radius-md);box-shadow:var(--shadow-lg);padding:var(--space-2) var(--space-3);font-size:var(--fs-sm);animation:feTipFadeIn .15s ease-out}}
@keyframes feTipFadeIn{{from{{opacity:0;transform:translateY(-4px)}}to{{opacity:1;transform:translateY(0)}}}}
.fe-tip__head{{font-size:var(--fs-xs);font-weight:var(--fw-bold);color:var(--accent-primary);margin-bottom:var(--space-1);text-transform:uppercase;letter-spacing:.04em}}
.fe-tip__foot{{font-size:var(--fs-xs);color:var(--text-muted);margin-top:var(--space-1)}}
.fe-tip .row{{display:grid;grid-template-columns:10px minmax(0,1fr) auto auto;gap:var(--space-2);align-items:center;padding:var(--space-1);border-bottom:1px solid var(--surface-sunken);font-size:var(--fs-sm)}}
.fe-tip .row:last-child{{border-bottom:0}}
.fe-tip .swatch{{width:8px;height:8px;border-radius:50%}}
.fe-tip .tnamebox{{display:flex;align-items:center;gap:var(--space-2);min-width:0}}
.fe-tip .tname{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.fe-tip .tspin{{font-family:var(--font-mono);font-size:var(--fs-xs);color:var(--text-muted);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}}
.fe-tip .tenergy{{font-family:var(--font-mono);font-size:var(--fs-sm);color:var(--text-secondary);font-variant-numeric:tabular-nums}}

.fe-note{{font-size:var(--fs-xs);color:var(--text-muted);margin-top:var(--space-2);line-height:var(--lh-normal)}}

.leg{{display:flex;flex-wrap:wrap;gap:var(--space-1);margin-top:var(--space-3)}}
.leg-group{{display:flex;flex-wrap:wrap;gap:var(--space-1);min-width:0;margin:var(--space-1) 0;padding:var(--space-1);border-left:2px solid var(--surface-divider)}}
.leg>div{{display:flex;align-items:center;gap:3px;font-size:var(--fs-sm);cursor:pointer;padding:2px var(--space-1);border-radius:var(--radius-sm);transition:background .12s ease}}
.leg>div:hover{{background:var(--surface-sunken)}}
.leg-cat{{font-size:var(--fs-xs)!important;font-weight:var(--fw-bold);color:var(--accent-primary);flex-basis:100%;text-transform:uppercase;letter-spacing:.04em}}
.leg-cat:hover{{background:var(--accent-soft)!important}}

.csub{{font-size:.72em;vertical-align:sub}}
.csup{{font-size:.72em;vertical-align:super}}

@media(max-width:800px){{.report-grid{{grid-template-columns:1fr}}}}
@media(max-width:560px){{body{{padding:var(--space-3)}}.report-head{{align-items:flex-start;flex-direction:column}}.report-card__body{{padding:var(--space-3)}}}}
</style></head><body>
<header class="report-head"><h2>{title_html}</h2><span class="report-kicker">Defect thermodynamics</span></header>
<main class="report-grid">
<section class="report-card" id="cpdCard"><header class="report-card__head"><h3>化学势稳定区</h3><span class="report-card__hint">拖动或点击选择化学条件</span></header><div class="report-card__body cpd-card__body">
<canvas id="cpd" width="600" height="450"></canvas>
<section class="selection-card" aria-live="polite"><div class="selection-card__head"><span class="selection-card__title">当前化学条件</span><span id="selection-state" class="selection-card__state">区域内插值</span></div><div id="selection-constraints" class="selection-card__constraints"></div><div id="selection-mu" class="selection-card__mu"></div><div class="mupanel"><div class="mupanel-title">化学势 μ (eV) · 拖动滑块逐元素调节 · 点「固定」约束该元素并绘制截面</div><div id="murows"></div></div></section>
</div></section>
<section class="report-card" id="feCard"><header class="report-card__head"><h3>缺陷形成能</h3><span class="report-card__hint">移动查询 E<sub>F</sub></span></header><div class="report-card__body"><div class="fe-workspace"><div class="fe-plot"><canvas id="cv" width="600" height="450"></canvas><div class="leg" id="leg"></div><div class="fe-note">查询层按 E<sub>f</sub> 降序列出当前可见缺陷 · 本征缺陷 · 1000 K · 未含自由载流子</div></div></div></div></section>
<section class="report-card" id="structureCard" style="grid-column: 1 / -1;"><header class="report-card__head"><h3>缺陷局域结构</h3><span class="report-card__hint">选择缺陷查看 3D 结构</span></header><div class="report-card__body">
<div id="structure-viewer" style="width:100%;height:500px;position:relative;">
<div id="viewer3d" style="width:100%;height:100%;"></div>
<div id="atom-info" style="position:absolute;top:10px;right:10px;background:rgba(255,255,255,0.95);padding:10px;border-radius:8px;display:none;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,0.1);"></div>
</div>
<div style="margin-top:10px;display:flex;gap:20px;align-items:center;">
<div style="flex:1;"><label style="font-size:12px;font-weight:600;color:var(--text-secondary);">近邻距离 cutoff:</label>
<input type="range" id="cutoff-slider" min="2" max="10" value="5" step="0.5" style="width:100%;margin-top:5px;">
<span id="cutoff-value" style="font-size:12px;color:var(--text-muted);">5.0 Å</span></div>
<div id="defect-selector" style="flex:1;"><label style="font-size:12px;font-weight:600;color:var(--text-secondary);">选择缺陷:</label>
<select id="defect-select" style="width:100%;margin-top:5px;padding:6px;border:1px solid var(--line);border-radius:4px;font-size:13px;"></select></div>
</div>
</div></section>
<div id="tip" class="fe-tip"></div>
</main>
<script src="https://3Dmol.org/build/3Dmol-min.js"></script>
<script>"""

_COMMON_JS_DECLS = """var DEF = {def_json};
var REF = {ref_json};
var CL = {colors_json};
var BG = {bg};
var names = {names_json};
var DISP = {disp_json};
var MAG = {mag_json};
var VOX = {vox_json};
var STRUCTURES = {structures_json};
var nEF = 200;
var hidden = {{}}; names.forEach(function(n){{hidden[n]=false;}});
"""

_FE_CANVAS_JS = """
var cv=document.getElementById("cv"), cx=cv.getContext("2d");
var W=600, H=450, P={l:64,r:20,t:24,b:52};
/* ── HiDPI: same pattern as CPD canvas ── */
var dpr = window.devicePixelRatio || 1;
cv.width = W * dpr; cv.height = H * dpr;
cv.style.width = W + "px"; cv.style.height = H + "px";
cx.scale(dpr, dpr);
var minY=-10, maxY=10;
var cursorEF=null;

function xPx(v){return P.l+(v/BG)*(W-P.l-P.r);}
function yPx(v){return P.t+(1-(v-minY)/(maxY-minY))*(H-P.t-P.b);}
function xInv(x){return (x-P.l)/(W-P.l-P.r)*BG;}

// The y extent is computed ONCE over the per-element slider box and stays
// fixed: the box's CORNERS (formation energy is linear in μ, so its
// extremes over the box sit at corners — 2^N of them) plus each charge
// state at the E_F endpoints. Any slider position therefore draws inside
// the frame; the lower bound IS the lowest formation energy of the box (no
// padding below); the top keeps a 10% pad, hard-capped at +10 eV (higher
// lines clip at the canvas top). Selection drags and legend toggles never
// rescale the frame. The docked readout is unaffected — it lists energies
// as computed, regardless of the visible axis range.
function boxCorners(){
  var el=[],mins={},maxs={};
  VERTEX_MU.forEach(function(vm){for(var e in vm)if(el.indexOf(e)<0)el.push(e);});
  el.forEach(function(e){
    var mn=Infinity,mx=-Infinity;
    VERTEX_MU.forEach(function(vm){var v=vm[e];if(v===undefined)return;if(v<mn)mn=v;if(v>mx)mx=v;});
    mins[e]=mn;maxs[e]=mx;
  });
  var corners=[{}];
  el.forEach(function(e){
    var next=[];
    corners.forEach(function(c){
      var a={},b={};for(var k in c){a[k]=c[k];b[k]=c[k];}
      a[e]=mins[e];b[e]=maxs[e];next.push(a,b);
    });
    corners=next;
  });
  return corners;
}
function calcGlobalYRange(){
  var allE=[];
  boxCorners().forEach(function(mu){
    names.forEach(function(n){
      var ms=0,d=DEF[n];
      for(var e in d.delta) if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];
      d.charges.forEach(function(c){allE.push(c.e0+ms);allE.push(c.e0+c.q*BG+ms);});
    });
  });
  if(allE.length===0){minY=-2;maxY=6;return;}
  var lo=Math.min.apply(null,allE),hi=Math.max.apply(null,allE),pad=Math.max(.5,(hi-lo)*.1);
  minY=lo;maxY=Math.ceil(hi+pad);
  if(maxY>10)maxY=10;
  if(minY>=maxY)minY=maxY-5;
}
calcGlobalYRange();

function calcE(name,mu,eF){
  var d=DEF[name],me=Infinity,ms=0;
  for(var e in d.delta) if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];
  d.charges.forEach(function(c){var v=c.e0+c.q*eF+ms;if(v<me)me=v;});
  return me;
}

function drawFE(mu){
  cx.clearRect(0,0,W,H);
  cx.lineJoin="round";cx.lineCap="round";
  cx.strokeStyle="#e9edf2";cx.lineWidth=1;cx.fillStyle="#64748b";cx.font="500 13px "+FONT_SANS;
  for(var i=0;i<=5;i++){
    var x=xPx(i*BG/5);cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,H-P.b);cx.stroke();
    cx.textAlign="center";cx.fillText((i*BG/5).toFixed(1),x,H-P.b+18);
    cx.beginPath();cx.moveTo(x,H-P.b);cx.lineTo(x,H-P.b+4);cx.stroke();
  }
  var step=(maxY-minY)/8;
  for(var j=0;j<=8;j++){
    var y=yPx(minY+j*step);cx.beginPath();cx.moveTo(P.l,y);cx.lineTo(W-P.r,y);cx.stroke();
    cx.textAlign="right";cx.fillText((minY+j*step).toFixed(1),P.l-7,y+4);
    cx.beginPath();cx.moveTo(P.l,y);cx.lineTo(P.l-4,y);cx.stroke();
  }
  cx.strokeStyle="#94a3b8";cx.lineWidth=1.5;cx.beginPath();cx.moveTo(P.l,P.t);cx.lineTo(P.l,H-P.b);cx.lineTo(W-P.r,H-P.b);cx.stroke();
  cx.fillStyle="#475569";cx.textAlign="center";cx.font="500 13px "+FONT_SANS;cx.fillText("E − E_VBM (eV)",W/2,H-5);
  cx.save();cx.translate(14,H/2);cx.rotate(-Math.PI/2);cx.fillText("Formation energy E_f (eV)",0,0);cx.restore();

  names.forEach(function(n,i){
    if(hidden[n])return;
    cx.globalAlpha=.76;cx.strokeStyle=CL[i];cx.lineWidth=1.65;
    var isDoped={is_doped_str};cx.setLineDash(isDoped?[6,3]:[]);
    cx.beginPath();var first=true;
    for(var k=0;k<nEF;k++){
      var ef=k*BG/(nEF-1),e=calcE(n,mu,ef),x=xPx(ef),y=yPx(e);
      if(isNaN(y)||y<P.t||y>H-P.b)continue;
      if(first){cx.moveTo(x,y);first=false;}else cx.lineTo(x,y);
    }
    cx.stroke();cx.setLineDash([]);cx.globalAlpha=1;
  });
  if(cursorEF!==null){
    cx.strokeStyle="rgba(29,78,216,.5)";cx.lineWidth=1;cx.setLineDash([4,4]);
    cx.beginPath();cx.moveTo(xPx(cursorEF),P.t);cx.lineTo(xPx(cursorEF),H-P.b);cx.stroke();cx.setLineDash([]);
  }
  drawFermi(mu);
}
"""

_FERMI_JS = """
// Intrinsic-defect charge-neutrality: only defects whose name does NOT
// start with an exogenous (dopant) element prefix enter the balance.
var EXO = {exo_json};
var KT = 0.0862;
function isIntrinsic(n){{
  for(var i=0;i<EXO.length;i++) if(n.indexOf(EXO[i]+"_")===0) return false;
  return true;
}}
function calcFermi(mu){{
  var best=null, bestQ=Infinity;
  for(var j=0;j<=400;j++){{
    var ef=j*BG/400;
    var qs=0, ws=0;
    names.forEach(function(n){{
      // Display hiding (legend click) must NOT change the physics:
      // the charge-neutrality level uses ALL intrinsic defects.
      if(!isIntrinsic(n)) return;
      var d=DEF[n], ms=0;
      for(var e in d.delta) if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];
      d.charges.forEach(function(c){{
        var E=c.e0+c.q*ef+ms;
        var w=Math.exp(-Math.max(E,-8)/KT);
        qs+=c.q*w; ws+=w;
      }});
    }});
    var qn=(ws>0)?qs/ws:0;
    if(Math.abs(qn)<Math.abs(bestQ)){{bestQ=qn;best=ef;}}
  }}
  return best;
}}
function drawFermi(mu){{
  var ef=calcFermi(mu);
  if(ef===null) return;
  var x=xPx(ef),label="E_F="+ef.toFixed(2)+" eV (电荷中性)";
  cx.strokeStyle="#1d4ed8";cx.lineWidth=2;cx.setLineDash([]);
  cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,H-P.b);cx.stroke();
  cx.fillStyle="#1d4ed8";cx.font="600 14px "+FONT_SANS;
  // Anchor to the plot interior. Near the right edge, paint leftward so the
  // physical E_F annotation is never clipped by the canvas boundary.
  var gap=6,w=cx.measureText(label).width;
  if(x+gap+w>W-P.r){{cx.textAlign="right";cx.fillText(label,x-gap,P.t+14);}}
  else{{cx.textAlign="left";cx.fillText(label,x+gap,P.t+14);}}
}}
"""

_COMMON_JS_FOOTER = """
function segHtml(segs){
  var h="";
  segs.forEach(function(seg){
    if(seg[0]==="s")h+="<span class='csub'>"+seg[1]+"</span>";
    else if(seg[0]==="p")h+="<span class='csup'>"+seg[1]+"</span>";
    else h+=seg[1];
  });
  return h;
}

var tip=document.getElementById("tip");
// Stable charge state at the cursor Fermi level: the argmin over charges
// (calcE's minimum). The docked readout shows its magnetization.
function calcRow(name,mu,eF){
  var d=DEF[name],me=Infinity,mq=null,ms=0;
  for(var e in d.delta) if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];
  d.charges.forEach(function(c){var v=c.e0+c.q*eF+ms;if(v<me){me=v;mq=c.q;}});
  return {e:me,q:mq};
}
function muLabel(name,q){
  var mq=MAG[name];
  if(mq===undefined||mq[q]===undefined)return "—";
  return Math.abs(mq[q]).toFixed(2);
}
// The defect ion's oxidation state at the cursor's Fermi level (价态):
// substitution X_Yn → X^(h+q) with h the host-site valence inferred from
// the host formula; interstitial X_iN → X^q (charge conservation); vacancy
// Va_Xn → q itself. Chemical notation: sign AFTER the magnitude (5+, 2-).
// It flips when the cursor crosses a charge transition.
function qLabel(q){return q===0?"0":(q>0?q+"+":(-q)+"-");}
function ionLabel(name,q){
  var t=VOX[name];
  if(!t)return "?";
  if(t.v)return qLabel(q);
  var ox=t.h===undefined?q:t.h+q;
  return t.p+"<span class='csup'>"+qLabel(ox)+"</span>";
}
function rowHtml(r){
  return "<div class='row'><span class='swatch' style='background:"+CL[r.idx]+"'></span>"+
    "<span class='tnamebox'><span class='tname'>"+segHtml(DISP[r.name])+"</span></span>"+
    "<span class='tspin'>("+ionLabel(r.name,r.q)+", "+muLabel(r.name,r.q)+")</span>"+
    "<span class='tenergy'>"+(r.e>=0?"+":"")+r.e.toFixed(3)+" eV</span></div>";
}
function fillTip(ef){
  var rows=[];
  names.forEach(function(n,i){if(!hidden[n]){var cr=calcRow(n,curMu,ef);rows.push({name:n,idx:i,e:cr.e,q:cr.q});}});
  rows.sort(function(a,b){return b.e-a.e;});
  var h="<div class='fe-tip__head'>E_F = "+ef.toFixed(3)+" eV · 最高在前</div>";
  rows.forEach(function(r){h+=rowHtml(r);});
  h+="<div class='fe-tip__foot'>共 "+rows.length+" 条 · 本征缺陷 · 1000 K · 未含自由载流子 · 滚轮翻页</div>";
  tip.innerHTML=h;
  sizeTip();
}
// The readout docks over the CPD card — entirely outside the formation-energy
// chart — so inspecting the chart never covers the data itself. It is a
// content-adaptive panel right-aligned inside the CPD card: the CPD canvas's
// left, larger part stays visible and draggable, while the panel carries the
// list. Width follows the longest visible row (incl. head/foot lines), clamped
// to [READOUT_W_MIN, READOUT_W_MAX]; height follows the rows but never exceeds
// the viewport — beyond the cap the panel scrolls (wheel is forwarded from the
// FE chart). Sizing is display state only: calcFermi/hidden are untouched.
var READOUT_W_MIN = 240, READOUT_W_MAX = 320, READOUT_INSET = 10, READOUT_GAP = 10, READOUT_MIN_H = 120;
function sizeTip(){
  if(tip.style.display!=="block")return;
  var c=document.getElementById("cpdCard");
  var canv=document.getElementById("cpd");
  // Measure the rendered content in the same synchronous block the caller
  // runs in: the browser cannot paint between measurement and positioning.
  var w=Math.min(READOUT_W_MAX,Math.max(READOUT_W_MIN,tip.offsetWidth));
  tip.style.width=w+"px";
  tip.style.left=(c.offsetLeft + c.clientWidth - w - READOUT_INSET)+"px";
  var maxH=window.innerHeight - tip.getBoundingClientRect().top - READOUT_GAP;
  // The readout never extends below the CPD canvas: the per-element
  // slider panel below it stays fully visible and draggable.
  var canvBottom=canv.offsetTop+canv.clientHeight-tip.offsetTop;
  if(canvBottom<maxH)maxH=canvBottom;
  if(maxH<READOUT_MIN_H)maxH=READOUT_MIN_H;
  tip.style.maxHeight=maxH+"px";
}
function dockTip(ef){
  var c=document.getElementById("cpdCard");
  var canv=document.getElementById("cpd");
  fillTip(ef);
  tip.style.display="block";
  tip.style.top=(canv.offsetTop+2)+"px";
  sizeTip();
}
function undockTip(){tip.style.display="none";}
var hoverCapable=window.matchMedia("(hover:hover)").matches;
var plotEl=cv.parentElement;
plotEl.addEventListener("mousemove",function(ev){
  if(!hoverCapable)return;
  var r=cv.getBoundingClientRect();
  if(ev.clientX<r.left||ev.clientX>r.right||ev.clientY<r.top||ev.clientY>r.bottom)return;
  var ef=xInv(ev.clientX-r.left);
  if(ef<0||ef>BG)return;
  cursorEF=ef;
  if(curMu)drawFE(curMu);
  dockTip(ef);
});
plotEl.addEventListener("mouseleave",function(){
  // Browsers synthesize a compat mouseleave after every tap; on touch-only
  // devices that must not kill the tapped-on readout — the tap toggles it.
  if(!hoverCapable)return;
  cursorEF=null;if(curMu)drawFE(curMu);undockTip();
});
// The readout is docked on the CPD card: the pointer can't reach it without
// leaving the FE chart (which hides it). Scrolling therefore happens by
// wheeling over the chart — the wheel is forwarded to the panel.
plotEl.addEventListener("wheel",function(ev){
  if(tip.style.display!=="block")return;
  tip.scrollTop+=ev.deltaY;
  if(ev.preventDefault)ev.preventDefault();
},{passive:false});
// Touch / non-hover devices: tapping the FE chart docks/undocks the readout.
if(!hoverCapable){
  cv.addEventListener("click",function(ev){
    var r=cv.getBoundingClientRect(),ef=xInv(ev.clientX-r.left);
    if(ef<0||ef>BG)return;
    cursorEF=ef;if(curMu)drawFE(curMu);
    if(tip.style.display==="block"){undockTip();}
    else{dockTip(ef);}
  });
}

// Chemical-potential panel: per-element sliders over the vertex brackets.
// Dragging a slider sets that element's μ freely (the selection may leave
// the stability region — the canvas labels it 区域外); dragging the CPD
// canvas re-constrains the selection to the polygon and re-syncs sliders.
function buildMuPanel(){
  var box=document.getElementById("murows");
  var elems=[];VERTEX_MU.forEach(function(vm){for(var e in vm)if(elems.indexOf(e)<0)elems.push(e);});
  elems.sort();
  var rows={};
  elems.forEach(function(e){
    var mn=Infinity,mx=-Infinity;
    VERTEX_MU.forEach(function(vm){var v=vm[e];if(v<mn)mn=v;if(v>mx)mx=v;});
    var init=(curMu&&curMu[e]!==undefined)?curMu[e]:mn;
    var row=document.createElement("div");row.className="murow";
    row.innerHTML="<span class='muel'>"+e+"</span>"+
      "<span class='mumin'>"+mn.toFixed(2)+"</span>"+
      "<input type='range' class='muslider' min='"+mn.toFixed(4)+"' max='"+mx.toFixed(4)+"' step='0.001' value='"+init.toFixed(4)+"'>"+
      "<span class='mumax'>"+mx.toFixed(2)+"</span>"+
      "<button type='button' class='mufix' title='固定该元素化学势：绘制固定约束下的截面'>固定</button>";
    box.appendChild(row);
    var slider=row.querySelector(".muslider");
    var fix=row.querySelector(".mufix");
    slider.addEventListener("input",function(){
      pickPath="slider";
      curMu[e]=parseFloat(slider.value);
      selectionMode="逐元素";
      update(curMu);
    });
    fix.addEventListener("click",function(){
      if(FIXED[e]!==undefined&&FIXED[e]!==null){
        FIXED[e]=null;fix.classList.remove("on");fix.textContent="固定";slider.disabled=false;
      }else{
        FIXED[e]=curMu[e]!==undefined?curMu[e]:mn;fix.classList.add("on");fix.textContent="固定✓";slider.disabled=true;
      }
      SECTION=buildSection();
      pickPath="slider";
      update(curMu);
    });
    rows[e]={mn:mn,mx:mx,slider:slider,fix:fix};
  });
  return rows;
}
var muRows=buildMuPanel();
function updateMuPanel(mu){
  for(var e in muRows){
    var r=muRows[e],v=mu[e];if(v===undefined)continue;
    var fixed=FIXED[e]!==undefined&&FIXED[e]!==null;
    if(fixed!==r.slider.disabled){
      r.slider.disabled=fixed;
      if(fixed){r.fix.classList.add("on");r.fix.textContent="固定✓";}else{r.fix.classList.remove("on");r.fix.textContent="固定";}
    }
    r.slider.value=Math.max(r.mn,Math.min(r.mx,v));
  }
}
function updateSelectionCard(mu){
  var idx=selectedVertex(mu),state=document.getElementById("selection-state"),constraints=document.getElementById("selection-constraints");
  var hs=hullState(mu);
  var outside=selectionMode==="逐元素"&&!hs.inside;
  if(idx>=0&&!outside){
    var phases=(VPHASES[idx].competing||[]).join(" · ");
    state.textContent="当前顶点 V"+(idx+1);
    constraints.textContent="V"+(idx+1)+(phases?" · "+phases+"（约束）":" · 无相约束记录");
  }else if(outside){
    state.textContent="区域外";
    constraints.textContent="逐元素自由调节超出稳定区 "+(-hs.dist).toFixed(2)+" eV（亚稳/非平衡参考）；拖回 CPD 图内恢复稳定区选择";
  }else{
    state.textContent=selectionMode;
    constraints.textContent=selectionMode==="逐元素"
      ?"逐元素自由调节；当前点在稳定区内 · 距边界 +"+hs.dist.toFixed(2)+" eV"
      :(selectionMode==="边界插值"?"沿稳定区边界插值；无单一顶点约束":"稳定区内部插值；无单一顶点约束");
  }
  if(SECTION){
    var fixDesc=SECTION.fe.map(function(e){return "μ_"+e+" = "+SECTION.cv[e].toFixed(4)+" eV（固定）";}).join(" · ");
    constraints.textContent=fixDesc+" · 截面维数 "+SECTION.d+(SECTION.empty?" · 约束下无稳定区":"");
  }
  var entries=[];Object.keys(mu).sort().forEach(function(k){entries.push("μ_"+k+" = "+mu[k].toFixed(4)+" eV");});
  document.getElementById("selection-mu").textContent=entries.join(" · ");
}
function update(mu){
  curMu=mu;drawCPD(mu);drawFE(mu);updateMuPanel(mu);updateSelectionCard(mu);
  if(tip.style.display==="block"&&cursorEF!==null)fillTip(cursorEF);
}

// Group the legend by defect KIND (site-independent base name):
// Va_O1 / Va_O2 / Va_O13 all collapse under "Va_O"; Ga_Sb1/Ga_Sb2
// under "Ga_Sb". Clicking a group heading toggles the whole kind.
function defectBase(n){return n.replace(/\d+$/,"");}
function refreshVisibleDefects(){
  if(!curMu)return;
  drawFE(curMu);
  if(tip.style.display==="block"&&cursorEF!==null)fillTip(cursorEF);
}
function toggleGroup(base){
  var anyVisible=false;
  CATS[base].forEach(function(d){if(!hidden[d.getAttribute("data-name")])anyVisible=true;});
  var hide=anyVisible;
  CATS[base].forEach(function(d){
    var n=d.getAttribute("data-name");hidden[n]=hide;d.style.opacity=hide?".4":"1";
  });
  refreshVisibleDefects();
}
var leg=document.getElementById("leg"),CATS={};
names.forEach(function(n,i){
  var d=document.createElement("div");d.setAttribute("data-name",n);
  d.innerHTML="<span style='display:inline-block;width:10px;height:10px;border-radius:50%;background:"+CL[i]+";margin-right:3px'></span>"+segHtml(DISP[n]);
  d.onclick=function(){hidden[n]=!hidden[n];d.style.opacity=hidden[n]?".4":"1";refreshVisibleDefects();};
  var base=defectBase(n);if(!CATS[base])CATS[base]=[];CATS[base].push(d);
});
// Fixed layout: kind headings (in generation-time order) + members.
// The legend NEVER reorders on drag or E_F hover.
Object.keys(CATS).forEach(function(base){
  var g=document.createElement("div");g.className="leg-group";
  var h=document.createElement("div");h.className="leg-cat";h.textContent=base+"（"+CATS[base].length+"）";
  h.title="点击隐藏/显示该缺陷种类";h.onclick=function(){toggleGroup(base);};
  g.appendChild(h);CATS[base].forEach(function(d){g.appendChild(d);});leg.appendChild(g);
});

// ── 3D Viewer for defect local structures ──
var viewer3d = null;
var currentDefect = null;
var currentCharge = null;
var currentCutoff = 5.0;

function init3DViewer() {
  var viewerDiv = document.getElementById("viewer3d");
  if (!viewerDiv || typeof $3Dmol === 'undefined') return;
  
  viewer3d = $3Dmol.createViewer(viewerDiv, {backgroundColor: "white"});
  
  var select = document.getElementById("defect-select");
  if (select) {
    Object.keys(DEF).forEach(function(name) {
      DEF[name].charges.forEach(function(c) {
        var opt = document.createElement("option");
        opt.value = name + "_" + c.q;
        opt.textContent = name + " (q=" + c.q + ")";
        select.appendChild(opt);
      });
    });
    
    if (select.options.length > 0) {
      select.selectedIndex = 0;
      loadDefectStructure(select.value);
    }
    
    select.onchange = function() {
      loadDefectStructure(this.value);
    };
  }
  
  var slider = document.getElementById("cutoff-slider");
  var valueDisplay = document.getElementById("cutoff-value");
  if (slider) {
    slider.oninput = function() {
      currentCutoff = parseFloat(this.value);
      if (valueDisplay) valueDisplay.textContent = currentCutoff.toFixed(1) + " Å";
      if (currentDefect !== null) {
        loadDefectStructure(currentDefect + "_" + currentCharge);
      }
    };
  }
}

function loadDefectStructure(defectChargeStr) {
  if (!viewer3d) return;
  
  var parts = defectChargeStr.split("_");
  if (parts.length < 2) return;
  
  var charge = parseInt(parts[parts.length - 1]);
  var defectName = parts.slice(0, -1).join("_");
  
  currentDefect = defectName;
  currentCharge = charge;
  
  // Use embedded STRUCTURES data instead of API call
  if (STRUCTURES && STRUCTURES[defectName] && STRUCTURES[defectName][charge]) {
    var data = STRUCTURES[defectName][charge];
    renderDefectStructure(data);
  } else {
    console.warn("No structure data for", defectName, "q=", charge);
  }
}

function renderDefectStructure(data) {
  if (!viewer3d || !data || !data.atoms) return;
  
  viewer3d.removeAllModels();
  
  var xyz = data.atoms.length + "\\n" + data.metadata.defect_name + " (q=" + data.metadata.charge + ")\\n";
  data.atoms.forEach(function(atom) {
    xyz += atom.element + " " + atom.x.toFixed(4) + " " + atom.y.toFixed(4) + " " + atom.z.toFixed(4) + "\\n";
  });
  
  viewer3d.addModel(xyz, "xyz");
  viewer3d.setStyle({}, {stick: {radius: 0.1, opacity: 0.7}, sphere: {scale: 0.3, opacity: 0.8}});
  
  data.atoms.forEach(function(atom, idx) {
    if (atom.is_defect) {
      viewer3d.setStyle({serial: idx}, {sphere: {scale: 0.5, color: "red", opacity: 1.0}});
    } else if (atom.is_neighbor) {
      viewer3d.setStyle({serial: idx}, {sphere: {scale: 0.35, color: "yellow", opacity: 0.9}});
    }
  });
  
  viewer3d.setClickable({}, true, function(atom) {
    showAtomInfo(atom, data.atoms[atom.serial]);
  });
  
  viewer3d.zoomTo();
  viewer3d.render();
}

function showAtomInfo(viewerAtom, atomData) {
  var infoDiv = document.getElementById("atom-info");
  if (!infoDiv) return;
  
  var html = "<strong>" + atomData.element + "</strong><br>";
  html += "位置: (" + atomData.x.toFixed(2) + ", " + atomData.y.toFixed(2) + ", " + atomData.z.toFixed(2) + ")<br>";
  html += "距离: " + atomData.distance.toFixed(2) + " Å<br>";
  if (atomData.is_defect) {
    html += "<span style='color:red;font-weight:bold;'>缺陷中心</span>";
  } else if (atomData.is_neighbor) {
    html += "<span style='color:orange;'>近邻原子</span>";
  }
  
  infoDiv.innerHTML = html;
  infoDiv.style.display = "block";
  
  setTimeout(function() {
    infoDiv.style.display = "none";
  }, 5000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init3DViewer);
} else {
  init3DViewer();
}

// Link legend clicks with 3D viewer
function linkLegendWith3DViewer() {
  var legDiv = document.getElementById("leg");
  if (!legDiv) return;
  
  legDiv.addEventListener("click", function(e) {
    var target = e.target;
    while (target && target !== legDiv) {
      if (target.getAttribute && target.getAttribute("data-name")) {
        var defectName = target.getAttribute("data-name");
        var select = document.getElementById("defect-select");
        if (select) {
          // Find the first charge state for this defect
          for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value.startsWith(defectName + "_")) {
              select.selectedIndex = i;
              loadDefectStructure(select.value);
              // Scroll to 3D viewer
              var structureCard = document.getElementById("structureCard");
              if (structureCard) {
                structureCard.scrollIntoView({behavior: "smooth", block: "nearest"});
              }
              break;
            }
          }
        }
        break;
      }
      target = target.parentElement;
    }
  });
}

// Call linkLegendWith3DViewer after DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", linkLegendWith3DViewer);
} else {
  linkLegendWith3DViewer();
}

// Fixed canvas dimensions: both canvases are 600×450.
// No responsive resizing — dimensions are set in HTML and JS.
update(curMu);
</script></body></html>"""


def _html_template(
    *,
    host_name: str,
    n_vertices: int,
    poly_2d: list[list[float]],
    vertex_mu: list[dict[str, float]],
    vertex_names: list[str],
    vertex_phases: list[dict[str, list[str]]],
    vertex_elements: list[str],
    defects: dict[str, Any],
    sorted_names: list[str],
    ref_mu: dict[str, float],
    colors: list[str],
    cbm: float,
    exo_elements: list[str],
    mags: dict[str, dict[int, float]] | None = None,
    vox: dict[str, dict[str, Any]] | None = None,
    defect_structures: dict[str, dict[int, dict[str, Any]]] | None = None,
) -> str:
    """Render the self-contained interactive HTML page."""
    js = json.dumps
    mags = mags or {}
    vox = vox or {}
    defect_structures = defect_structures or {}
    # Compute impurity elements (in vertex_mu but not host vertex_elements)
    host_set = set(vertex_elements)
    impurity_set: set[str] = set()
    for vm in vertex_mu:
        for e in vm:
            if e not in host_set:
                impurity_set.add(e)
    is_doped_str = "false"
    if impurity_set:
        pattern = "|".join(sorted(impurity_set))
        is_doped_str = f"!!n.match(/^({pattern})_/)"

    exo = list(exo_elements) or sorted(impurity_set)
    exo_json = js(exo)

    cpd_js = _cpd_canvas_js(
        n_vertices, poly_2d, vertex_mu, vertex_names, vertex_phases,
        vertex_elements,
    )

    fe_canvas = _FE_CANVAS_JS.replace("{is_doped_str}", is_doped_str)
    fermi_js = _FERMI_JS.replace("{exo_json}", exo_json)

    return (
        _COMMON_HTML_HEAD.format(
            title=host_name, title_html=_formula_html(host_name),
        )
        + "\n"
        + _COMMON_JS_DECLS.format(
            def_json=js(defects),
            ref_json=js(ref_mu),
            colors_json=js(colors),
            bg=cbm,
            names_json=js(sorted_names),
            disp_json=js({n: _defect_segments(n) for n in sorted_names}),
            mag_json=js({n: {str(q): mu for q, mu in qm.items()}
                         for n, qm in mags.items()}),
            vox_json=js({n: vox.get(n, {"p": "?", "h": None})
                         for n in sorted_names}),
            structures_json=js({
                name: {str(q): data for q, data in charges.items()}
                for name, charges in defect_structures.items()
            }),
        )
        + "\n" + cpd_js + "\n" + fe_canvas + fermi_js + "\n" + _COMMON_JS_FOOTER
    )


# ═════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════

def generate_interactive_html(system_dir: Path) -> Path | None:
    """Read inputs from *system_dir* and write ``formation_energy_interactive.html``.

    Requires:
        - ``defect/defect_energy_summary.json``
        - ``cpd/chem_pot_diag.json``
        - ``cpd/target_vertices.yaml`` (or ``.json``)

    Returns the output path, or ``None`` for unsupported systems.
    """
    de, cpd = _load_inputs(system_dir)
    cbm_val = de.cbm
    if cbm_val is None:
        import logging
        logging.getLogger(__name__).warning("No CBM found, skipping")
        return None

    try:
        (
            vertex_mu,
            vertex_names,
            host_name,
            vertex_elements,
            vertex_phases,
        ) = _extract_vertex_data(cpd)
    except (ValueError, TypeError) as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Cannot extract vertex data: %s", exc,
        )
        return None
    n_vertices = len(vertex_mu)

    if n_vertices < 2:
        import logging
        logging.getLogger(__name__).warning(
            "%s: %d vertex/vertices, skipping interactive HTML",
            host_name, n_vertices,
        )
        return None

    defects = _build_defects(de)
    sorted_names = _sort_defect_names(defects)
    colors = _kind_colors(sorted_names)

    mags = _load_magnetizations(system_dir, sorted_names)
    host_valences = _infer_host_valences(host_name)
    vox = {n: _ion_valence_template(n, host_valences) for n in sorted_names}
    if not host_valences:
        logger.warning(
            "%s: host formula %r has no charge-neutral valence solution; "
            "ion-valence labels render as ?", host_name, host_name,
        )

    # Extract defect structures for 3D viewer (pre-loaded, no API needed)
    defect_structures = {}
    for name in sorted_names:
        defect_structures[name] = {}
        for charge_data in defects[name]["charges"]:
            charge = int(charge_data["q"])
            structure = _extract_defect_structure(system_dir, name, charge, cutoff=5.0)
            if structure:
                defect_structures[name][charge] = structure

    ref_mu: dict[str, float] = vertex_mu[0] if vertex_mu else {}

    if len(vertex_elements) >= 2:
        ax0, ax1 = vertex_elements[0], vertex_elements[1]
    else:
        keys = list(ref_mu.keys())
        ax0, ax1 = keys[0], keys[1] if len(keys) > 1 else keys[0]

    poly_2d: list[list[float]] = [
        [v.get(ax0, 0.0), v.get(ax1, 0.0)] for v in vertex_mu
    ]
    # NOTE: all vertices are kept — the 2D-projection convex hull used to
    # filter/order them, but that drops true vertices of a 3D region whose
    # projection lands inside the hull (e.g. BaAl2B2O7 A/F), and the JS
    # hull then fabricates pseudo-edges with empty phase intersections
    # (edges with no compound label). Vertex ordering is handled in JS
    # (hull2D / springLayout); poly_2d is only a layout seed.

    exo_elements = _dopant_elements(system_dir)
    n_vertices = len(vertex_mu)

    html = _html_template(
        host_name=host_name,
        n_vertices=n_vertices,
        poly_2d=poly_2d,
        vertex_mu=vertex_mu,
        vertex_names=vertex_names,
        vertex_phases=vertex_phases,
        vertex_elements=vertex_elements,
        defects=defects,
        sorted_names=sorted_names,
        ref_mu=ref_mu,
        colors=colors,
        cbm=cbm_val,
        exo_elements=exo_elements,
        mags=mags,
        vox=vox,
        defect_structures=defect_structures,
    )

    out_path = system_dir / "formation_energy_interactive.html"
    out_path.write_text(html)
    return out_path
