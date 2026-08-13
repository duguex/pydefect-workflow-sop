"""Interactive formation-energy + chemical-potential HTML report.

Generates a self-contained HTML page from pydefect post-processing outputs:
  - defect/defect_energy_summary.json
  - cpd/chem_pot_diag.json
  - cpd/target_vertices.yaml

The page renders formation-energy vs Fermi-level plots that respond to
chemical-potential dragging inside the stability region, matching the static
``pydefect pe`` PDF plots in a live, interactive form.

Supports:
  - 2-vertex (binary):    1-D slider with linear interpolation
  - 3-vertex (ternary):   triangle with barycentric
  - 4-vertex (ternary):   quadrilateral with two-triangle barycentric
  - 1-vertex (unary):     static plot (no CPD interactivity)

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
# JS helpers — barycentric & lerp code generation
# ═════════════════════════════════════════════════════════════════════

def _bary_js(
    verts: list[list[float]], tri: tuple[int, int, int]
) -> str:
    """Return a JS function string for barycentric interpolation in *tri*."""
    i0, i1, i2 = tri
    t0, t1, t2 = verts[i0], verts[i1], verts[i2]

    d = (t1[1] - t2[1]) * (t0[0] - t2[0]) + (t2[0] - t1[0]) * (t0[1] - t2[1])

    a_c = t1[1] - t2[1]; a_px = -a_c * t2[0]
    a_d = t2[0] - t1[0]; a_py = -a_d * t2[1]
    b_c = t2[1] - t0[1]; b_px = -b_c * t2[0]
    b_d = t0[0] - t2[0]; b_py = -b_d * t2[1]

    lines = [
        f"function(px,py)", "{",
        f"  var d={d:.10g};if(Math.abs(d)<1e-15)return null;",
        f"  var a=({a_c:.10g}*px+{a_px:.10g}+{a_d:.10g}*py+{a_py:.10g})/d;",
        f"  var b=({b_c:.10g}*px+{b_px:.10g}+{b_d:.10g}*py+{b_py:.10g})/d;",
        f"  var c=1-a-b;var inside=a>=-0.001&&b>=-0.001&&c>=-0.001;a=Math.max(0,Math.min(1,a));b=Math.max(0,Math.min(1,b));c=1-a-b;if(c<0){{b=1-a;c=0;}}return[a,b,c,inside];}}",
    ]
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════
# CPD widget JS (per vertex count)
# ═════════════════════════════════════════════════════════════════════

def _convex_hull(points: list[list[float]]) -> list[int]:
    """Convex-hull vertex indices (CCW) via monotone chain.

    The 3D chemical-potential polytope's vertices, projected onto the 2D
    display axes, are not necessarily in boundary order — the raw
    target_vertices order can self-intersect in 2D (observed with 5
    vertices on Fe-doped CaAl4O7).  The hull order is the correct 2D
    boundary, and its triangulation is the valid interpolation domain.
    """
    pts = sorted((tuple(p), i) for i, p in enumerate(points))
    if len(pts) <= 1:
        return [i for _, i in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[tuple[float, float], int]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2][0], lower[-1][0], p[0]) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[tuple[float, float], int]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2][0], upper[-1][0], p[0]) <= 0:
            upper.pop()
        upper.append(p)
    return [i for _, i in lower[:-1] + upper[:-1]]


def _ear_clip(poly: list[list[float]]) -> list[tuple[int, int, int]]:
    """Ear-clipping triangulation of a simple polygon (concave OK).

    Returns triangle vertex-index tuples (N-2 for N>=3).  Falls back to a
    fan when no ear is found (numerically degenerate input) rather than
    raising, so the widget stays functional.
    """
    n = len(poly)
    if n < 3:
        return []
    idx = list(range(n))
    tris: list[tuple[int, int, int]] = []
    area2 = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    orient = 1.0 if area2 >= 0 else -1.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def in_tri(p, a, b, c):
        d1 = cross(a, b, p)
        d2 = cross(b, c, p)
        d3 = cross(c, a, p)
        has_neg = d1 < -1e-9 or d2 < -1e-9 or d3 < -1e-9
        has_pos = d1 > 1e-9 or d2 > 1e-9 or d3 > 1e-9
        return not (has_neg and has_pos)

    while len(idx) > 3:
        ear = None
        for i in range(len(idx)):
            i0, i1, i2 = idx[i - 1], idx[i], idx[(i + 1) % len(idx)]
            a, b, c = poly[i0], poly[i1], poly[i2]
            if cross(a, b, c) * orient <= 0:
                continue  # reflex (or degenerate) corner
            if any(
                in_tri(poly[j], a, b, c) for j in idx if j not in (i0, i1, i2)
            ):
                continue
            ear = i
            break
        if ear is None:
            for i in range(1, len(idx) - 1):
                tris.append((idx[0], idx[i], idx[i + 1]))
            break
        i0, i1, i2 = idx[ear - 1], idx[ear], idx[(ear + 1) % len(idx)]
        tris.append((i0, i1, i2))
        del idx[ear]
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _cpd_canvas_js(
    n_vertices: int,
    poly_2d: list[list[float]],
    vertex_mu: list[dict[str, float]],
    vertex_names: list[str],
    vertex_phases: list[dict[str, list[str]]],
    ax0: str,
    ax1: str,
    a0_range: tuple[float, float],
    a1_range: tuple[float, float],
) -> str:
    """Return the CPD canvas + getMu JS block for *n_vertices*."""
    js = json.dumps

    common = f"""
