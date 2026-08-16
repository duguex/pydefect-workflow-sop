---
name: vasp-sop-report-canvas-sizing
description: "Diagnose and change chart/text size in the crisp webui formation-energy reports: canvas backing-vs-display scaling, the window→iframe width chain, font-px constants, and the docked-readout current contract. Use when the user says 图太小/字太小/画布大一点/改布局 in the vasp-sop interactive report."
---

# vasp-sop formation-energy report: canvas sizing & layout ops

Operates the interactive report (`vasp_sop/report/interactive.py` emits a self-contained HTML; consumed by the crisp webui `/reports` iframe). Use when the user complains 图太小/字太小/画布大一点/布局不对.

## How text size actually works (the key gotcha)

- `layout()` sets the canvas backing store to `display × devicePixelRatio`, and the JS draw code draws fonts in **backing px = display px** (dpr=1). So **font constants in the templates ARE the displayed size**.
- "图上的字太小" is a **display-area** problem, not a resolution problem. Two levers: (a) make the canvas bigger (width chain below), (b) bump the hardcoded font constants in the JS templates (they live in `interactive.py`: FE axis/tick `cx.font="14px Arial"`, E_F label `cx.font="bold 16px Arial"`, CPD grid `cctx.font="13px Arial"`, vertex `cctx.font="600 14px Arial"`, axis titles `cctx.font="14px Arial"`).
- After any font/layout change: regenerate ALL 10 reports (`generate_interactive_html` over `2026_undergo_spin_defect`), run `pytest tests/test_report_interactive.py`, and headless-verify (Playwright in `crisp/frontend`, `import {chromium} from "playwright"`).

## The width chain (webui)

```
window → .crisp-main--wide (max-width:none since 887162c; full window, no cap)
       → reports layout: sidebar minmax(228px,272px) + gap 20 → detail col = the iframe
       → report body padding 14px×2
       → .report-grid (2 equal cards; stacks to 1 col ≤ 800px)
       → card body padding 12px×2 → canvas
```
- FE canvas height in `layout()`: `fh = max(360, min(640, round(fw*0.72)))`.
- iframe height cap: `.crisp-reports-frame{height:min(94vh,1400px)}`.
- Measured at 1680 window (2026-08-13): FE 627×451, CPD 520×520; at 1920: FE 747×538.

## Readout widget current contract (2026-08-13, after many flips)

- Docked over the ENTIRE CPD card (`#tip` is a direct child of `.report-grid{position:relative}`, sized to `#cpdCard` offsetClientWidth/Height by `dockTip()`), opaque, all visible defects at cursor E_F, DESCENDING (highest first — matches chart top=high; this flipping direction was a real bug once).
- Show: pointer in the FE plot (`plotEl` mousemove) → dock + fill; Hide: leave FE plot (`mouseleave`, guarded by `hoverCapable = matchMedia("(hover:hover)")` so touch compat-mouseleave doesn't kill a tap-docked panel).
- Scroll: **wheel over the FE chart** forwards to `tip.scrollTop` (the panel sits on the CPD card, so the pointer can't hover it without leaving FE). Touch: tap FE toggles dock.
- DO NOT reintroduce follow/clamp/freeze (`showTip/hideTip/tipHover/tipTimer` were removed in c18af2f); the old ones caused the "挡住右边/快速移动粘住" complaints.
- Invariant: `hidden[n]||!isIntrinsic(n)` must never re-enter `calcFermi` (display hiding must not change E_F).

## Verification recipe

1. `python3 -m pytest tests/test_report_interactive.py tests/test_analysis.py -q` (53 passed + skips).
2. Regenerate all 10 under `2026_undergo_spin_defect`.
3. Playwright (cwd `crisp/frontend`): `await page.goto("file://.../Y2Ti2O7/formation_energy_interactive.html")`; hover FE → `#tip` display block, rows>0; leave FE → `none`; wheel → scrollTop>0; assert `page.on('pageerror')` empty.
4. WebUI deploy: `npm run build` + `systemctl --user kill -s SIGKILL crisp-gui && systemctl --user start crisp-gui` (SIGTERM hangs; never `restart`). Verify served bundle hash + measure canvas via the frame.
