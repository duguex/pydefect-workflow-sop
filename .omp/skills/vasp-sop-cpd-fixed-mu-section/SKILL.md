---
name: vasp-sop-cpd-fixed-mu-section
description: "Operate or extend the fixed-mu constrained-subspace section view in the vasp-sop interactive CPD report (vasp_sop/report/interactive.py): FIXED/SECTION state, buildSection edge×hyperplane geometry, pickSection/muFromSection contracts, secPos clamping, verification battery. Use when iterating the 固定 button, section polygons, constraint labels, or section selection."
---

# Fixed-mu constrained-subspace section (vasp-sop interactive CPD)

Feature committed 0b51e1b (2026-08-15). User ask: "让元素化学势可固定，绘制固定化学势约束下的子空间" — fix element μ → draw the section of the stability polytope.

## UI
- Each mu slider row (buildMuPanel) ends with a 固定 button (`.mufix`); click → `FIXED[e]=curMu[e]`, slider disabled, button .on; re-click → `FIXED[e]=null`, rebuild SECTION, `update(curMu)`. Rows store {mn,mx,slider,fix}.
- Panel title mentions the 固定 affordance. Selection card shows fixed constraints + section dimension in section mode.

## Geometry (buildSection)
- For each ALL_EDGES edge: solve t such that all fixed elements hit their value (t must be consistent across fixed elements within 1e-6); intersection μ = full vector incl. impurity, linearly interpolated. Dedupe by free-element key. d = HOST_ELS.length-1-n_fixed.
- d=2 → per-axis normalized xy + hull2D order; d=1 → sorted segment; d=0 → point; empty (no intersections) → 约束下无稳定区 message.
- sectionLabel(oi,oj): the target facet containing ALL 4 endpoint indices of both support edges → competing-phase intersection over its vertices. Empty when drift.
- pickSection: vertex snap (14px) → polygon fan triBary → edge interp → nearest vertex fallback. Returns muFromSection(w).
- **muFromSection trap**: skip fixed elements via `if(SECTION.cv[e2]!==undefined)continue;` — checking `mu[e2]===undefined` short-circuits after the first point (wrong barycentric μ).
- **secPos trap**: `var fy=mu[SECTION.free[1]]` — the VALUE; using the element name yields NaN markers.
- pickPath provenance: vertex/facet/edge — markerPos routes to secPos whenever SECTION is set (incl. empty).

## Verdict & clamping
- hullState unchanged (full-space hull). Outside-μ marker pulled to section boundary (red ring + 区域外).

## Engineering discipline
- All JS in interactive.py lives inside f-string templates: EVERY single brace (incl. comments) must be doubled `{{ }}` — a mixed-brace insert fails at import with `f-string: expecting '}'`.
- After ANY change: `python3 -m pytest tests/test_report_interactive.py` + regenerate ALL 13 systems (`vasp-sop report <dir> --interactive`) — stale HTML silently masks fixes (bitten by muFromSection: page behaved old, source was fixed).
- Verification battery: fix at vertex value → degenerate section ok; fix at vertex mean → polygon with n = edge intersections; fix out of range → empty; click round-trip marker delta ~1e-12 px; free slider to min → marker clamped to boundary, inside=false; unlock → 3D topology map restored (sliders enabled).}
