---
name: vasp-ldmatrix-repro-measurement
description: "Run VASP LDMATRIX ZFS reproducibility/label-noise measurements via crisp on the duguex_113 test partition: build job dirs (INCAR variants, POSCAR from npz/CONTCAR, C+N POTCAR), upload, submit via submit.slurm with a QOS-retry loop, parse OUTCAR ZFS tables, analyze per-structure spreads and ENCUT convergence. Use when measuring ZFS label precision, generating new LDMATRIX labels (e.g. MD-relabel phase), or auditing protocol sensitivity."
---

# VASP LDMATRIX Reproducibility / Label Measurement (crisp + duguex_113)

Measured workflow for ZFS label precision studies (proven 2026-08-10, 20+ jobs).

## When to use
- Measuring the ZFS label noise floor / protocol sensitivity (EDIFF/LREAL/ENCUT axes)
- Generating new LDMATRIX labels for the MD-relabel dataset phase
- Auditing CHGCAR-based pipelines (LCHARG=.TRUE. runs)

## Protocol facts
- Label source: VASP OUTCAR table "Spin-spin contribution to zero-field splitting tensor (MHz)" — printed when `LDMATRIX=.TRUE.` in a COLLINEAR ISPIN=2 calc (NOT noncollinear/SOC). Requires `ISYM=3`. vasp 6.5.1 (sif: `~/vasp651_avx2.sif`, cluster: duguex_113).
- Reference INCAR base: `ISMEAR=0 SIGMA=0.05 ALGO=N ADDGRID=T NCORE=4 NSIM=16 ISPIN=2 NUPDOWN=2 NELECT=862 ISYM=3 LDMATRIX=T LWAVE=F LCHARG=F NSW=0 IBRION=-1` + `EDIFF=1E-6 ENCUT=400 LREAL=Auto`.
- POTCAR: C+N at `/home/duguex/zfs_and_force/experimental/step0_results/scf/POTCAR`.
- POSCAR: write Direct coords with ONE lattice row per line (pymatgen Poscar rejects row-per-float files even though VASP tolerates them).
- KPOINTS (Gamma): `Automatic\n0\nGamma\n1 1 1\n0 0 0`.

## Workflow
1. Build job dirs locally (one dir per structure__variant; INCAR = base + variant block).
2. Copy repo `submit.slurm` (test partition, qos_test, 40 cores, .completed/.failed markers) into each dir.
3. Tar + `crisp put duguex_113 <tar> /home/phys/duguex/work/`, extract via `crisp exec`.
4. **QOS trap**: `QOSMaxSubmitJobPerUserLimit` caps total submitted jobs (shared with the user's other VASP work). Submit via a remote nohup retry loop (`sbatch` every 300 s until "Submitted") — see retry_submit.sh pattern.
5. Parse: `parse_zfs_tables.py` (line-based regex, robust to leading whitespace; locate marker line then match the 6-float row; diagonalization block = 3 rows of `D vec3` after "after diagonalization"). Runs with cluster `python3` (stdlib only — no numpy on login node).
6. Analysis: per-structure D_std across variants; D = λ2 − 0.5(λ0+λ1), E = 0.5(λ1−λ0) (ascending eigenvalues).

## Known results (2026-08-10, 4 structures × {ref, edi7, lreal, encut500} + encut600)
- Absolute D sensitivity: pooled D_std ≈ 0.48 MHz; ENCUT 400→500 shifts D −1.2 MHz, 500→600 −2.95 MHz (NOT converged at 600).
- Perturbation accuracy: with a FIXED protocol, ΔD between structures cancels to ~0.05 MHz in the low/mid-strain regime; ENCUT bias flips sign at the max-distortion structure (0.080 Å) → cross-regime ΔD error ~1.3 MHz.
- EDIFF 1e-6→1e-7: +0.04 MHz uniform; LREAL Auto→False: +0.12 MHz uniform (both cancel in differences to ≤0.03/0.01 MHz).
- pristine NV: D = 3022.44 MHz, [111] axis, E ≈ 0.3 MHz.
- CHGCAR (LCHARG=.TRUE.) is 46 MB @ 108³; ZFS table bit-identical with LCHARG=.FALSE.

## Pitfalls
- VASP CHGCAR values are in e/cell, NOT e/Å³ — divide by cell volume before integrating (integrated spin must be 2.0 for NUPDOWN=2).
- `crisp exec` with sleep inside can time out the socket; use nohup for long remote jobs and poll.
- Grid-density dipolar integral (FFT, sampled kernel) cannot reproduce LDMATRIX ZFS: PAW augmentation gap is structure-dependent (28–74 MHz residuals after calibration). Do NOT use grid-density integrals as a precision path — only as sanity probes.
