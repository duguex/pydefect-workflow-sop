---
name: embedded-hull-geometry-verify
description: "Implement or verify exact convex-hull membership/boundary-distance JS inside a self-contained HTML report (e.g. vasp_sop interactive.py chemical-potential stability region), including the three JS pitfalls and the scipy cross-check recipe. Use when touching buildHull/hullState code or validating 区域外 judgments."
---

# Embedded hull geometry — implement & verify

Exact membership of a point in the convex hull of CPD vertices, computed in-page (no scipy in the report), plus signed boundary distance. Used by vasp_sop interactive.py `buildHull()`/`hullState()`.

## Algorithm (all in JS, vertices ≤ 10, dim ≤ 6)

1. **Affine basis**: orthonormalize differences from vertex 0 via Gram-Schmidt. Keep a direction only if `norm > 1e-4 × maxDiffNorm` (RELATIVE threshold — SVD singular values like 9e-6 on data of scale 10 are float noise; absolute 1e-7 keeps them and Gram-Schmidt cancellation produces garbage axes → wrong hull).
2. **Facet candidates**: every r-subset of vertices (r = affine rank; ≤ C(10,5)=252 combos, typically ≤ 56). Facet span = orthonormalize the subset's differences (keep `norm > tol`); normal seed = SUM of ALL vertex coordinates (NOT U[F[0]] — if F[0] projects to the origin the seed degenerates to zero and the facet is silently dropped). Orthogonalize seed against span, normalize, offset at U[F[0]].
3. **Facet accept**: compute signed distances of ALL vertices; if `min < -tol && max > tol` → plane cuts the hull, reject; if all on minus side → flip; else push. tol must be scale-relative (1e-4 × maxNrm) — facet vertices scatter ~1e-4 around their plane after GS, an absolute 1e-5 rejects true facets.
4. **State**: `hullState(mu)`: inside = all facet distances ≥ −tol; dist = min signed distance (positive inside, negative outside). r=0: distance from origin. r=1: segment clamp.

## THE JS PITFALL THAT COST AN HOUR

`var HULL=buildHull(),HTOL=1e-5;` — var hoisting makes HTOL `undefined` INSIDE buildHull() (assigned after the call). `mn < -undefined` is always false → facets never rejected, never flipped → garbage normals, 56 junk facets for an 8-vertex hull. Define tolerances INSIDE the function before use, expose via the returned object (`HULL.tol`).

## Verify — never against live files

The batch loop may re-run CPD/analyze between your reference computation and the browser check; chem_pot_diag.json changes → false mismatches. Extract the ground-truth input from the generated HTML itself:

```
re.search(r"var VERTEX_MU = (\[.*?\]);\nvar selectionMode", html, re.S)
```

Python reference (trusted): membership via `scipy.optimize.linprog` feasibility (A_eq = [V.T; ones], b_eq = [mu; 1], λ ≥ 0); boundary distance via `ConvexHull` on SVD-projected vertices (r = singular values > 1e-4·s0, same relative rule).

Test points: all 2^N slider-box corners + every vertex + centroid. Verdicts must match EXACTLY (0 flips). Distances: exact (<2e-4) on well-conditioned hulls; hulls with near-coplanar vertices deviate by up to the near-coplanarity gap (~0.02 eV) — the facet SET still matches (compare per-vertex signed residuals), it's conditioning, not a logic bug. Document this in the code comment.

## UI wiring (vasp_sop)

- `updateSelectionCard`: 逐元素 mode → 区内 `距边界 +x.xx eV` / 区外 `区域外 · 超出稳定区 x.xx eV`
- Canvas selection dot ring: red `#e11d48` when outside, white inside
- The 2D polygon remains the visual reference only — the label/ring use the N-dim truth