var cc = document.getElementById("cpd"), cctx = cc.getContext("2d");
var cW = 300, cH = 300, cP = {{l:35,r:10,t:20,b:25}};
var a0R = [{a0_range[0]},{a0_range[1]}], a1R = [{a1_range[0]},{a1_range[1]}];
var POLY = {js(poly_2d)};
var VPHASES = {js(vertex_phases)};
var VERTEX_MU = {js(vertex_mu)};
var selectionMode = "区域内插值";

function selectedVertex(mu){{
  var best=-1,dist=Infinity;
  VERTEX_MU.forEach(function(vm,i){{
    var dx=cX(vm["{ax0}"])-cX(mu["{ax0}"]),dy=cY(vm["{ax1}"])-cY(mu["{ax1}"]);
    var d=Math.sqrt(dx*dx+dy*dy);if(d<dist){{dist=d;best=i;}}
  }});
  return dist<=14?best:-1;
}}

function cX(v){{return cP.l+(v-a0R[0])/(a0R[1]-a0R[0])*(cW-cP.l-cP.r);}}
function cY(v){{return cP.t+(1-(v-a1R[0])/(a1R[1]-a1R[0]))*(cH-cP.t-cP.b);}}
function invX(x){{return a0R[0]+(x-cP.l)/(cW-cP.l-cP.r)*(a0R[1]-a0R[0]);}}
function invY(y){{return a1R[0]+(1-(y-cP.t)/(cH-cP.t-cP.b))*(a1R[1]-a1R[0]);}}
function projectToEdge(px,py,edges){{
  var best=Infinity,bestT=0,bestI=0,bestJ=0;
  for(var k=0;k<edges.length;k++){{
    var i=edges[k][0],j=edges[k][1];
    var ax=POLY[i][0],ay=POLY[i][1],bx=POLY[j][0],by=POLY[j][1];
    var dx=bx-ax,dy=by-ay,len2=dx*dx+dy*dy;
    if(len2<1e-15)continue;
    var t=((px-ax)*dx+(py-ay)*dy)/len2;
    t=Math.max(0,Math.min(1,t));
    var cx=ax+t*dx,cy=ay+t*dy;
    var dist=(px-cx)*(px-cx)+(py-cy)*(py-cy);
    if(dist<best){{best=dist;bestT=t;bestI=i;bestJ=j;}}
  }}
  return{{t:bestT,i:bestI,j:bestJ}};
}}
"""

    if n_vertices == 1:
        # Static: no CPD canvas, getMu returns the single vertex
        return common + f"""
function getMu() {{
  return {js(vertex_mu[0])};
}}
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  cctx.fillStyle="#555";cctx.font="11px Arial";cctx.textAlign="center";
  cctx.fillText("μ fixed at vertex {vertex_names[0]}",cW/2,cH/2);
}}
var curMu = getMu();
"""

    if n_vertices == 2:
        return common + f"""
