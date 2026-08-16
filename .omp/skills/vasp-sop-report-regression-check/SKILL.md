---
name: vasp-sop-report-regression-check
description: "Verify the vasp-sop formation-energy interactive report and its webui shell haven't regressed: feature-marker greps, headless browser assertions (descending readout, E_F invariant, hover/legend/touch), and the side-by-side width constraints."
---

# vasp-sop interactive report regression check

Use when the user asks "回滚了吗 / 其他地方回滚了吗 / 检查一下" about the formation-energy interactive report, or after any `vasp_sop/report/interactive.py` / crisp-reports-frontend change. The report is generated per system dir (`formation_energy_interactive.html`, served by `/api/v1/reports/html` per-request — regenerate = deploy; no webui restart for report-only changes).

## 1. Feature markers in a generated report
Regenerate first (`generate_interactive_html`), then grep the newest HTML:
- Present: `defectBase`, `toggleGroup`, `function fillTip`, `fe-tip`, `fe-note`, `selectedVertex`, `selection-card`, `drawFermi`, `rowHtml`, `@media(max-width:800px)` (side-by-side breakpoint)
- ABSENT: `pinnedEF`, `fe-inspector`, `renderInspector` (retired right panel), and exactly `hidden[n]||!isIntrinsic(n)` must not appear (display hiding never enters calcFermi)

## 2. Browser assertions (headless, playwright from crisp/frontend)
- Hover readout is DESCENDING E_f (first row = highest energy; matches chart y-axis where high = top)
- Toggle a whole legend group off/on → `calcFermi(curMu)` unchanged (physics/display separation)
- Legend group titles present (基名分组) with count hint
- pointer canvas→panel: tip persists (scrollable); plotEl wrapper shares hover state. pointer onto legend: tip hides and stays hidden (mousemove guard = pointer must be inside canvas rect)
- touch emulation (`hasTouch: true`): tap shows Top5 + "共 N 条 · 再次点击收起", tap again dismisses (guard compat mouseleave with `hoverCapable`)

## 3. Side-by-side layout
- Report: `.report-grid` two columns when body ≥ 800px
- webui: without `.crisp-main--wide` on `<main>` the iframe stays ~748px (`.crisp-main` caps at `--bp-wide:1080px`, minus 272px sidebar) → reports route must add the wide class or the two cards always stack
- Deploy webui: `kill -9` then `systemctl --user start crisp-gui` (systemd stop hangs); verify served `assets/index-*.js` hash = newest build

## 4. Git truth
Feature commits: 6f083b1 redesign, c4b4bb0 floating readout, c08d849 review fixes, 46300e9 800px breakpoint, 74f210d descending order. There is NO revert commit — a "rolled back" look is almost always a silent behavior flip (the sort direction on 2026-08-13 was the case). Verify with `git log --oneline -- vasp_sop/report/interactive.py` first.
