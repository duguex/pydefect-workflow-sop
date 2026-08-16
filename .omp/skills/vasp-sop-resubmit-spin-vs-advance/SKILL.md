---
name: vasp-sop-resubmit-spin-vs-advance
description: "Diagnose whether repeated crisp/vasp-sop resubmits (stage2 SOC, cpd ionic restarts) advance convergence or spin in place: per-round first-F/last-F trajectory from slurm logs, CONTCAR continuation check, POSCAR.bak semantics. Use when a job loops through NSW-exhausted/TIME-LIMIT restarts without converging or user asks 重提是推进还是打转."
---

# Resubmit: advancing vs spinning in place

Given a dir in repeated crisp resubmits, decide whether the loop converges or burns core-hours in place.

## Ground truth: per-round F trajectory

```python
import re, datetime
from pathlib import Path
for log in sorted(Path('.').glob('*.log'), key=lambda p: p.stat().st_mtime):
    txt = log.read_text(errors='ignore')
    fl = re.findall(r'^\s*(\d+)\s+F=\s*([-\d.E+]+)', txt, re.M)
    print(log.name, datetime.datetime.fromtimestamp(log.stat().st_mtime).strftime('%H:%M'),
          'steps=', len(fl), 'first=', fl[0][1] if fl else '-', 'last=', fl[-1][1] if fl else '-')
```

- **Advancing**: round N+1 first F ≈ round N last F (continuation from CONTCAR worked; TIME-LIMIT truncations show this, match to ~0.01 eV).
- **Spinning**: first F returns to the same old baseline every round while last F stalls → the restart fell back to a stale POSCAR. Energy "progress" across rounds within noise = magnetic-state drift (no MAGMOM), not structure advance.
- Check POSCAR vs CONTCAR mtime/md5: after in-place fetch they are identical (normal, NOT evidence of a restore).

## crisp POSCAR.bak semantics (verified 2026-08-14)

- Written ONCE at submit time by `_preserve_original_poscar` (crisp/cli/commands/jobs.py ~338) — a cache-prefill identity snapshot ("stable relaxation-chain input").
- fetch/daemon NEVER write POSCAR. There is NO "fetch restores POSCAR.bak" behavior — do not claim it.
- vasp-cache prefill (`_prefill_converged_structure`) mirrors cached CONTCAR to POSCAR at submit, preserving POSCAR.bak.

## vasp-sop restart machinery

- cpd ionic restarts: `wave2_submit` calls `restart_from_contcar` (CONTCAR→POSCAR + ISTART=1) when verdict reason ∈ `_IONIC_RETRY_REASONS` {force_gate_fail, nsw_exhausted, nsw_early_exit, missing_forces, truncated}; capped at `_CPD_MAX_IONIC_RESTARTS` (truncated exempt).
- Since commit 87663ca: restart failure logs warning (not silent) + warns "POSCAR older than CONTCAR after restart — continuation not in effect" when the copy didn't take.
- stage2 first round: `_submit_stage2_soc` patches LSORBIT + CONTCAR→POSCAR; later rounds go through the same ionic-restart path.

## Discipline (ADR 0014/0022)

- Every stage2 retry MUST continue from latest CONTCAR — a retry on stale POSCAR spins from the same old geometry forever.
- SOC systems: set explicit MAGMOM, else magnetic initial state drifts every round and masks continuation.
- Avoid 20-min-limit partitions (duguex_113 test) for SOC relaxations — truncation every ~12 steps.

## Case: Ti8Bi9_mp-640045 (2026-08-14)

5 stage2 rounds: first F −174.781/−174.841/−174.948/−174.785/−174.976, last F −175.739/−174.958/−175.845/−174.986/−175.858 — never converged; NSW rounds fell back to stage1 CONTCAR baseline. Excluded from cpd (moved to cpd_excluded/).
