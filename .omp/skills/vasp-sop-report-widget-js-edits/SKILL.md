---
name: vasp-sop-report-widget-js-edits
description: "Edit the embedded canvas-widget JS in vasp_sop/report/interactive.py (floating hover readout, CPD/formation-energy canvases) without breaking it: hover-race across a wrapper, Chromium touch compat mouseleave, var ordering inside the triple-quoted template, and (hover:hover) media gate. Use when changing the interactive report's in-page interactions."
---

# vasp-sop report widget JS edits

The interactive formation-energy report is one self-contained HTML emitted by `vasp_sop/report/interactive.py` (triple-quoted Python strings: `_COMMON_HTML_HEAD`, `_FE_CANVAS_JS`, `_FERMI_JS`, `_COMMON_JS_FOOTER`). All JS is wrapped in `.replace()`-friendly templates; CSS/JS braces are doubled (`{{ }}`) inside f-strings. Iterate: edit → `py_compile` → run `tests/test_report_interactive.py` → regenerate → headless Playwright verify.

## Order of verification (do all)
1. `python3 -m py_compile vasp_sop/report/interactive.py` — catches broken triple-quote/delimiter edits fast.
2. `python3 -m pytest tests/test_report_interactive.py -q` — string-contract assertions on the emitted HTML.
3. Regenerate all systems: loop `generate_interactive_html(dir)` over `2026_undergo_spin_defect/*`.
4. Headless Playwright (from `crisp/frontend`, package has playwright): load `file://…/formation_energy_interactive.html`, drive `#cv`/`#cpd` mouse/touch, listen `page.on("pageerror")` — a single `pageerror` usually means the whole footer script died (nothing after the throw runs, e.g. legend never renders).

## Traps that have burned this widget
- **Footer-scope ordering**: top-level `var` hoisting means `var leg=document.getElementById("leg")` is `undefined` until that statement executes. Referencing `leg` earlier in the same footer (e.g. `leg.addEventListener(...)`) throws and kills every subsequent statement — legend never builds, and the symptom is "`.leg-cat` not found" in Playwright while the JS error says "Cannot read properties of undefined". Use `document.getElementById("leg")` inline instead of relying on a later `var`.
- **Hover-race (scrollable floating panel)**: a tooltip at an offset from the cursor leaves a dead gap — pointer leaves canvas, panel hides before it can be scrolled. Solve by making the canvas wrapper (`.fe-plot`) the single hover owner: listen `mousemove`/`mouseleave` on the wrapper (both canvas and panel are children), and gate `mousemove` on a `tipHover` flag set by the panel's `mouseenter`/`mouseleave` so the panel can scroll without fighting position. Pointer gliding canvas→panel stays inside the wrapper → no hide.
- **Touch compat `mouseleave`**: Playwright's `touchscreen.tap` (and real Chromium touch) synthesizes `mouseenter → mousemove → touchstart/touchend → click → mouseleave`. The trailing `mouseleave` re-triggers "left the plot" logic and instantly kills a tap-pinned summary. Gate hover hide-leave with `window.matchMedia("(hover:hover)").matches`; on touch-only devices the tap handler owns visibility (toggle).
- **`(hover:hover)` media gate**: use one `hoverCapable = window.matchMedia("(hover:hover)").matches` for both "follow cursor on mousemove" and "hide on mouseleave"; attach the tap toggle only when `!hoverCapable`.
- **Legend-click interception**: the floating panel (absolutely positioned) can overlay legend rows → Playwright `locator.click` times out on the covered element. In verification, hide the panel (move pointer away) before clicking legend.
- **Clipped canvas labels**: right-edge annotations get cut; anchor-to-plot-interior by measuring text and flipping `textAlign`/side when `x+gap+w > W-P.r`.

## Domain term (2026-08-13)
The readout is **形成能悬浮读数 (formation-energy hover readout)** in CONTEXT.md. The retired fixed right-hand panel is the former "形成能检查器" — do not re-introduce that term or layout; the user prefers floating hover.
