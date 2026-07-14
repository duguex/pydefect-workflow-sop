# `analyze_status.json` lacks actionable counters for QA

**Date:** 2026-07-14  
**Severity:** P1  
**File:** `vasp_sop/defect/analysis.py::_write_status`

## Problem

Status currently has coarse fields (`status`, eligible/corrected counts, unconverged list). Operators cannot see **why** partial: missing vasprun, missing calc_results, missing dei, efnv skipped, etc.

## Acceptance

`analyze_status.json` includes at least:

- `n_eligible`, `n_converged`, `n_corrected`, `n_dei`, `n_unconverged`
- `missing_vasprun`, `missing_calc_results`, `missing_correction` (lists or counts+sample)
- `n_missing_outcar` / list when applicable
- Schema stable enough for production scans

Test asserts keys present after `analyze()` / classify path.

## Related

- Publishable DoD, #0010, #0011
