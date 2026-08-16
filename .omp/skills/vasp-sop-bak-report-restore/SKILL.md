---
name: vasp-sop-bak-report-restore
description: Restore formation_energy_interactive reports from .big_sc_bak backups without poisoning the vasp-sop batch state machine (never copy summaries into defect/). Use when 备份能否替代/恢复旧报告/充分利用已有资源 for 2025/2026 batch systems.
---

# Restore formation-energy reports from .big_sc_bak without poisoning the batch state machine

## When to use

A system's `formation_energy_interactive.html` is missing/stale but `.big_sc_bak/` holds an archived defect analysis (supercell-switchover leftovers). Operator wants to "充分利用已有资源" and show the archived results now, while the small-supercell batch continues. Trigger phrasing: "备份能否替代", "为什么这个图还是旧的", "备份能替代吗".

## Critical invariant — NEVER touch defect/

`core/orchestrator.py` gates defect submission/retry on `defect/defect_energy_summary.json` **NOT existing** (wave2 submit block, ~line 621). Copying an old summary into `defect/` permanently freezes retries for unconverged dirs and blocks the real analysis. The state machine is a red line.

## Substitution evaluation first (should-you-restore verdict)

For each system under `/mnt/shared/home/2sidesniddle/vasp/<root>/<system>`:

1. **Inventory the backup**: `ls <sys>/.big_sc_bak/` —
   - `defect_energy_summary.json` (full archive)
   - `.recompute_bak` suffix (retired before recompute = judged stale)
   - empty dir (no archive) — e.g. BaS — skip
   - reset markers (`remove [defect_energy_summary.json] to reset` = deliberately reset)
2. **Parse the backup** with the adapter: `from vasp_sop.defect.pydefect_adapter import defect_summary; defect_summary(bak / name)` — count defects, check cbm (must succeed). Optionally check `analyze_status.json` for `status=full` + `missing_correction=[]` (quality complete).
3. **Coverage check (killer metric)**: strip charge suffix from current `defect/` dir names (`re.fullmatch(r"(.*)_([+-]?\d+)$")`) and from summary defect names; compute intersection. Exclude `perfect/` (NOT a defect — counting it as a gap is a false negative). Restoring a summary that covers only 2-6 of the current defect names silently drops the rest from the report.
4. **Supercell-size consistency**: compare POSCAR atom counts (`pymatgen Structure.from_file(POSCAR).num_sites`) — backup vs current. Mixed sizes = chemically inconsistent formation energies (finite-size effects). A size mismatch (300-atom backup vs 96-atom current) is ACCEPTED for the temporary-view restore below when the archived analysis is self-consistent (its own CPD + corrections, e.g. analyze_status full 325/325).
5. **Current-dir convergence**: `convergence_verdict(dir).converged` over `defect/` — if the batch is still converging, waiting is cheaper than restoring.

**Verdict rules**: NO if coverage <100% of current unique defect names, supercell sizes differ, or any retirement marker (`.recompute_bak`, reset marker, empty backup) exists. Only consider restore if summary covers the full current defect set AND no retirement marker. Even then, prefer waiting for the pipeline to re-run analysis unless the user explicitly overrides.
2025 batch baseline (2026-08-12): AlN 6/7, CaO 2/3, GaN 4/5, MgO 4/5, MoS2 6/6 covered; BaS no backup; AlN/CaO/MoS2 have full summaries, GaN/MgO only .recompute_bak, MoS2 has a reset marker. Verdict for all: cannot substitute.

## Restore procedure (only after evaluation passes / user overrides)

```python
import json, shutil
from pathlib import Path
from vasp_sop.report.interactive import generate_interactive_html

d = ROOT / sysname
cand = d / ".big_sc_bak" / "defect_energy_summary.json"
if not cand.is_file():
    cand = d / ".big_sc_bak" / "defect_energy_summary.json.recompute_bak"
data = json.loads(cand.read_text())
# Filter out complex defects (keys containing '+' or '.') — they belong
# to the archived supercell defect set, not the current one.
data["defect_energies"] = {k: v for k, v in data["defect_energies"].items()
                           if "+" not in k and "." not in k}

t = TMP / sysname          # temp dir: NEVER write into the system tree
(t / "defect").mkdir(parents=True); (t / "cpd").mkdir(parents=True)
(t / "defect" / "defect_energy_summary.json").write_text(json.dumps(data))
for f in ("chem_pot_diag.json", "target_vertices.yaml"):
    src = d / "cpd" / f
    if src.is_file():
        shutil.copy2(src, t / "cpd" / f)

out = generate_interactive_html(t)
shutil.copy2(out, d / "formation_energy_interactive.html")  # only file touched
```

- CPD coherence: the on-disk `cpd/chem_pot_diag.json` + `target_vertices.yaml` mtimes should match the backup era (same generation of analysis). The report generator reads those cpd files, not the backup.
- The report is a temporary view that the real analysis overwrites on completion.

## Verify

- New report: `grep -c drawSegs` > 0 (new format); DISP map has zero complex names (`+`/`.`).
- `defect/defect_energy_summary.json` still absent; `defect/` untouched.
- The report is temporary: when the small-supercell analysis completes, `analysis.py` regenerates and overwrites it automatically.

## Status-machine safety net

If you ever see a summary already in `defect/`, do NOT delete it blindly — the batch may be mid-postprocess. Check `batch_snapshot.json`/JobStore for the analysis leg first.

## Reports angle (display gap)

Old-format HTML files stay on disk until regeneration succeeds; regeneration requires all three inputs (`defect/defect_energy_summary.json`, `cpd/chem_pot_diag.json`, `cpd/target_vertices.yaml|json`). Missing inputs → generator raises ValueError, old HTML kept. The crisp webui marks such reports "已生成" without warning (missing_inputs is [] when the HTML exists) — a known display gap.