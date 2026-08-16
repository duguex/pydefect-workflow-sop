---
name: vasp-sop-interactive-report-regen
description: "Regenerate and verify vasp-sop interactive formation-energy reports (formation_energy_interactive.html) for the whole 2026 batch after generator changes: run tests, regenerate ALL systems, restart crisp-gui, headless-chromium verify per-system Fermi/constraint rendering. Use when editing vasp_sop/report/interactive.py or pydefect_adapter.py CPD/defect rendering."
---

# vasp-sop interactive report regeneration

Trigger: after changing `vasp_sop/report/interactive.py` (FE/CPD canvas JS, constraint labels, charge-neutrality Fermi) or `vasp_sop/defect/pydefect_adapter.py` CPD parsing.

## Key facts

- Generator entry: `generate_interactive_html(system_dir)` writes `formation_energy_interactive.html` per system. It reads `defect/defect_energy_summary.json` + `cpd/chem_pot_diag.json` + `cpd/target_vertices.yaml`.
- **CPD vertex constraint phases (`competing_phases`) live ONLY in `cpd/target_vertices.yaml`** — the summary's `rel_chem_pots` is flat (per-vertex μ only). The adapter (`cpd_diagram`) merges phases in; if constraints vanish after a refactor, check that merge.
- Vertex records nest μ under `chem_pot`; `_extract_vertex_data._mu_dict` must accept both nested and flat forms.
- Charge-neutrality Fermi (user-approved): intrinsic-defect-only balance `Σ q·exp(-E_f/kT)=0`, kT=0.0259@300K, T=300K. Exogenous (dopant) defects excluded by `plan.yaml dopant_elements` prefix (e.g. `Bi_*`). No intrinsic-carrier term.
- **User preference: constraint label is uniform across doped/undoped systems — list only competing constraint phases per vertex ("顶点 X: A · B · C（约束）"), NEVER the impurity/unstable phase section.** Doped systems must not show extra "不稳定" lines.
- The FE canvas must NOT print per-element μ (overlaps the mupanel).

## Procedure

1. `python3 -m pytest tests/test_report_interactive.py tests/test_analysis.py -q` (also full suite before commit).
2. Regenerate ALL systems — single-system regeneration leaves stale files elsewhere:
```python
from vasp_sop.report.interactive import generate_interactive_html
from pathlib import Path
for s in sorted(Path(ROOT).iterdir()):
    if s.is_dir(): generate_interactive_html(s)
```
ROOT = `/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect` (10 systems).
3. Restart crisp-gui: `kill -9 $(pgrep -f crisp-gui)` then `systemctl --user restart crisp-gui` (systemd stop alone hangs on this service).
4. Verify in headless chromium per system (playwright from `~/crisp/frontend`):
   - open `file://` URL of the HTML (no server needed)
   - `page.evaluate(() => calcFermi(curMu))` → expect a number ~1–2.5 eV
   - read `#cpdconstraints` textContent → expect "顶点 X: ...（约束）", no "不稳定"
   - collect `pageerror` events → zero
5. WebUI viewer: hard refresh (Ctrl+Shift+R) — iframe is cached; bundle hash changes are invisible without refresh.

## Pitfalls

- URL-embedded credentials (`http://user:pass@host`) break page fetch() ("Request cannot be constructed from a URL that includes credentials") — use `page.setExtraHTTPHeaders({Authorization: "Basic " + btoa(...)})` when verifying served pages.
- The JS blocks are Python triple-quoted strings with `{{ }}` escaping; a bare `//` comment line outside a string breaks module syntax — after edits run `python -c "import ast; ast.parse(...)"`.
- `_extract_vertex_data` returns 5-tuple (added `vertex_phases`) — update all unpack sites (tests included).
