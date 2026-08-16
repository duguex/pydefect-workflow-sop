---
name: formation-energy-report-design-contract
description: Capture and implement an approved vasp-sop formation-energy report visual/interaction contract without conflating scientific state and display state.
---

# Report visual-redesign decision capture

Use when a user has settled the visual and interaction contract for the vasp-sop formation-energy interactive reports after a design/grilling session.

## Capture implementation-ready decisions

Record the decisions in `vasp_sop/CONTEXT.md` only as domain language when a durable term is resolved. Do not write implementation design into the glossary.

For the agreed 2026 report redesign, preserve these distinctions:

- **Formation-energy inspector** is a persistent readout, not a tooltip and not a new calculation.
- It lists all currently visible defects at one selected Fermi level, ordered by formation energy.
- Hover can preview; click pins; Escape unpins.
- Legend visibility controls only display state and MUST NOT affect the intrinsic charge-neutrality calculation.
- CPD vertex identity applies only within an explicit pixel proximity threshold; otherwise call the condition an interior or boundary interpolation.

## 2026 已定契约（formation-energy-report-redesign 交付基线）

- 双等宽 report card，保留原生图表纵横比（不强制等高 canvas）。
- **CPD canvas**: 石墨色边界、淡绿稳定区、`V1…Vn`、一个绿色选中点；相与 μ 值放常驻选择卡，不做密集画布标注。
- **形成能 canvas**: 无常驻右缘标签；默认全曲线可见、稳定科学色板、绿色本征缺陷电荷中性 E_F；E_F 标在画布内（右缘附近向左绘）。
- **Inspector**: 260px 桌面右栏、可滚动、所有**当前可见**缺陷按 E_f 升序；hover 预览 E_F；click pin；Escape unpin；窄屏移到画布下方。
- **Legend**: 固定排列 + 支持单/整类过滤；可见性过滤只改渲染与 inspector 行，`calcFermi` 必须永远用全部本征缺陷（不触碰电荷中性计算）。

## Implementing after approval

1. Read `vasp_sop/report/interactive.py`（模板/canvas JS/footer）and existing report tests.
2. Treat the report artifact and the crisp frontend shell as separate deliverables:
   - report generator owns CPD/formation-energy canvas, inspector, readout, responsive layout;
   - crisp frontend owns only the outer ReportsDashboard shell and iframe presentation.
3. Add behavioral tests for inspector sorting/visibility, pin/unpin, legend independence from Fermi calculation, and vertex-threshold language (`tests/test_report_interactive.py`).
4. Build and regenerate only the approved report scope; for this contract, 2026's 10 systems first (`generate_interactive_html`).
5. Verify: `python3 -m pytest tests/test_report_interactive.py tests/test_analysis.py -q` + `python3 -m py_compile vasp_sop/report/interactive.py`; dense-report browser-smoke (two report cards, selection card, inspector row count, hover, click pin, Escape unpin, no `pageerror`).
6. For outer shell changes: `npx vitest run src/routes/Reports.test.tsx`, then `npm run build`; restart `crisp-gui` SIGKILL-then-start (normal restart can hang). Verify `/reports` defaults to the visible 2026 root, not an off-filter fallback.
7. Commit vasp-sop and crisp frontend (worktree) changes separately.