function getMu(t){{
  var mu={{}};
  var v0=VERTEX_MU[0],v1=VERTEX_MU[1];
  for(var e in v0){{mu[e]=v0[e]+t*(v1[e]-v0[e]);}}
  return mu;
}}
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  var x0=cX(POLY[0][0]),y0=cY(POLY[0][1]);
  var x1=cX(POLY[1][0]),y1=cY(POLY[1][1]);
  cctx.strokeStyle="#64748b";cctx.lineWidth=2.5;cctx.lineCap="round";
  cctx.beginPath();cctx.moveTo(x0,y0);cctx.lineTo(x1,y1);cctx.stroke();
  [[x0,y0],[x1,y1]].forEach(function(p,i){{
    cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(p[0],p[1],3.5,0,2*Math.PI);cctx.fill();
    cctx.font="600 11px Arial";cctx.textAlign="left";cctx.fillText("V"+(i+1),p[0]+6,p[1]-6);
  }});
  if(mu){{
    var mx=cX(mu["{ax0}"]),my=cY(mu["{ax1}"]);
    cctx.beginPath();cctx.arc(mx,my,6,0,2*Math.PI);
    cctx.fillStyle="#169b78";cctx.fill();cctx.strokeStyle="#fff";cctx.lineWidth=2;cctx.stroke();
  }}
  cctx.fillStyle="#657084";cctx.font="11px Arial";cctx.textAlign="center";
  cctx.fillText("μ_{ax0} (eV)",cW/2,cH-4);
  cctx.save();cctx.translate(13,cH/2);cctx.rotate(-Math.PI/2);cctx.fillText("μ_{ax1} (eV)",0,0);cctx.restore();
}}
function ptrT(e){{
  var r=cc.getBoundingClientRect();
  var cx=e.clientX-r.left, cy=e.clientY-r.top;
  var x0=cX(POLY[0][0]),y0=cY(POLY[0][1]);
  var x1=cX(POLY[1][0]),y1=cY(POLY[1][1]);
  var dx=x1-x0,dy=y1-y0;
  var len2=dx*dx+dy*dy; if(len2<1e-8) return 0.5;
  var t=((cx-x0)*dx+(cy-y0)*dy)/len2;
  return Math.max(0,Math.min(1,t));
}}
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var t=ptrT(e);selectionMode="边界插值";update(getMu(t));}});
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var t=ptrT(e);selectionMode="边界插值";update(getMu(t));}});
var curMu=getMu(0.5);
"""

    if n_vertices >= 3:
        # General N-gon (N>=3): ear-clip into triangles, barycentric in
        # each, edge projection outside.  Replaces the former 3/4-vertex
        # special cases — CPDs grow extra vertices when dopant phases
        # (Fe/Bi) enter the chemical-potential diagram (e.g. 5 vertices
        # for CaAl4O7 with Fe), and the 2D projection can be concave, so
        # a plain fan is not safe.
        tris = _ear_clip(poly_2d)
        edges = [[i, (i + 1) % n_vertices] for i in range(n_vertices)]
        barys = "[" + ",".join(_bary_js(poly_2d, t) for t in tris) + "]"
        return common + f"""
