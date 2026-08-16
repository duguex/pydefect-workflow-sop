---
name: vasp-sop-cpd-topological-map
description: "Operate or extend the vasp_sop interactive-report CPD widget: the topological N-dim stability-polytope map (exact hull, face-level exact selection, per-element μ sliders). Use when touching drawCPD/buildHull/hullState/pickMu/layout code, regenerating reports, or re-validating the stability-region map in vasp_sop/report/interactive.py."
---

# vasp-sop CPD topological map — contract & verification

## Design contract (settled 2026-08-15, commits 598cc7b + df310e7)

- The stability region is drawn as a TOPOLOGICAL map of the exact N-dim convex polytope, not a 2D projection:
  - `POLY` (2D projection) is ONLY the spring-layout seed; layout is deterministic (120 iterations, cooling t*=0.95, repulsive k²/d + edge springs d²/k).
  - `EDGES` = true N-dim edges: vertex pairs whose co-facet intersection is exactly the pair (all facets containing both share only those two). NOT the complete graph.
  - `FACET_HULLS` = 2D hulls (monotone chain) of each facet's vertex set in layout space — used for filling and hit-testing.
- Selection is face-level exact:
  - vertex click (≤14px) → exact vertex μ, mode "当前顶点 Vn"
  - facet-region click → N-dim barycentric of that facet's vertices (weights from the 2D drawing), mode "区域内插值"
  - edge click (≤8px) → endpoint interpolation, mode "边界插值"
  - Every canvas selection is inside the polytope by construction; 区域外 can only come from the per-element sliders.
- `buildHull` (exact, facet-enumeration):
  - affine basis via Gram-Schmidt over vertex differences; relative tol = 1e-4·maxNrm computed BEFORE the basis loop
  - facet candidates = r-subsets of vertices; normal seed = SUM OF ALL VERTEX COORDS (a single vertex seed is degenerate at origin)
  - accept only planes with all vertices on one side (±tol); flip orientation if all on minus side; reject if it cuts the hull
  - NON-SIMPLICIAL FACETS: k>r coplanar vertices enumerate once per r-subset — MUST dedupe by plane key (n.toFixed(5)+c.toFixed(5))
- `hullState(mu)` = min signed facet distance (positive inside). Near-coplanar hulls give distance noise up to ~0.02 eV; inside/outside verdict stays exact.
- Keep `selectionMode` display state separate from `calcFermi`/physics. f-string JS in interactive.py doubles braces {{ }}.

## Host-space geometry & tolerance split (region-diagram contract, commit 6b842b0)

- **Host-element subspace only**: build the hull over HOST_ELS (from CPD vertex_elements), NOT all vertex_mu keys. Impurity elements (Bi, Fe, Mn…) are a per-vertex branch of the impurity equilibrium, not a region dimension — including them inflates rank with sliver facets and empties facet phase intersections (Y2Ti2O7 went r=3 instead of its true r=2).
- **RTOL vs HTOL split**: rank tolerance coarse `1e-3*maxNrm` (absorbs meV batch drift — BaAl4O7_mp1019534 vertices sit 1.4 meV off the true plane); facet fit + verdict tolerance fine `1e-4*maxNrm` inside the projected subspace. Do not merge them.
- Per-rank drawing:
  - **r=2** (3 host elements): spring layout seeded by the true region shape; real regions can be extreme slivers (2nd singular value down to 0.006×1st) — metric drawing collapses them to a line. EDGES = polygon cycle from the seed hull; FACET_HULLS = fan of the final layout hull for interior clicks.
  - **r=3** (4+ hosts): OUTER = 2D hull of the layout with collinear intermediate vertices chained; every outline edge is a true polytope edge (verified on 13 live systems) — draw outline dark/thick, interior edges faint.
  - **r=1** (binary): segment + endpoint phase labels. **r=0** (unary): static text.
- **Boundary labels**: edge phases = intersection of endpoint `VPHASES[i].competing` lists (1 phase on 2D-region edges, 2 on 3D-region edges); empty intersection = no label (batch drift); label drawn offset from the edge midpoint toward the region centroid (white pill).

## Canvas mapping invariants (canvas-mapping discipline, commit 68e1a49)

Any iteration touching selection or marker mapping must preserve:

