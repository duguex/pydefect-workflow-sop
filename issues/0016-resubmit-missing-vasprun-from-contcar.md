# Auto-recover missing `vasprun.xml` (fetch first, else CONTCAR resubmit)

**Date:** 2026-07-14  
**Severity:** P0 ops  
**Related:** #0010

## Problem

Ionically converged defects without `vasprun.xml` never re-enter submit (`check_converged` short-circuits). Pipeline stuck partial; no CONTCAR restart.

## Strategy

1. `move_crisp_outputs` + cache restore  
2. If still no vasprun / calc_results: **`restart_from_contcar`** (CONTCAR→POSCAR, ISTART=1), set **NSW=0 / IBRION=-1** single-point recovery run, `submit_vasp`  
3. JobStore reason=`vasprun_recovery`

## Acceptance

- Converged + missing vasprun no longer permanently skipped  
- Recovery uses CONTCAR when present  
- Unit test for prepare path  
