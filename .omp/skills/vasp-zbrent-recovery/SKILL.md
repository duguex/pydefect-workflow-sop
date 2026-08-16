---
name: vasp-zbrent-recovery
description: "Recover VASP ISIF=3 relaxations that crashed with ZBRENT: fatal error in bracketing — patch EDIFF to 1E-06 AND copy CONTCAR to POSCAR, then resubmit (both actions, not just CONTCAR). Use when OUTCAR shows the ZBRENT error, jobs fail repeatedly on cpd/defect phases, or crisp shows CRISP_FAILED EXIT_CODE:1 with ZBRENT text."
---

# VASP ZBRENT Recovery

## Symptom
OUTCAR tail:
```
|     ZBRENT: fatal error in bracketing                                       |
|      please rerun with smaller EDIFF, or copy CONTCAR                       |
|      to POSCAR and continue                                                 |
|       ---->  I REFUSE TO CONTINUE WITH THIS SICK JOB ... BYE!!! <----       |
```
crisp job shows `CRISP_FAILED EXIT_CODE: 1`.

## Root cause
VASP ISIF=3 ionic relaxation fails to bracket EDIFFG force root — typically when EDIFF (electronic) is too loose (1e-4) for the force criterion. Not a crisp bug; crisp has NO ZBRENT auto-recovery (grep shared/calculators/vasp.py: no handling).

## Fix (BOTH actions, in order)
1. **EDIFF -> 1E-06** in INCAR (keep everything else protocol-identical):
   `sed -i "s/^EDIFF = .*/EDIFF = 1E-06/" INCAR`
2. **CONTCAR -> POSCAR** (continue from the last ionic step):
   `cp CONTCAR POSCAR`
3. Resubmit via crisp (`crisp submit --dir <dir> --tag long`).

Doing ONLY the CONTCAR->POSCAR half is NOT enough — ~half of jobs re-crash on the next bracket (observed 10/22 re-crash on Li2ZnGe3O8 cpd phases).

## Notes
- Phases that already converged keep their original EDIFF — only patch the crashing dirs.
- Convergence verdict: OUTCAR contains `reached required accuracy`.
- Scan a batch for ZBRENT dirs: `for d in */; do grep -q "ZBRENT: fatal error" $d/OUTCAR && echo $d; done`
- Always back up POSCAR before overwrite: `cp POSCAR POSCAR.orig` (geometric provenance matters).
