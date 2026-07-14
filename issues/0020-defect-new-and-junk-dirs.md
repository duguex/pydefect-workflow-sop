# `defect_new/`, junk dirs, and non-`*_` folders pollute scans and JobStore

**Date:** 2026-07-14  
**Severity:** P2  
**Examples:** `diamond/defect_new` (~800 JobStore paths); orth-SiC `hh`/`hk`/`kk`/`small` (vasprun recovery submitted junk)  
**Related:** #0016

## Problem

1. **`defect_new/`** (or similar parallel trees) inflate JobStore and “calculation counts” without being the main `defect/` pipeline root.  
2. **Junk / exploratory dirs** under `defect/` without standard `Name_Charge` layout still may have OUTCAR and get:
   - counted in scans  
   - accidentally submitted by recovery scripts if filters are loose  
3. Analyze uses `"_" in name` for defect dirs; ops scripts must match or they over-submit.

## Desired policy

Document and enforce:

| Path | Role |
|------|------|
| `{sys}/defect/` | **Only** main pipeline root for batch/analyze |
| `{sys}/defect_new/` | Ignored by batch unless `plan.yaml` opt-in |
| Non `*_*` dirs under defect/ | Ignore for submit/analyze (or require `defect_entry.json`) |

## Acceptance

- [ ] `batch run` / JobStore reconcile / vasprun recovery **ignore** `defect_new` by default  
- [ ] Recovery/submit only dirs matching defect name pattern **or** having `defect_entry.json`  
- [ ] Optional inventory command lists ignored trees  
- [ ] diamond / orth-SiC cleaned or documented as exception  

## Non-goals

- Deleting production data without operator approval
