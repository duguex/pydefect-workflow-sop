# Large pydefect commands timeout / retain too many figures

**Date:** 2026-07-15  
**Severity:** P1  
**Example:** SiC (~250 converged defect-charge dirs)

## Symptoms

- `pydefect efnv`, `dsi`, `beoi` hit the fixed 600 s `run_local` timeout
- `beoi` warns `More than 20 figures have been opened`
- `des` can fail after parsing hundreds of dirs (`atom_io` assertion)
- A single system consumes >30 min and causes outer batch timeout

## Root cause

`analyze()` invokes huge all-dir CLI commands with the generic 600 s timeout. Several steps are per-directory and resumable; batching reduces memory/figure pressure and gives progress checkpoints.

## Acceptance

- [ ] `cr`, `efnv`, `dsi`, `dvf`, `beoi`, `bes`, `dei` process explicit bounded batches
- [ ] Each batch uses a configurable / step-appropriate timeout
- [ ] Existing artifacts are skipped; rerun resumes
- [ ] `des` remains a whole-summary command but only receives ready dirs; timeout raised for large sets
- [ ] Unit tests verify batching excludes unconverged dirs and covers all targets exactly once
