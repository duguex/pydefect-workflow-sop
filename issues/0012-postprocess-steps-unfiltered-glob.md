# Post-process steps still use unfiltered `*_` globs

**Date:** 2026-07-14  
**Severity:** P1  
**File:** `vasp_sop/defect/analysis.py`

## Problem

`efnv` was restricted to ionically converged dirs, but `dsi`, `dvf`, `beoi`, `bes` still run on `*_` (all defects). Unconverged / incomplete dirs add noise, time, and can fail mid-pipeline.

Also `pydefect_vasp cr` uses all-or-nothing skip when every dir already has `calc_results.json`, without re-targeting dirs that still lack it after new converges.

## Acceptance

1. `dsi` / `beoi` / `bes` operate on **converged** (or explicit ready) dir lists with `shlex.quote`
2. `cr` re-runs for dirs missing `calc_results.json` among eligible/converged (not only global skip)
3. Tests assert unconverged names are absent from those command strings

## Related

- #0010 (vasprun readiness), #0009
