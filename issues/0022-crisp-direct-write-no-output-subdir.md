# crisp layout change: direct write + `jobid.log`

**Date:** 2026-07-15  
**Severity:** P2 (compat / docs)  
**Source:** crisp update (operator report)

## Changes in crisp

1. **Slurm log name:** `{slurm_job_id}.log` (e.g. `205890.log`), not a fixed `vasp_stdout.log`-only layout.  
2. **No `output/` staging:** VASP results are written **directly** into the calculation work directory. The previous `work_dir/output/` pull path is **abolished** for new jobs.

## Impact on vasp-sop

| Area | Action |
|------|--------|
| `move_crisp_outputs` | Keep as **legacy no-op** when `output/` absent; still promote old trees with mtime prefer |
| `check_converged` / `has_vasprun` | Keep root **and** `output/` lookups for mixed old/new trees |
| Orphan scan of `*/output/OUTCAR` | Harmless; finds only legacy dirs |
| Docs | Document direct-write as current; `output/` as legacy |

## Acceptance

- [x] Code tolerates missing `output/` (always did for promote)  
- [x] Docs state current crisp = direct write + `jobid.log`  
- [ ] Optional: stop inventing `vasp_stdout.log` assumptions in tooling (none required in core today)

## Non-goals

- Deleting historical `output/` trees in production  
