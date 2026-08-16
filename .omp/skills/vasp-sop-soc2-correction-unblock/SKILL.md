---
name: vasp-sop-soc2-correction-unblock
description: "Unblock vasp-sop analyze partial caused by missing correction.json in stage2 SOC single-point (NSW=0) defect/cpd dirs: stale calc_results false-blockers, NELM=200 reruns, and the ZBRENT-auto-patch oscillation loop. Use when a system is 100% D/T but analyze stays partial with \"Skipping N converged defect(s) missing correction.json\", or defect relaxations stall at the same max_f repeatedly."
---

# vasp-sop SOC single-point correction unblock

## Symptom
System 100% D/T but `analyze_status.json` stays `partial`, log: `Skipping N converged defect(s) missing correction.json` and/or `efnv: no correction.json for X after run`. Dirs are NSW=0 stage2 SOC single points (or Bi_* SOC continuations).

## Step 1 — classify each missing-correction dir (never trust OUTCAR strings)
- `OUTCAR` lacking "reached required accuracy" is **NOT** proof of non-convergence for NSW=0 runs (2026-08-12: 181 false positives).
- The authoritative check is pydefect's cr: `cd <system>/defect && pydefect_vasp cr -d <dir>` then read `calc_results.json` `electronic_conv`.
- Rule behind it (pymatgen): `converged_electronic = len(final_elec_steps) < parameters["NELM"]` — NELM exhaustion is the failure signal, not the absent string.
- ⚠️ `pydefect_vasp cr -d A -d B -d C` only writes the LAST dir's file — run **one dir per invocation**.

## Step 2 — stale calc_results.json (the common case)
A dir whose NELM=200 rerun already fetched back still shows econv=False because `calc_results.json` predates the fetch. Verify with the real vasprun:
```python
from pymatgen.io.vasp import Vasprun
v = Vasprun('vasprun.xml', parse_potcar_file=False, parse_dos=False)
print(v.parameters.get('NELM'), len(v.ionic_steps[-1]['electronic_steps']), v.converged_electronic)
```
If converged_electronic=True: `rm calc_results.json` + re-run `pydefect_vasp cr -d <dir>` singly → econv=True → next analyze cycle produces correction.json. (2026-08-12: 8 of 8 "blockers" were this.)

## Step 3 — genuine electronic fail: NELM budget
If cr says econv=False and vasprun steps >= NELM: patch `NELM = 200` in INCAR (keep LSORBIT + NSW=0), resubmit (crisp submit or via `_submit_stage2_soc`), wait for fetch, then re-cr. Watch for the stale-cr trap again after fetch.

## Step 4 — oscillation loop from ZBRENT-auto-patch misfire (force-gate dirs)
If a *relaxation* (NSW>0) keeps restarting and `max_f` oscillates, check the loop's auto-patch: `_has_zbrent_failure` reads old slurm logs and applies `EDIFF=1e-6` — on spin-polarized near-metallic defects this makes SCF expensive and noisy (100+ DAV steps), forces jitter, and the relaxation stalls at the same max_f value repeatedly (e.g. 0.147, 0.147, 0.044, 0.044).
- Detection: grep restart/stall lines — same max_f on consecutive stalls = oscillation, not progress.
- Fix: `EDIFF = 1e-4` (protocol default) + `EDIFFG = -0.05` (soft-mode tolerance) + keep NSW=100 → `batch retry`.
- Watch for it after: INCAR regeneration may re-apply the ZBRENT patch next cycle.

## Step 5 — verify
Wait one analyze cycle; correction.json appears; `analyze_status.json` → full; system advances (COMPLETE or next phase). Check the staged hull consistency for SOC systems (all cpd dirs at same level) before accepting formation energies.

## Related
- issue #125 (soc2 truncation family), #131 (ZBRENT log blind spot), #132 (INCAR stripped protocol).
- ADR 0016: electronic gate uses NELM warnings, not the accuracy string alone.
