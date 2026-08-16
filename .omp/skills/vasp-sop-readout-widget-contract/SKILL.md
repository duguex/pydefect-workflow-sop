---
name: vasp-sop-readout-widget-contract
description: "Current contract of the vasp-sop formation-energy interactive report's docked readout (形成能停靠读数) and y-axis protocol: (价态, μ) tuple semantics, compound-inferred ion valences, kind colors, axis ranges. Use when iterating the readout/plot in vasp_sop/report/interactive.py — the user-authored report-readout-widget-iterate skill is stale (describes the retired follow-cursor 200px panel)."
---

# vasp-sop interactive report — readout widget & y-axis contract (2026-08-14)

Settled design in `vasp_sop/report/interactive.py` (commit ed3ac19). Iterate WITHOUT re-litigating; the user corrected these choices explicitly.

## Readout panel (形成能停靠读数)
- Docked over the CPD card, right-aligned, `READOUT_W_MIN=240 / MAX=320`, height = content (`height:auto`), `maxHeight = innerHeight − top − 10`, scroll via wheel over the FE chart. `sizeTip()` measures after `display:block` in the same synchronous block.
- Content: every visible defect at cursor's E_F, descending energy. Row = `swatch 缺陷名 (价态, μ) E_f` — tuple `(ionLabel, muLabel)`, **no "μ" prefix** in the tuple.
- 价态 = **defect ion's oxidation state**, NOT the charge state q alone (user rejected both the static element table AND the bare-q label):
  - substitution `X_Yn` → `X^(h+q)` where h = host-site valence;
  - interstitial `X_iN` → `X^q` (charge conservation);
  - vacancy `Va_Xn` → q itself.
  - host valences inferred from **charge-neutral host formula** (`_infer_host_valences`, O fixed −2, candidate list `_COMMON_VALENCES` is only for solving — never a display source). No neutral solution → `?` labels + warning.
- Notation: sign AFTER magnitude (`5+`, `2-`, `0`). μ = **absolute value** of `calc_results.json` magnetization (user: 自旋是绝对值). Missing/unconverged → `—`.
- Both tuple elements flip live when the cursor crosses a charge transition (stable q* = argmin over charges).
- **Colors: one per defect KIND** (`_defect_kind` strips site digits: Va_O1/Va_O13 → Va_O), assigned by first appearance (`_kind_colors`) — same kind shares one color in plot, legend, and panel swatches.
- Shallow defects are filtered entirely (`allow_shallow=False` gate, `_build_defects`) — they never appear in panel or plot.

## y-axis protocol (`calcGlobalYRange`)
- Sample: all charge states × all CPD vertices × E_F∈{0, BG}; `lo=min, hi=max`; pad = max(0.5, 0.1·(hi−lo)).
- **minY = lo exactly** (data minimum sits on the bottom axis — no padding below; user: 直接用最低形成能).
- **maxY = ceil(hi+pad), hard-capped at +10 eV** (`if(maxY>10)maxY=10`); degenerate guard `if(minY>=maxY)minY=maxY-5`.
- Global fixed range (one set for all vertices); the readout is independent of axis clipping (lists energies as computed — BaAl2B2O7 rows still show +12.4 eV while plot clips at 10).

## Physics/data gotchas
- Deep-negative eform: q=−3/−4 acceptors at E_F=BG under cation-poor vertices (e.g. Y2Ti2O7 Va_Ti4³⁻ = −15.67 eV; BaAl4O7_mp1019534 Va_Al2³⁻ = −4.758) — drives minY very low. Real physics suspicion, not a display bug.
- `defect_energy_summary.json` can be transiently MISSING during the batch analyze chain (system dirs like Gd2GaSbO7:Bi) — regeneration then fails with "missing interactive report inputs"; retry later, it's data-side, not code.
- Ba3W2O9/BaAl2B2O7 have panel energies >10 eV (W_Ba1 +10.3, BaB1 +12.4) — proof the readout ignores the axis cap.

## Verification
`python3 -m pytest tests/test_report_interactive.py -q` (41 passed baseline) → `vasp-sop report <system_dir> --interactive` for all 13 systems under `/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect/` → file:// Playwright (`page.mouse`, never iframe frame.mouse): sweep E_F across plot width, assert tuple format regex `^\([A-Za-z?]*(?:[1-9]\d*[+-]|0), (?:\d\.\d\d|—)\)$`, assert flips at transitions, no pageerror. Webui serves HTML from disk per request — no crisp-gui restart needed.
