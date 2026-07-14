# Post-process writes summary with many missing corrections; pe can crash; COMPLETE blocked

**Date:** 2026-07-14
**Severity:** High — data integrity + false “pipeline complete”
**Systems:** BaO2 (hard fail), BaS / BaS3 / BaGe2S5 (partial success), likely others

## Symptoms

1. `analyze()` logs:
   ```
   WARNING Skipping N defect(s) missing correction.json: ...
   ✓ System pipeline complete
   ```
2. Still produces `defect_energy_summary.json` from the subset that has `correction.json`.
3. For BaO2, `pydefect pe` then crashes:
   ```
   ValueError: min() arg is an empty sequence
   ```
   (empty charge-energy set after allow_shallow / no usable defects).
4. `_phase()` stays `UNITCELL_DEFECT` because COMPLETE requires **every** non-failed defect dir to have `calc_results.json` + `correction.json` + `defect_structure_info.json` — so summary exists but phase ≠ COMPLETE.

## Root cause (pipeline)

- `pydefect_vasp cr` / `efnv` do not guarantee per-dir success; some dirs keep OUTCAR but never get `calc_results.json` / `correction.json`.
- `analyze()` continues with `corrected` subset only for `dei`, then runs `des`/`pe` on global globs.
- “pipeline complete” is printed after `analyze()` returns without exception — even when most defects were skipped.
- COMPLETE gate is stricter than analyze success criteria → phase/summary inconsistency.

Example **BaS** after batch:

| defect | OUTCAR | calc_results | correction |
|--------|--------|--------------|------------|
| Ba_S1_0 | ✓ | ✗ | ✗ |
| Ba_S1_2 | ✓ | ✓ | ✗ |
| S_Ba1_0 | ✓ | ✓ | ✓ |
| … | | | 12 missing intermediates |

## Expected

1. Fail (or soft-fail with clear non-complete status) if fraction of corrected defects below threshold.
2. Do not run `pydefect pe` when energy list is empty.
3. Either mark skipped defects `failed` in JobStore so COMPLETE can advance with known gaps, **or** re-run cr/efnv until all OUTCAR dirs have corrections.
4. Align “pipeline complete” print with COMPLETE phase criteria.

## Workaround

- Inspect missing `correction.json` dirs; re-run `pydefect_vasp cr` then `pydefect efnv` in `defect/`.
- Delete incomplete `defect_energy_summary.json` before re-analyze.
- Optionally JobStore `failed` for unrecoverable charge states.

## Related

- `vasp_sop/defect/analysis.py` (skip + pe loop)
- `vasp_sop/cli/main.py` `_phase()` COMPLETE intermediates loop
- issues/0005 (failed defects not blocking — only helps if status is `failed`)

## Status update (2026-07-14, later)

Implemented in `vasp_sop/defect/analysis.py`:
- `analyze()` → `"full" | "partial" | "failed"`
- incomplete final summary demoted to `defect_energy_summary.partial.json`
- writes `defect/analyze_status.json` (`n_eligible`, `n_corrected`, missing list)
- `batch` only prints `✓ pipeline complete` on **full**; partial/failed get `~` / `✗`