var BARYS = {barys};
var TRIS = {js(tris)};
var EDGES = {js(edges)};
function getMu(px,py){{
  for(var k=0;k<TRIS.length;k++){{
    var bc=BARYS[k](px,py);
    if(bc[3]){{
      selectionMode="区域内插值";
      var mu={{}},t=TRIS[k],v0=VERTEX_MU[t[0]],v1=VERTEX_MU[t[1]],v2=VERTEX_MU[t[2]];
      for(var e in v0){{mu[e]=bc[0]*v0[e]+bc[1]*v1[e]+bc[2]*v2[e];}}
      return mu;
    }}
  }}
  selectionMode="边界插值";
  var edge=projectToEdge(px,py,EDGES),mu={{}},v0=VERTEX_MU[edge.i],v1=VERTEX_MU[edge.j];
  for(var e in v0){{mu[e]=v0[e]+edge.t*(v1[e]-v0[e]);}}
  return mu;
}}
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  cctx.strokeStyle="#e9edf2";cctx.lineWidth=1;cctx.fillStyle="#718096";cctx.font="10px Arial";cctx.textAlign="center";
  for(var i=0;i<=4;i++){{var v=a0R[0]+i/4*(a0R[1]-a0R[0]);cctx.beginPath();cctx.moveTo(cX(v),cP.t);cctx.lineTo(cX(v),cH-cP.b);cctx.stroke();cctx.fillText(v.toFixed(2),cX(v),cH-cP.b+13);}}
  cctx.textAlign="right";
  for(var i=0;i<=4;i++){{var v=a1R[0]+i/4*(a1R[1]-a1R[0]);cctx.beginPath();cctx.moveTo(cP.l,cY(v));cctx.lineTo(cW-cP.r,cY(v));cctx.stroke();cctx.fillText(v.toFixed(2),cP.l-5,cY(v)+3);}}
  cctx.strokeStyle="#475569";cctx.lineWidth=2;cctx.beginPath();
  POLY.forEach(function(v,i){{i===0?cctx.moveTo(cX(v[0]),cY(v[1])):cctx.lineTo(cX(v[0]),cY(v[1]));}});
  cctx.closePath();cctx.stroke();cctx.fillStyle="rgba(22,155,120,0.09)";cctx.fill();
  POLY.forEach(function(v,i){{
    var x=cX(v[0]),y=cY(v[1]);cctx.fillStyle="#334155";cctx.beginPath();cctx.arc(x,y,3.5,0,2*Math.PI);cctx.fill();
    cctx.font="600 11px Arial";cctx.textAlign="left";cctx.fillText("V"+(i+1),x+6,y-6);
  }});
  if(mu){{var mx=cX(mu["{ax0}"]),my=cY(mu["{ax1}"]);cctx.beginPath();cctx.arc(mx,my,6,0,2*Math.PI);cctx.fillStyle="#169b78";cctx.fill();cctx.strokeStyle="#fff";cctx.lineWidth=2;cctx.stroke();}}
  cctx.fillStyle="#657084";cctx.font="11px Arial";cctx.textAlign="center";cctx.fillText("μ_{ax0} (eV)",cW/2,cH-4);
  cctx.save();cctx.translate(13,cH/2);cctx.rotate(-Math.PI/2);cctx.fillText("μ_{ax1} (eV)",0,0);cctx.restore();
}}
function ptrPos(e){{var r=cc.getBoundingClientRect();return[invX(e.clientX-r.left),invY(e.clientY-r.top)];}}
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
var cx0=0,cy0=0;POLY.forEach(function(v){{cx0+=v[0];cy0+=v[1];}});cx0/=POLY.length;cy0/=POLY.length;
var curMu=getMu(cx0,cy0);
"""

    raise ValueError(f"Unsupported vertex count: {n_vertices}")


# ═════════════════════════════════════════════════════════════════════
# HTML page template
# ═════════════════════════════════════════════════════════════════════

_COMMON_HTML_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} Formation Energy</title>
<style>
:root{{--ink:#172033;--muted:#657084;--line:#dbe1e9;--grid:#e9edf2;--card:#fff;--canvas:#fbfcfe;--accent:#169b78;--accent-soft:#e7f6f1}}
*{{box-sizing:border-box}}
html,body{{min-height:100%;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;margin:0;background:#f4f6f9;color:var(--ink)}}
body{{padding:14px}}
h2{{margin:0;font-size:17px;letter-spacing:-.01em}}
.report-head{{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:0 0 12px}}
.report-kicker{{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}}
.report-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}}
.report-card{{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 2px rgba(15,23,42,.04);overflow:hidden}}
.report-card__head{{display:flex;align-items:baseline;justify-content:space-between;gap:8px;padding:11px 13px 9px;border-bottom:1px solid var(--line)}}
.report-card__head h3{{margin:0;font-size:13px;letter-spacing:.01em}}
.report-card__hint{{font-size:11px;color:var(--muted)}}
.report-card__body{{padding:12px}}
.cpd-card__body{{display:flex;flex-direction:column;align-items:center;gap:10px}}
canvas{{display:block;background:var(--canvas);border:1px solid var(--line);border-radius:7px}}
.selection-card{{width:100%;border:1px solid var(--line);border-radius:7px;background:#f8fafc;padding:10px 11px}}
.selection-card__head{{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:7px}}
.selection-card__title{{font-size:12px;font-weight:700}}
.selection-card__state{{font-size:11px;color:var(--accent);font-weight:650}}
.selection-card__constraints{{font-size:12px;line-height:1.45;color:#39465b;min-height:18px}}
.selection-card__mu{{margin:7px 0;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;line-height:1.45;color:#4c596d;word-break:break-word}}
.mupanel{{font-size:12px;color:#444}}
.mupanel-title{{font-size:11px;font-weight:700;color:var(--muted);margin:2px 0 5px;letter-spacing:.02em}}
.murow{{display:grid;grid-template-columns:30px 42px 1fr 42px;gap:6px;align-items:center;margin:3px 0}}
.muel{{font-weight:700;color:#334155}}.mumin,.mumax{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:10px;color:#7b8797;text-align:right}}
.mubar{{position:relative;height:5px;background:#dfe6ee;border-radius:99px}}.mucur{{position:absolute;top:-3px;width:11px;height:11px;border-radius:50%;background:var(--accent);margin-left:-5px;box-shadow:0 0 0 2px #fff}}
.fe-workspace{{display:block}}
.fe-plot{{min-width:0;position:relative}}
.fe-tip{{position:absolute;z-index:40;display:none;width:272px;max-height:78%;overflow-y:auto;background:rgba(255,255,255,.97);border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 18px rgba(15,23,42,.16);padding:6px 8px;font-size:11px}}
.fe-tip__head{{font-size:11px;font-weight:700;color:var(--accent);margin-bottom:4px}}
.fe-tip__foot{{font-size:10px;color:var(--muted);margin-top:4px}}
.fe-tip .row{{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:6px;align-items:center;padding:3px 2px;border-bottom:1px solid #eef2f6}}
.fe-tip .row:last-child{{border-bottom:0}}
.fe-tip .swatch{{width:8px;height:8px;border-radius:50%}}
.fe-tip .tname{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.fe-tip .tenergy{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:10px;color:#4c596d}}
.fe-note{{font-size:10px;color:var(--muted);margin-top:8px;line-height:1.4}}
.leg{{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}}.leg-group{{display:flex;flex-wrap:wrap;gap:4px;min-width:0;margin:2px 0;padding:2px 4px;border-left:2px solid #dfe6ee}}.leg>div{{display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;padding:2px 5px;border-radius:3px}}.leg>div:hover{{background:#eef3f6}}.leg-cat{{font-size:10px!important;font-weight:700;color:var(--accent);flex-basis:100%}}.leg-cat:hover{{background:var(--accent-soft)!important}}
.csub{{font-size:.72em;vertical-align:sub}}.csup{{font-size:.72em;vertical-align:super}}
@media(max-width:800px){{.report-grid{{grid-template-columns:1fr}}}}
@media(max-width:560px){{body{{padding:8px}}.report-head{{align-items:flex-start;flex-direction:column}}.report-card__body{{padding:8px}}}}
</style></head><body>
<header class="report-head"><h2>{title_html}</h2><span class="report-kicker">Defect thermodynamics</span></header>
<main class="report-grid">
<section class="report-card" id="cpdCard"><header class="report-card__head"><h3>化学势稳定区</h3><span class="report-card__hint">拖动或点击选择化学条件</span></header><div class="report-card__body cpd-card__body">
<canvas id="cpd" width="420" height="420"></canvas>
<section class="selection-card" aria-live="polite"><div class="selection-card__head"><span class="selection-card__title">当前化学条件</span><span id="selection-state" class="selection-card__state">区域内插值</span></div><div id="selection-constraints" class="selection-card__constraints"></div><div id="selection-mu" class="selection-card__mu"></div><div class="mupanel"><div class="mupanel-title">化学势范围 μ (eV)</div><div id="murows"></div></div></section>
</div></section>
<section class="report-card" id="feCard"><header class="report-card__head"><h3>缺陷形成能</h3><span class="report-card__hint">移动查询 E<sub>F</sub></span></header><div class="report-card__body"><div class="fe-workspace"><div class="fe-plot"><canvas id="cv" width="800" height="520"></canvas><div class="leg" id="leg"></div><div class="fe-note">查询层按 E<sub>f</sub> 升序列出当前可见缺陷 · 本征缺陷 · 300 K · 未含自由载流子</div><div id="tip" class="fe-tip"></div></div></div></div></section>
</main>
<script>"""

