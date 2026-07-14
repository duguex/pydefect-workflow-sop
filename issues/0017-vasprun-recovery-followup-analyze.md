# After vasprun_recovery jobs finish: re-analyze and track correction coverage

**Date:** 2026-07-14  
**Severity:** P0 ops  
**Related:** #0010, #0016

## Context

Production submitted ~254 single-point VASP jobs (`reason=vasprun_recovery`, NSW=0 / IBRION=-1 from CONTCAR) for ion-converged defects missing `vasprun.xml`.

## Required follow-up (do not forget)

1. Wait for crisp jobs to complete; `move_crisp_outputs` / poll so `vasprun.xml` lands on disk  
2. Re-run:
   ```bash
   vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
   # or per system:
   vasp-sop defect analyze <system_dir>
   ```
3. Scan `analyze_status.json`:
   - `n_missing_vasprun` should drop  
   - `n_corrected` / `n_converged` should rise toward full  
4. Spot-check MgS, SrS, Ba2TeO, CaSe, SiC (heavy missing-vasprun before recovery)

## Acceptance

- [ ] Recovery cohort mostly has `vasprun.xml` or `calc_results.json`  
- [ ] At least the small-unc systems (e.g. BaO2, BaTe, MgS once vr present) reach `analyze=full` or clearly documented blockers  
- [ ] No permanent `submitted` stuck without OUTCAR/timing for recovery jobs  

## Non-goals

- Does not re-open #0016 code path; this is **ops execution + verification**.
