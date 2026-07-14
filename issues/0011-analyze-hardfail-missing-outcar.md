# `analyze()` hard-fails if any defect dir lacks OUTCAR

**Date:** 2026-07-14  
**Severity:** P1  
**File:** `vasp_sop/defect/analysis.py::analyze`

## Problem

If **any** subdirectory under `defect/` (or perfect) is missing OUTCAR and cache restore fails, analyze returns **failed** for the whole system — even when dozens of other defects are ready for efnv/des.

## Acceptance

1. Missing-OUTCAR dirs are skipped and listed in `analyze_status.json` (`missing_outcar`)
2. Remaining converged+ready dirs still run cr/efnv/dei/des → at least **partial** when possible
3. **failed** only when zero dirs are usable for energetics (or perfect unusable)
4. Unit test covers mixed ready/missing OUTCAR → partial not failed

## Related

- #0007, #0010
