# Next directions

## 0. Ops status (2026-07-13)

Code fixes landed this session:
- `_phase()`: JobStore `failed` defects no longer block COMPLETE; non-calc junk dirs under `defect/` ignored
- UC false-`converged` (missing `vasprun.xml`) resubmits via crisp
- Post-process gates treat `failed` defects as finished
- `--dry-run` no longer runs backfill / orphan / CONTCAR-restart (was leaking real jobs)

Production (`2025_undergo_spin_defect`):
- COMPLETE ≈ 8 (AlN, BaO, CaO, CeO2, GaN, MgO, MoS2, SrO)
- STRUCTURE_OPT: ZnO, CaMg2(SO4)3 (JobStore target not yet backfilled / incomplete target)
- UNITCELL_DEFECT: majority — batch cycle submitted many CONTCAR restarts + band/dos re-runs
- Post-process still fails when `defect/perfect/vasprun.xml` is missing (pydefect `pbes`)

## 1. Continue production batch

```bash
vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
# after VASP finishes (esp. band/dos/perfect vasprun recovery):
vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch status /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

Priorities:
1. Wait for UC band/dos resubmits → rebuild `unitcell.yaml` (hBN, SiC, BaS, …)
2. Ensure defect `perfect/` has `vasprun.xml` before analysis
3. ZnO: backfill target OUTCAR → COMPETING remaining phases → CHEM_POT_DIAGRAM

## 2. Phase B — cache as post-process data source

Goal: analysis can run from JSONStore blobs when disk OUTCAR/vasprun are incomplete.

- Restore `vasprun.xml` / OUTCAR from cache blobs into work dirs before pydefect
- Or teach analysis to read TaskDoc/cache when files missing
- Keep Phase A behavior (disk-first) as default until restore is verified on GaN

## 3. vasp-incar integration

Import INCAR tag knowledge from `/home/duguex/vasp_incar` into `_extract_tags` for richer semantic tags.

## 4. Broader SOP (post point-defect MVP)

Not started: phonon, electron-phonon, excited-state, linear-response SOPs; methodology for turning new VASP workflows into pipeline stages.

## 5. Test coverage

- [x] `_extract_tags` Line_mode / band-structure KPOINTS
- [x] `_extract_tags` combined INCAR+KPOINTS+structure
- [x] `_extract_tags` space group from sga
- [x] INCAR tags: SCAN, PBEsol, phonon, dielectric, high/low-encut
- [x] `cache put -r` recursive scanning
- [x] `cache put --formula --task-id` explicit args
- [x] `vasp_results_put` partial auto-detect
- [x] `_phase` failed-defect skip + junk dir ignore
- [x] UC stale JobStore converged resubmit (missing vasprun)
- [ ] `_batch_run --dry-run` never calls `_handle_unconverged_poll` (regression)
- [ ] E2E analysis with partial failed defects

## 6. Feature catalog

`FEATURES.md` — keep phase names (`STRUCTURE_OPT` / `CHEM_POT_DIAGRAM` / `UNITCELL_DEFECT` / `COMPLETE`) and CLI inventory in sync with code.