_COMMON_JS_DECLS = """var DEF = {def_json};
var REF = {ref_json};
var CL = {colors_json};
var BG = {bg};
var names = {names_json};
var DISP = {disp_json};
var nEF = 200;
var hidden = {{}}; names.forEach(function(n){{hidden[n]=false;}});
"""

_FE_CANVAS_JS = """
var cv=document.getElementById("cv"), cx=cv.getContext("2d");
var W=800, H=520, P={l:54,r:16,t:22,b:42};
var minY=-10, maxY=10;
var cursorEF=null;

function xPx(v){return P.l+(v/BG)*(W-P.l-P.r);}
function yPx(v){return P.t+(1-(v-minY)/(maxY-minY))*(H-P.t-P.b);}
function xInv(x){return (x-P.l)/(W-P.l-P.r)*BG;}

// Keep the y extent stable while legend visibility changes: display state
// must not rescale the scientific frame of reference.
function calcGlobalYRange(){
  var allE=[];
  VERTEX_MU.forEach(function(vm){
    names.forEach(function(n){
      var ms=0,d=DEF[n];
      for(var e in d.delta) if(vm[e]!==undefined) ms-=d.delta[e]*vm[e];
      d.charges.forEach(function(c){allE.push(c.e0+ms);allE.push(c.e0+c.q*BG+ms);});
    });
  });
  if(allE.length===0){minY=-2;maxY=6;return;}
  var lo=Math.min.apply(null,allE),hi=Math.max.apply(null,allE),pad=Math.max(.5,(hi-lo)*.1);
  minY=Math.floor(lo-pad);maxY=Math.ceil(hi+pad);
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
  cx.strokeStyle="#e9edf2";cx.lineWidth=1;cx.fillStyle="#657084";cx.font="11px Arial";
  for(var i=0;i<=5;i++){
    var x=xPx(i*BG/5);cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,H-P.b);cx.stroke();
    cx.textAlign="center";cx.fillText((i*BG/5).toFixed(1),x,H-P.b+18);
  }
  var step=(maxY-minY)/8;
  for(var j=0;j<=8;j++){
    var y=yPx(minY+j*step);cx.beginPath();cx.moveTo(P.l,y);cx.lineTo(W-P.r,y);cx.stroke();
    cx.textAlign="right";cx.fillText((minY+j*step).toFixed(1),P.l-7,y+4);
  }
  cx.strokeStyle="#94a3b8";cx.lineWidth=1.25;cx.beginPath();cx.moveTo(P.l,P.t);cx.lineTo(P.l,H-P.b);cx.lineTo(W-P.r,H-P.b);cx.stroke();
  cx.fillStyle="#475569";cx.textAlign="center";cx.fillText("E − E_VBM (eV)",W/2,H-5);
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
    cx.strokeStyle="rgba(22,155,120,.55)";cx.lineWidth=1;cx.setLineDash([4,4]);
    cx.beginPath();cx.moveTo(xPx(cursorEF),P.t);cx.lineTo(xPx(cursorEF),H-P.b);cx.stroke();cx.setLineDash([]);
  }
  drawFermi(mu);
}
"""

