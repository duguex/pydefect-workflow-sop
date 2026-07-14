# ZnO / CaMg2(SO4)3 stuck at STRUCTURE_OPT

**Date:** 2026-07-14
**Severity:** Medium — blocks CPD for two systems
**Systems:** ZnO, CaMg2(SO4)3

## ZnO

- Phase: `STRUCTURE_OPT`
- Target: `cpd/ZnO_mp-2133`
- OUTCAR exists (~0.9 MB) but **not** `check_converged` — VASP aborted:

```
please rerun with smaller EDIFF, or copy CONTCAR to POSCAR and continue
---->  I REFUSE TO CONTINUE WITH THIS SICK JOB ... BYE!!! <----
```

- NSW=50, IBRION=2, ~20 ionic steps, no “General timing” footer
- JobStore target: `None`
- Extra CPD dirs without OUTCAR: BN_mp-*, Zn3N2_mp-9460 (likely wrong chemistry leftovers / incomplete competing set)

### Fix

1. CONTCAR → POSCAR restart for `ZnO_mp-2133` (or soft EDIFF / POTIM tweak)
2. Record JobStore after successful re-run
3. Clean or re-fetch competing phases without OUTCAR before CHEM_POT_DIAGRAM

## CaMg2(SO4)3

- Phase: `STRUCTURE_OPT`
- Target: `cpd/CaMg2[SO4]3_mp-1229186` — **no OUTCAR**
- Many competing phases still missing OUTCAR (CaS3O10, CaSO4, MgSO4, SO2, SO3, S, mol_O2, …)
- Needs full target + competing VASP submission wave

## Related

- CONTCAR restart path in `_handle_unconverged_poll` (only tracked jobs)
- Target never entered JobStore `tracked` → restart poll never fires for ZnO