1. **triBary weight order** — `v0 = c−a`, `v1 = b−a`; the computed `v` is the weight of **c**, `w` is the weight of **b**. MUST return `[1−v−w, w, v]` (not `[1−v−w, v, w]` — that silently mirrors every facet interpolation; symmetric points like centroids hide the bug).
2. **markerPos must be the exact inverse of pickMu** — never a global affine fit (the spring layout is not an affine image of the region; offsets reach hundreds of px). Locate `u = HULL.proj(mu)` in the true-space face fan (facets, then the drawn-hull fan) and map barycentric weights through the drawn polygons.
3. **pickPath provenance** — `pickMu` records `vertex|edge|facet|fan|slider`; `markerPos` maps fan selections through the drawn-hull fan FIRST (a fan mu can lie exactly on a facet plane). Slider handler resets `pickPath="slider"`.
4. **Sliver-facet handling** (meV batch drift warps near-coplanar facets so a mu matches several facets' 2D projections):
   - buildHull: dedupe facets by VERTEX SET (sorted join), then drop subfaces (verts ⊂ another facet's verts). Verdict-unaffected — same plane distances.
   - `faceWeights`: plane-residual gate `sqrt(res) <= 0.1*HULL.tol` (u must be ON the facet plane, not merely inside its 2D projection).
   - display faces (`FACET_VERTS`): filter to phase-bearing facets (vertex competing-phase intersection non-empty).
   - candidates (pickMu + markerPos): largest `drawnArea(F)` wins.
5. **区域外 markers** — `hullState(mu).inside === false` ⇒ skip face lookup, pull the marker onto the drawn boundary (`closestBoundary`), red ring + 区域外 card. A marker must NEVER sit inside the polygon for an outside mu.
6. **Gap clicks** (r≥3, click inside drawn hull but between drawn facets): interpolate in the drawn-hull fan (silhouette vertices) — the mu maps back to exactly the click position.

## Known validation results (13 systems, 2026-08-15)

- Verdict-level vs scipy Delaunay: 0 flips off-boundary; flips only within ±0.0014 eV of facets (float boundary artifacts, not bugs). Cross-validation band ±0.013 eV (find_simplex artifacts — `find_simplex` classifies exact-boundary points as outside by its own tolerance).
- Facet-set equality vs scipy FAILS legitimately:
  - Qhull `Qt` triangulates non-simplicial facets (La2SrSc2O7: 13 py facets = 11 unique planes + 2 triangulation splits)
  - Qhull merges near-coplanar sliver facets (BaAl2B2O7: 18 py vs 36 js planes; ≤0.02 eV, verdicts unaffected)
- Compare UNIQUE PLANES, not simplex counts, when re-validating. r=2 facet sets must match scipy ConvexHull exactly (10/10); r=3 may differ by Qhull processing — judge by verdicts instead.

## Regeneration & verification

```bash
vasp-sop report <system_dir> --interactive   # per system under /mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect/
python3 -m pytest tests/test_report_interactive.py   # 39 pass, 4 skip baseline; asserts HOST_ELS/outerSegments/edgePhases/drawEdgeLabel, no fillText("V")
```

- Batch is LIVE: skip systems lacking `defect/defect_energy_summary.json` (analyze reruns), retry later.
- Playwright (conda env dgkan_rocm_3.11 or autoresearchclaw) on `file://` URL: no JS errors, click vertex 0 → "当前顶点 V1", click facet midpoint → "区域内插值", drag stays inside, slider min → 区域外 card.
- Verification battery:
  - **Drag tracking**: dispatch `PointerEvent` (buttons:1 on moves!) along rejection-sampled points with `insideDrawnHull(p, order)` (>30px spacing; bbox corners lie outside non-rectangular hulls!); assert `markerPos(curMu)` within 0.5px of each cursor point (vertex snaps ≤14px by design).
  - **Outside-marker on boundary**: sample random μ's outside the region; assert marker distance to drawn boundary ≤ 0.01px.
  - **Verdict cross-validation**: JS `hullState` vs scipy Delaunay over interior+box+near-boundary points (800 pts × 13) — 0 flips outside ±0.013 eV band; evaluate `hullState(x).dist` on JS side; use HTML-embedded VERTEX_MU (live batch files drift!).
  - **Roundtrip**: random canvas points → `pickMu` → `markerPos` — exact for face/fan selections; classify by `selectionMode` before measuring (vertex snaps and outside clicks legitimately don't roundtrip).
- Playwright gotchas: `page.goto` before ANY evaluate (else page vars undefined on about:blank — check `pg.url` first); `evaluate` with an arg runs in a different/main world; bounding rect keys are dict `box['x']`; canvas sizing `layout()` sets backing = cw·dpr + `setTransform(dpr,…)`, pointer coords (CSS px) and drawing coords consistent — do not add manual scaling.
- WebUI serves on-disk HTML per request (ADR 0005): regenerate = propagate, no crisp-gui restart.
- 10% alpha fills look blank in ASCII art — census pixels, don't trust thresholds. Extract generated HTML and eyeball the JS when f-string braces are suspected.

## Dead machinery (do not resurrect)

_bary_js, _ear_clip, 2D axis params (ax0/ax1/a0_range/a1_range), BARYS/TRIS arrays, projectToEdge, getMu.