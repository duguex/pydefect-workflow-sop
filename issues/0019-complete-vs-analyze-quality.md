# Phase COMPLETE can disagree with analyze quality

**Date:** 2026-07-14  
**Severity:** P1  
**Example:** GeSe2 — `_phase` COMPLETE-like path while `analyze_status` is failed / defects unusable  
**Related:** #0007, #0003

## Problem

System-level phase (`COMPLETE` / `UNITCELL_DEFECT`) is driven by JobStore + marker files + gates that treat `failed`/`unconverged` defects as non-blocking. Formation-energy **quality** is `analyze_status` (`full|partial|failed`).

Operators can see “pipeline complete” while there is **no trustworthy** `defect_energy_summary.json`.

## Desired behavior

1. `batch status` (or phase table) shows **both**:
   - pipeline phase  
   - analyze status (`full|partial|failed|none`)  
2. Optional: COMPLETE requires `analyze=full` **or** explicit `plan.yaml` flag `allow_complete_without_full_analyze: true`  
3. Demote messaging: never print success that looks like scientific complete when analyze is failed  

## Acceptance

- [ ] CLI status columns or JSON include analyze status per system  
- [ ] Document when COMPLETE ≠ publishable  
- [ ] Regression test: failed analyze does not look like full success in status output  

## Related production note

GeSe2: defects force-fail / no usable corrections; do not treat as publishable host.
