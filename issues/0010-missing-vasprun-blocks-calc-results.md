# Missing `vasprun.xml` blocks `pydefect_vasp cr` → no eFNV

**Date:** 2026-07-14  
**Severity:** P0 — publishable formation energies  
**Systems (examples):** MgS (15/22 conv without corr), SrS (18/18), many bulk defects

## Symptoms

- Defect dir has **OUTCAR** (ionically converged) but **no** `correction.json`
- `pydefect_vasp cr -d <dir> --verbose` fails:

```text
FileNotFoundError: ... '<dir>/vasprun.xml'
WARNING: Failed directories are: <dir>
```

- No `calc_results.json` → efnv/dei cannot run for that defect
- Production: hundreds of OUTCAR-only defect dirs; analyze stays **partial** even when forces are fine

## Root cause

`pydefect_vasp cr` **requires** `vasprun.xml`. Crisp fetch / `move_crisp_outputs` often leaves only OUTCAR/CONTCAR. Pipeline treated OUTCAR-only as post-processable.

## Acceptance criteria

1. `analyze_status.json` reports `missing_vasprun` (list + count) for eligible dirs
2. Defect post-process readiness treats **vasprun.xml** as required for cr/efnv path (alongside ionic convergence)
3. Docs state: OUTCAR-only is **not** formation-energy ready
4. Optional: batch poll / JobStore `record_if_done` for defect dirs does not mark fully “analysis-ready” without vasprun (or separate flag)
5. Test: synthetic defect with OUTCAR but no vasprun appears in `missing_vasprun` and does not count as corrected

## Related

- #0007 partial correction / false complete  
- #0009 post-process failures  
- #0004 hBN missing unitcell (vasprun theme for UC)
