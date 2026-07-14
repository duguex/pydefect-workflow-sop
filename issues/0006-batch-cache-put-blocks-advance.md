# batch run: orphan/cache TaskDoc put blocks system advance for 10+ minutes

**Date:** 2026-07-14
**Severity:** High — operational throughput
**System:** production `2025_undergo_spin_defect`

## Symptoms

`vasp-sop batch run` spends most of wall time in:

```
INFO Getting task doc in: .../defect/...
INFO Processed N orphaned crisp outputs.
INFO Cached N completed calculation(s).
```

A single cycle timed out at **15 minutes** after only advancing systems ~2–11/40. Orphan sweep + poll-path `_cache_phase_results` call `vasp_results_put` → `TaskDoc.from_directory` / full `Vasprun` parse per directory.

## Impact

- System `_advance_one_system` (post-process / submit) is delayed until cache I/O finishes
- Interactive ops and multi-cycle progress become impractical
- Unconverged vasprun.xml triggers heavy warnings but still parse cost

## Expected

- Cache put should be best-effort background or skip when meta already present
- Orphan sweep should not re-parse every unconverged OUTCAR on every batch cycle
- Prefer: record JobStore + `move_crisp_outputs` first; cache put lazy / sampled

## Evidence

`/tmp/batch_run_2.log` (2026-07-14): ~7 minutes of continuous `Getting task doc` before first `[2/40]` advance line; 129 calcs cached in one poll.

## Related

- `vasp_sop/cli/main.py` orphan sweep + poll `_cache_phase_results`
- `vasp_sop/core/cache.py` `vasp_results_put` / TaskDoc path