_FERMI_JS = """
// Intrinsic-defect charge-neutrality: only defects whose name does NOT
// start with an exogenous (dopant) element prefix enter the balance.
var EXO = {exo_json};
var KT = 0.0259;
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
  cx.strokeStyle="#16c79a";cx.lineWidth=2;cx.setLineDash([]);
  cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,H-P.b);cx.stroke();
  cx.fillStyle="#16c79a";cx.font="bold 12px Arial";
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
var tipHover=false;
function rowHtml(r){
  return "<div class='row'><span class='swatch' style='background:"+CL[r.idx]+"'></span>"+
    "<span class='tname'>"+segHtml(DISP[r.name])+"</span>"+
    "<span class='tenergy'>"+(r.e>=0?"+":"")+r.e.toFixed(3)+" eV</span></div>";
}
function fillTip(ef){
  var rows=[];
  names.forEach(function(n,i){if(!hidden[n])rows.push({name:n,idx:i,e:calcE(n,curMu,ef)});});
  rows.sort(function(a,b){return a.e-b.e;});
  var h="<div class='fe-tip__head'>E_F = "+ef.toFixed(3)+" eV</div>";
  rows.forEach(function(r){h+=rowHtml(r);});
  h+="<div class='fe-tip__foot'>共 "+rows.length+" 条 · 本征缺陷 · 300 K · 未含自由载流子</div>";
  tip.innerHTML=h;
}
function showTip(ef,clientX,clientY){
  fillTip(ef);
  tip.style.display="block";
  var r=cv.getBoundingClientRect(),plot=tip.parentElement.getBoundingClientRect();
  var x=clientX-r.left+16,y=clientY-r.top+16;
  if(x+tip.offsetWidth>plot.width-4)x=Math.max(4,clientX-r.left-tip.offsetWidth-16);
  if(y+tip.offsetHeight>plot.height-4)y=Math.max(4,clientY-r.top-tip.offsetHeight-16);
  tip.style.left=x+"px";tip.style.top=y+"px";
}
function hideTip(){tip.style.display="none";}
// Single owner of hover state: the plot wrapper contains BOTH the canvas and
// the floating panel, so the pointer gliding from canvas onto the scrollable
// tooltip never re-enters a dead gap — the panel stays and can be scrolled.
var hoverCapable=window.matchMedia("(hover:hover)").matches;
var plotEl=cv.parentElement;
plotEl.addEventListener("mousemove",function(ev){
  if(!hoverCapable||tipHover)return; // touch: no floating follow; inside the panel: let it scroll
  var r=cv.getBoundingClientRect();
  // Follow only while the pointer is over the canvas itself. Over the legend
  // the panel must stay hidden so legend rows stay clickable; over the panel
  // the tipHover guard above already applies.
  if(ev.clientX<r.left||ev.clientX>r.right||ev.clientY<r.top||ev.clientY>r.bottom)return;
  var ef=xInv(ev.clientX-r.left);
  if(ef<0||ef>BG)return;
  cursorEF=ef;
  if(curMu)drawFE(curMu);
  showTip(ef,ev.clientX,ev.clientY);
});
plotEl.addEventListener("mouseleave",function(){
  // Browsers synthesize a compat mouseleave after every tap; on touch-only
  // devices that must not kill the tapped-on summary — the tap toggles it.
  if(!hoverCapable)return;
  cursorEF=null;if(curMu)drawFE(curMu);hideTip();
});
tip.addEventListener("mouseenter",function(){tipHover=true;});
tip.addEventListener("mouseleave",function(){tipHover=false;});
document.getElementById("leg").addEventListener("mouseenter",hideTip);
// Touch / non-hover devices: no floating follow. A tap pins a compact
// summary to the plot; tapping again dismisses it.
if(!hoverCapable){
  cv.addEventListener("click",function(ev){
    var r=cv.getBoundingClientRect(),ef=xInv(ev.clientX-r.left);
    if(ef<0||ef>BG)return;
    cursorEF=ef;if(curMu)drawFE(curMu);
    if(tip.style.display==="block"&&tip.tappedEf===ef){hideTip();tip.tappedEf=null;return;}
    tip.tappedEf=ef;
    var rows=[];
    names.forEach(function(n,i){if(!hidden[n])rows.push({name:n,idx:i,e:calcE(n,curMu,ef)});});
    rows.sort(function(a,b){return a.e-b.e;});
    var h="<div class='fe-tip__head'>E_F = "+ef.toFixed(3)+" eV · 最低 5 条</div>";
    rows.slice(0,5).forEach(function(r){h+=rowHtml(r);});
    h+="<div class='fe-tip__foot'>共 "+rows.length+" 条 · 再次点击收起</div>";
    tip.innerHTML=h;tip.style.display="block";
    tip.style.left="8px";tip.style.top="8px";
  });
}

// Chemical-potential range panel: per-element min/current/max over the
// stability vertices, updated live as the selection moves.
function buildMuPanel(){
  var box=document.getElementById("murows");
  var elems=[];VERTEX_MU.forEach(function(vm){for(var e in vm)if(elems.indexOf(e)<0)elems.push(e);});
  elems.sort();
  var rows={};
  elems.forEach(function(e){
    var mn=Infinity,mx=-Infinity;
    VERTEX_MU.forEach(function(vm){var v=vm[e];if(v<mn)mn=v;if(v>mx)mx=v;});
    var row=document.createElement("div");row.className="murow";
    row.innerHTML="<span class='muel'>"+e+"</span>"+
      "<span class='mumin'>"+mn.toFixed(2)+"</span>"+
      "<div class='mubar'><span class='mucur'></span></div>"+
      "<span class='mumax'>"+mx.toFixed(2)+"</span>";
    box.appendChild(row);
    rows[e]={mn:mn,mx:mx,cur:row.querySelector(".mucur")};
  });
  return rows;
}
var muRows=buildMuPanel();
function updateMuPanel(mu){
  for(var e in muRows){
    var r=muRows[e],v=mu[e];if(v===undefined)continue;
    r.cur.style.left=((r.mx>r.mn)?(v-r.mn)/(r.mx-r.mn)*100:50)+"%";
  }
}
function updateSelectionCard(mu){
  var idx=selectedVertex(mu),state=document.getElementById("selection-state"),constraints=document.getElementById("selection-constraints");
  if(idx>=0){
    var phases=(VPHASES[idx].competing||[]).join(" · ");
    state.textContent="当前顶点 V"+(idx+1);
    constraints.textContent="V"+(idx+1)+(phases?" · "+phases+"（约束）":" · 无相约束记录");
  }else{
    state.textContent=selectionMode;
    constraints.textContent=selectionMode==="边界插值"?"沿稳定区边界插值；无单一顶点约束":"稳定区内部插值；无单一顶点约束";
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

// Responsive sizing: each scientific card owns its native chart ratio.
// On narrow displays the CSS grid stacks the two cards.
function layout(){
  var dpr=window.devicePixelRatio||1;
  var cpdCard=document.getElementById("cpdCard");
  var cw=Math.max(250,Math.min(520,cpdCard.clientWidth-30));
  cc.width=cw*dpr;cc.height=cw*dpr;cc.style.width=cw+"px";cc.style.height=cw+"px";
  cctx.setTransform(dpr,0,0,dpr,0,0);cW=cw;cH=cw;
  var plot=cv.parentElement,fw=Math.max(300,Math.round(plot.clientWidth));
  var fh=Math.max(320,Math.min(520,Math.round(fw*.68)));
  cv.width=fw*dpr;cv.height=fh*dpr;cv.style.width=fw+"px";cv.style.height=fh+"px";
  cx.setTransform(dpr,0,0,dpr,0,0);W=fw;H=fh;
  if(curMu)update(curMu);
}
window.addEventListener("resize",layout);
layout();
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
    ax0: str,
    ax1: str,
    a0_range: tuple[float, float],
    a1_range: tuple[float, float],
    exo_elements: list[str],
) -> str:
    """Render the self-contained interactive HTML page."""
    js = json.dumps

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
        ax0, ax1, a0_range, a1_range,
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
    colors = _COLORS[:len(sorted_names)]

    ref_mu: dict[str, float] = vertex_mu[0] if vertex_mu else {}

    if len(vertex_elements) >= 2:
        ax0, ax1 = vertex_elements[0], vertex_elements[1]
    else:
        keys = list(ref_mu.keys())
        ax0, ax1 = keys[0], keys[1] if len(keys) > 1 else keys[0]

    poly_2d: list[list[float]] = [
        [v.get(ax0, 0.0), v.get(ax1, 0.0)] for v in vertex_mu
    ]

    # 2D projection of a 3D polytope: order the vertices along the hull
    # so the display polygon is simple (raw order can self-intersect).
    hull_idx = _convex_hull(poly_2d)
    if len(hull_idx) < len(poly_2d):
        logger.warning(
            "CPD 2D projection: %d vertices collapse to %d hull vertices",
            len(poly_2d), len(hull_idx),
        )
    poly_2d = [poly_2d[i] for i in hull_idx]
    vertex_mu = [vertex_mu[i] for i in hull_idx]
    vertex_names = [vertex_names[i] for i in hull_idx]
    vertex_phases = [vertex_phases[i] for i in hull_idx]
    n_vertices = len(hull_idx)

    all_ax0 = [p[0] for p in poly_2d]
    all_ax1 = [p[1] for p in poly_2d]
    pad = 0.3

    exo_elements = _dopant_elements(system_dir)

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
        ax0=ax0,
        ax1=ax1,
        a0_range=(min(all_ax0) - pad, max(all_ax0) + pad),
        a1_range=(min(all_ax1) - pad, max(all_ax1) + pad),
        exo_elements=exo_elements,
    )

    out_path = system_dir / "formation_energy_interactive.html"
    out_path.write_text(html)
    return out_path
