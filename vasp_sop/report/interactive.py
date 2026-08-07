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
import math
from pathlib import Path
from typing import Any

# ── colour palette (stable, high-contrast) ─────────────────────────
_COLORS: list[str] = [
    "#e94560", "#0f3460", "#16c79a", "#f5a623", "#7c3aed", "#ec4899",
    "#06b6d4", "#84cc16", "#f472b6", "#64748b", "#eab308", "#22c55e",
    "#a855f7", "#38bdf8", "#fb923c", "#4ade80", "#2dd4bf", "#fbbf24",
    "#c084fc", "#fb7185", "#34d399", "#facc15", "#818cf8", "#f97316",
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
) -> tuple[list[dict[str, float]], list[str], str, list[str]]:
    """Return (vertex_mu, vertex_names, host_name, vertex_elements).

    *vertex_mu*: ``[{elem: μ, ...}]`` — ALL element chemical potentials per
                 cyclically-ordered vertex.
    *vertex_names*: vertex labels (e.g. ``["A", "C", "D", "B"]``).
    *vertex_elements*: ordered list of host elements (from CPD).
    """
    rcp_raw: dict = cpd.rel_chem_pots

    # target phase name
    host_name = cpd.target

    # vertex elements (e.g. ["Br", "Cs", "Pb"] or ["Ba", "O"])
    vertex_elements: list[str] = list(cpd.vertex_elements)

    # Collect per-vertex mu dicts — only keys with numeric chemical potentials
    _meta_keys = {"target", "chem_pot", "competing_phases", "impurity_phases"}
    def _mu_dict(v: Any) -> dict[str, float]:
        if isinstance(v, dict):
            return {k: float(vv) for k, vv in v.items()
                    if k not in _meta_keys and isinstance(vv, (int, float))}
        return {}
    vert_names = [k for k, v in rcp_raw.items()
                  if isinstance(v, dict) and _mu_dict(v)]
    raw_vert_mu: list[dict[str, float]] = [
        _mu_dict(rcp_raw[vn]) for vn in vert_names
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

    return vertex_mu, vertex_names, host_name, vertex_elements


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

def _cpd_canvas_js(
    n_vertices: int,
    poly_2d: list[list[float]],
    vertex_mu: list[dict[str, float]],
    vertex_names: list[str],
    ax0: str,
    ax1: str,
    a0_range: tuple[float, float],
    a1_range: tuple[float, float],
) -> str:
    """Return the CPD canvas + getMu JS block for *n_vertices*."""
    js = json.dumps

    common = f"""
var cc = document.getElementById("cpd"), cctx = cc.getContext("2d");
var cW = cc.width, cH = cc.height, cP = {{l:35,r:10,t:20,b:25}};
var a0R = [{a0_range[0]},{a0_range[1]}], a1R = [{a1_range[0]},{a1_range[1]}];
var POLY = {js(poly_2d)};
var VNAMES = {js(vertex_names)};
var VERTEX_MU = {js(vertex_mu)};

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
  cctx.strokeStyle="#d63031";cctx.lineWidth=3;cctx.lineCap="round";
  cctx.beginPath();cctx.moveTo(x0,y0);cctx.lineTo(x1,y1);cctx.stroke();
  cctx.fillStyle="#d63031";cctx.font="bold 12px Arial";
  cctx.fillText(VNAMES[0],x0-10,y0-10);cctx.fillText(VNAMES[1],x1+10,y1-10);
  if(mu){{
    var t=(mu["{ax0}"]-POLY[0][0])/(POLY[1][0]-POLY[0][0]);
    var mx=cX(mu["{ax0}"]),my=cY(mu["{ax1}"]);
    cctx.beginPath();cctx.arc(mx,my,6,0,2*Math.PI);
    cctx.fillStyle="#16c79a";cctx.fill();
    cctx.strokeStyle="#fff";cctx.lineWidth=2;cctx.stroke();
  }}
  cctx.fillStyle="#555";cctx.font="11px Arial";cctx.textAlign="center";
  cctx.fillText("μ_{ax0} (eV)",cW/2,cH-2);
  cctx.save();cctx.translate(10,cH/2);cctx.rotate(-Math.PI/2);
  cctx.fillText("μ_{ax1} (eV)",0,0);cctx.restore();
}}
function ptrT(e){{
  var r=cc.getBoundingClientRect();
  var cx=(e.clientX-r.left)*cc.width/r.width, cy=(e.clientY-r.top)*cc.height/r.height;
  var x0=cX(POLY[0][0]),y0=cY(POLY[0][1]);
  var x1=cX(POLY[1][0]),y1=cY(POLY[1][1]);
  var dx=x1-x0,dy=y1-y0;
  var len2=dx*dx+dy*dy; if(len2<1e-8) return 0.5;
  var t=((cx-x0)*dx+(cy-y0)*dy)/len2;
  return Math.max(0,Math.min(1,t));
}}
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var t=ptrT(e);update(getMu(t));}});
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var t=ptrT(e);update(getMu(t));}});
var curMu=getMu(0.5);
"""

    if n_vertices == 3:
        bary = _bary_js(poly_2d, (0, 1, 2))
        return common + f"""
var bary01 = {bary};
function getMu(px,py){{
  var bc=bary01(px,py);
  if(bc[3]){{
    var mu={{}};var v0=VERTEX_MU[0],v1=VERTEX_MU[1],v2=VERTEX_MU[2];
    for(var e in v0){{mu[e]=bc[0]*v0[e]+bc[1]*v1[e]+bc[2]*v2[e];}}
    return mu;
  }}
  var e=projectToEdge(px,py,[[0,1],[1,2],[2,0]]);
  var mu={{}},v0=VERTEX_MU[e.i],v1=VERTEX_MU[e.j];
  for(var k in v0){{mu[k]=v0[k]+e.t*(v1[k]-v0[k]);}}
  return mu;
}}
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  cctx.strokeStyle="#ccc";cctx.lineWidth=0.5;cctx.fillStyle="#666";cctx.font="9px Arial";cctx.textAlign="center";
  for(var i=0;i<=4;i++){{var v=a0R[0]+i/4*(a0R[1]-a0R[0]);cctx.beginPath();cctx.moveTo(cX(v),cP.t);cctx.lineTo(cX(v),cH-cP.b);cctx.stroke();cctx.fillText(v.toFixed(2),cX(v),cH-cP.b+12);}}
  cctx.textAlign="right";
  for(var i=0;i<=4;i++){{var v=a1R[0]+i/4*(a1R[1]-a1R[0]);cctx.beginPath();cctx.moveTo(cP.l,cY(v));cctx.lineTo(cW-cP.r,cY(v));cctx.stroke();cctx.fillText(v.toFixed(2),cP.l-4,cY(v)+3);}}
  cctx.strokeStyle="#d63031";cctx.lineWidth=2;cctx.beginPath();
  POLY.forEach(function(v,i){{i==0?cctx.moveTo(cX(v[0]),cY(v[1])):cctx.lineTo(cX(v[0]),cY(v[1]));}});
  cctx.closePath();cctx.stroke();cctx.fillStyle="rgba(214,48,49,0.08)";cctx.fill();
  POLY.forEach(function(v,i){{cctx.fillStyle="#d63031";cctx.font="bold 12px Arial";cctx.fillText(VNAMES[i],cX(v[0])+5,cY(v[1])-5);}});
  if(mu){{cctx.beginPath();cctx.arc(cX(mu["{ax0}"]),cY(mu["{ax1}"]),6,0,2*Math.PI);cctx.fillStyle="#16c79a";cctx.fill();cctx.strokeStyle="#fff";cctx.lineWidth=2;cctx.stroke();}}
  cctx.fillStyle="#555";cctx.font="11px Arial";cctx.textAlign="center";cctx.fillText("μ_{ax0} (eV)",cW/2,cH-2);
  cctx.save();cctx.translate(10,cH/2);cctx.rotate(-Math.PI/2);cctx.fillText("μ_{ax1} (eV)",0,0);cctx.restore();
}}
function ptrPos(e){{var r=cc.getBoundingClientRect();return[invX((e.clientX-r.left)*cc.width/r.width),invY((e.clientY-r.top)*cc.height/r.height)];}}
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
var cx0=(POLY[0][0]+POLY[1][0]+POLY[2][0])/3,cy0=(POLY[0][1]+POLY[1][1]+POLY[2][1])/3;
var curMu=getMu(cx0,cy0);
"""

    if n_vertices == 4:
        bary12 = _bary_js(poly_2d, (0, 1, 2))
        bary23 = _bary_js(poly_2d, (0, 2, 3))
        return common + f"""
var bary12 = {bary12};
var bary23 = {bary23};
function getMu(px,py){{
  var bc1=bary12(px,py),bc2=bary23(px,py);
  if(bc1[3]){{var mu={{}};var v0=VERTEX_MU[0],v1=VERTEX_MU[1],v2=VERTEX_MU[2];for(var e in v0)mu[e]=bc1[0]*v0[e]+bc1[1]*v1[e]+bc1[2]*v2[e];return mu;}}
  if(bc2[3]){{var mu={{}};var v0=VERTEX_MU[0],v1=VERTEX_MU[2],v2=VERTEX_MU[3];for(var e in v0)mu[e]=bc2[0]*v0[e]+bc2[1]*v1[e]+bc2[2]*v2[e];return mu;}}
  var e=projectToEdge(px,py,[[0,1],[1,2],[2,3],[3,0]]);
  var mu={{}},v0=VERTEX_MU[e.i],v1=VERTEX_MU[e.j];
  for(var k in v0){{mu[k]=v0[k]+e.t*(v1[k]-v0[k]);}}
  return mu;
}}
function drawCPD(mu){{
  cctx.clearRect(0,0,cW,cH);
  cctx.strokeStyle="#ccc";cctx.lineWidth=0.5;cctx.fillStyle="#666";cctx.font="9px Arial";cctx.textAlign="center";
  for(var i=0;i<=4;i++){{var v=a0R[0]+i/4*(a0R[1]-a0R[0]);cctx.beginPath();cctx.moveTo(cX(v),cP.t);cctx.lineTo(cX(v),cH-cP.b);cctx.stroke();cctx.fillText(v.toFixed(2),cX(v),cH-cP.b+12);}}
  cctx.textAlign="right";
  for(var i=0;i<=4;i++){{var v=a1R[0]+i/4*(a1R[1]-a1R[0]);cctx.beginPath();cctx.moveTo(cP.l,cY(v));cctx.lineTo(cW-cP.r,cY(v));cctx.stroke();cctx.fillText(v.toFixed(2),cP.l-4,cY(v)+3);}}
  cctx.strokeStyle="#d63031";cctx.lineWidth=2;cctx.beginPath();
  POLY.forEach(function(v,i){{i==0?cctx.moveTo(cX(v[0]),cY(v[1])):cctx.lineTo(cX(v[0]),cY(v[1]));}});
  cctx.closePath();cctx.stroke();cctx.fillStyle="rgba(214,48,49,0.08)";cctx.fill();
  POLY.forEach(function(v,i){{cctx.fillStyle="#d63031";cctx.font="bold 12px Arial";cctx.fillText(VNAMES[i],cX(v[0])+5,cY(v[1])-5);}});
  if(mu){{cctx.beginPath();cctx.arc(cX(mu["{ax0}"]),cY(mu["{ax1}"]),6,0,2*Math.PI);cctx.fillStyle="#16c79a";cctx.fill();cctx.strokeStyle="#fff";cctx.lineWidth=2;cctx.stroke();}}
  cctx.fillStyle="#555";cctx.font="11px Arial";cctx.textAlign="center";cctx.fillText("μ_{ax0} (eV)",cW/2,cH-2);
  cctx.save();cctx.translate(10,cH/2);cctx.rotate(-Math.PI/2);cctx.fillText("μ_{ax1} (eV)",0,0);cctx.restore();
}}
function ptrPos(e){{var r=cc.getBoundingClientRect();return[invX((e.clientX-r.left)*cc.width/r.width),invY((e.clientY-r.top)*cc.height/r.height)];}}
cc.addEventListener("pointermove",function(e){{if(!e.buttons)return;var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
cc.addEventListener("pointerdown",function(e){{cc.setPointerCapture(e.pointerId);var p=ptrPos(e);var mu=getMu(p[0],p[1]);if(mu)update(mu);}});
var cx0=(POLY[0][0]+POLY[1][0]+POLY[2][0]+POLY[3][0])/4,cy0=(POLY[0][1]+POLY[1][1]+POLY[2][1]+POLY[3][1])/4;
var curMu=getMu(cx0,cy0);
"""

    raise ValueError(f"Unsupported vertex count: {n_vertices}")


# ═════════════════════════════════════════════════════════════════════
# HTML page template
# ═════════════════════════════════════════════════════════════════════

_COMMON_HTML_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>{title} Formation Energy</title>
<style>
body{{font-family:Arial,sans-serif;margin:15px;background:#fff;color:#222}}
canvas{{background:#f5f5f5;border-radius:8px}}
.panel{{display:flex;gap:15px;flex-wrap:wrap}}
.leg{{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}}
.leg>div{{display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;padding:2px 6px;border-radius:3px}}
.leg>div:hover{{background:#eee}}
.info{{font-size:12px;color:#666;margin:3px 0}}
#tooltip{{position:absolute;background:rgba(245,245,245,0.95);border:1px solid #d63031;border-radius:4px;padding:6px 10px;font-size:11px;pointer-events:none;display:none;z-index:10;color:#333}}
</style></head><body>
<h2>{title} — Formation Energy</h2>
<div class="panel">
<div>
<div class="info">{cpd_hint}</div>
<canvas id="cpd" width="300" height="300"></canvas>
<div class="info" id="muinfo">&mu; = &mdash; eV</div>
</div>
<div style="position:relative">
<canvas id="cv" width="800" height="500"></canvas>
<div class="leg" id="leg"></div>
</div>
</div>
<div id="tooltip"></div>
<script>"""

_COMMON_JS_DECLS = """var DEF = {def_json};
var REF = {ref_json};
var CL = {colors_json};
var BG = {bg};
var names = {names_json};
var nEF = 200;
var hidden = {{}}; names.forEach(function(n){{hidden[n]=false;}});
"""

_FE_CANVAS_JS = """
var cv=document.getElementById("cv"), cx=cv.getContext("2d");
var W=cv.width, H=cv.height, P={l:60,r:160,t:20,b:40};
var minY=-10, maxY=10;
var cursorEF=null;

function xPx(v){return P.l+(v/BG)*(W-P.l-P.r);}
function yPx(v){return P.t+(1-(v-minY)/(maxY-minY))*(H-P.t-P.b);}
function xInv(x){return (x-P.l)/(W-P.l-P.r)*BG;}

// Compute global y-range from all vertices (E_F=0 and E_F=BG)
function calcGlobalYRange(){
  var allE=[];
  VERTEX_MU.forEach(function(vm){
    var mu={};
    for(var e in vm) mu[e]=vm[e];
    names.forEach(function(n){
      if(hidden[n]) return;
      var ms=0, d=DEF[n];
      for(var e in d.delta) if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];
      d.charges.forEach(function(c){allE.push(c.e0+ms);allE.push(c.e0+c.q*BG+ms);});
    });
  });
  if(allE.length==0){minY=-2;maxY=6;return;}
  var dm=Math.min.apply(null,allE), dx=Math.max.apply(null,allE);
  var pad=Math.max(0.5,(dx-dm)*0.1);
  minY=Math.floor(dm-pad);maxY=Math.ceil(dx+pad);
  // Round to nice multiples
  var step=Math.pow(10,Math.floor(Math.log10((maxY-minY)/8)));
  minY=Math.floor(minY/step)*step;maxY=Math.ceil(maxY/step)*step;
}
calcGlobalYRange();

function calcE(name,mu,eF){
  var d=DEF[name],me=Infinity,ms=0;
  for(var e in d.delta){if(mu[e]!==undefined) ms-=d.delta[e]*mu[e];}
  d.charges.forEach(function(c){var v=c.e0+c.q*eF+ms;if(v<me)me=v;});
  return me;
}

function drawFE(mu){
  cx.clearRect(0,0,W,H);

  // Grid & axes
  cx.strokeStyle="#ddd";cx.lineWidth=1;
  for(var i=0;i<=5;i++){var x=xPx(i*BG/5);cx.beginPath();cx.moveTo(x,P.t);cx.lineTo(x,H-P.b);cx.stroke();cx.fillStyle="#666";cx.font="12px Arial";cx.textAlign="center";cx.fillText((i*BG/5).toFixed(1),x,H-P.b+18);}
  var step=(maxY-minY)/8;
  for(var i=0;i<=8;i++){var y=yPx(minY+i*step);cx.beginPath();cx.moveTo(P.l,y);cx.lineTo(W-P.r,y);cx.stroke();cx.fillStyle="#666";cx.textAlign="right";cx.fillText((minY+i*step).toFixed(1),P.l-8,y+4);}
  cx.strokeStyle="#bbb";cx.lineWidth=1.5;
  cx.beginPath();cx.moveTo(P.l,P.t);cx.lineTo(P.l,H-P.b);cx.lineTo(W-P.r,H-P.b);cx.stroke();
  cx.fillStyle="#555";cx.textAlign="center";cx.fillText("E - E_VBM (eV)",W/2,H-2);
  cx.save();cx.translate(14,H/2);cx.rotate(-Math.PI/2);cx.fillText("Formation Energy (eV)",0,0);cx.restore();

  // Sort defects by formation energy at cursor E_F (default BG)
  var orderEF=(cursorEF!==null)?cursorEF:BG;
  var sorted=[];
  names.forEach(function(n,i){
    if(!hidden[n]) sorted.push({name:n,idx:i,ef:calcE(n,mu,orderEF)});
  });
  sorted.sort(function(a,b){return b.ef-a.ef;});

  // Update legend order
  var leg=document.getElementById("leg");
  sorted.forEach(function(s,i){
    var div=Array.from(leg.children).filter(function(d){return d.textContent.indexOf(s.name.replace("1_",""))===0;})[0];
    if(div) leg.appendChild(div);
  });

  var lineData=[];
  sorted.forEach(function(s,si){
    var n=s.name, i=s.idx;
    cx.strokeStyle=CL[i];cx.lineWidth=2;
    var isDoped={is_doped_str};
    cx.setLineDash(isDoped?[8,4]:[]);
    cx.beginPath();var f=true;var pts=[];
    for(var j=0;j<nEF;j++){
      var ef=j*BG/(nEF-1),e=calcE(n,mu,ef),x=xPx(ef),y=yPx(e);
      if(isNaN(y)||y<P.t||y>H-P.b)continue;
      pts.push({ef:ef,e:e,x:x,y:y});
      if(f){cx.moveTo(x,y);f=false;}else cx.lineTo(x,y);
    }
    cx.stroke();cx.setLineDash([]);
    lineData.push({name:n,idx:i,pts:pts});
  });

  // Right-edge labels: always sorted by formation energy at BG
  var rightSorted=[];
  names.forEach(function(n,i){
    if(!hidden[n]) rightSorted.push({name:n,idx:i,ef:calcE(n,mu,BG)});
  });
  rightSorted.sort(function(a,b){return b.ef-a.ef;});
  rightSorted.forEach(function(s){
    cx.fillStyle=CL[s.idx];cx.textAlign="left";cx.font="11px Arial";
    cx.fillText(s.name.replace("1_","")+" "+(s.ef>=0?"+":"")+s.ef.toFixed(2)+"eV",W-P.r+6,yPx(s.ef));
  });

  // Vertical cursor line
  if(cursorEF!==null){
    cx.strokeStyle="rgba(0,0,0,0.3)";cx.lineWidth=1;cx.setLineDash([4,4]);
    cx.beginPath();cx.moveTo(xPx(cursorEF),P.t);cx.lineTo(xPx(cursorEF),H-P.b);cx.stroke();
    cx.setLineDash([]);
  }

  cx.storedLines=lineData;
  cx.fillStyle="#333";cx.textAlign="left";cx.font="13px Arial";
  Object.keys(mu).forEach(function(k,i){cx.fillText(k+" = "+mu[k].toFixed(4)+" eV",P.l+10,P.t+18+16*i);});
}
"""

_COMMON_JS_FOOTER = """
var tip=document.getElementById("tooltip");
cv.addEventListener("mousemove",function(ev){
  var r=cv.getBoundingClientRect();var x=ev.clientX-r.left;var ef=xInv(x);
  cursorEF=(ef>=0&&ef<=BG)?ef:null;
  if(curMu) drawFE(curMu);
  if(cursorEF===null){tip.style.display="none";return;}
  var lines=cx.storedLines;if(!lines||lines.length==0){tip.style.display="none";return;}
  var html="<b>E-E<sub>VBM</sub> = "+ef.toFixed(3)+" eV</b><br>";
  lines.forEach(function(ld){
    if(ld.pts.length<2)return;var lo=0,hi=ld.pts.length-1;
    while(hi-lo>1){var md=(lo+hi)>>1;if(ld.pts[md].ef<=ef)lo=md;else hi=md;}
    var p0=ld.pts[lo],p1=ld.pts[hi];
    if(p1.ef-p0.ef<1e-10)return;
    var t=(ef-p0.ef)/(p1.ef-p0.ef);var e=p0.e+(p1.e-p0.e)*t;
    html+="<span style='color:"+CL[ld.idx]+"'>"+ld.name.replace("1_","")+": "+(e>=0?"+":"")+e.toFixed(3)+" eV</span><br>";
  });
  tip.innerHTML=html;tip.style.display="block";tip.style.left=(x+15)+"px";tip.style.top=Math.max(5,ev.clientY-r.top-10)+"px";
});
cv.addEventListener("mouseleave",function(){tip.style.display="none";cursorEF=null;if(curMu) drawFE(curMu);});

function update(mu){curMu=mu;drawCPD(mu);drawFE(mu);
  var s="";Object.keys(mu).forEach(function(k){s+=k+"="+mu[k].toFixed(4)+" ";});
  if(s)document.getElementById("muinfo").innerHTML=s;
}

var leg=document.getElementById("leg");
names.forEach(function(n,i){
  var d=document.createElement("div");
  d.innerHTML="<span style='display:inline-block;width:12px;height:12px;border-radius:3px;background:"+CL[i]+";margin-right:4px'></span>"+n.replace("1_","");
  d.onclick=function(){hidden[n]=!hidden[n];d.style.opacity=hidden[n]?".4":"1";if(curMu)drawFE(curMu);};
  leg.appendChild(d);
});

update(curMu);
</script></body></html>"""


def _html_template(
    *,
    host_name: str,
    n_vertices: int,
    poly_2d: list[list[float]],
    vertex_mu: list[dict[str, float]],
    vertex_names: list[str],
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
) -> str:
    """Render the self-contained interactive HTML page."""
    js = json.dumps

    cpd_hints = {
        1: "Single chemical condition (no dragging)",
        2: "Drag along the line to change chemical potentials",
        3: "Drag inside the triangle to set chemical potentials",
        4: "Drag inside the polygon to set chemical potentials",
    }
    cpd_hint = cpd_hints.get(n_vertices, "Drag to set chemical potentials")

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

    cpd_js = _cpd_canvas_js(
        n_vertices, poly_2d, vertex_mu, vertex_names,
        ax0, ax1, a0_range, a1_range,
    )

    fe_canvas = _FE_CANVAS_JS.replace("{is_doped_str}", is_doped_str)

    return (
        _COMMON_HTML_HEAD.format(title=host_name, cpd_hint=cpd_hint)
        + "\n"
        + _COMMON_JS_DECLS.format(
            def_json=js(defects),
            ref_json=js(ref_mu),
            colors_json=js(colors),
            bg=cbm,
            names_json=js(sorted_names),
        )
        + "\n" + cpd_js + "\n" + fe_canvas + "\n" + _COMMON_JS_FOOTER
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
        vertex_mu, vertex_names, host_name, vertex_elements = _extract_vertex_data(
            cpd
        )
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

    all_ax0 = [p[0] for p in poly_2d]
    all_ax1 = [p[1] for p in poly_2d]
    pad = 0.3

    html = _html_template(
        host_name=host_name,
        n_vertices=n_vertices,
        poly_2d=poly_2d,
        vertex_mu=vertex_mu,
        vertex_names=vertex_names,
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
    )

    out_path = system_dir / "formation_energy_interactive.html"
    out_path.write_text(html)
    return out_path
