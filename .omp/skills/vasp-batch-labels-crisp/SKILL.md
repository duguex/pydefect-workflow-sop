---
name: vasp-batch-labels-crisp
description: "Batch-run VASP LDMATRIX/CHGCAR jobs on the crisp clusters (duguex_113 test partition): build job dirs, upload, QOS-capped retry-submit loop, parse OUTCAR ZFS tables, and CHGCAR density analysis with unit/POSCAR traps. Use when generating ZFS labels, reproducibility measurements, or spin-density runs for the spin-model pipeline."
---

# VASP batch label runs via crisp (LDMATRIX / CHGCAR)

Verified 2026-08-10 on duguex_113 (test partition, qos_test, vasp 6.5.1 AVX2 sif). Used for ZFS label reproducibility tests and CHGCAR production for the spin-model pipeline.

## Job dir layout (per job)
- `POSCAR` — VASP format, lattice as **3 rows × 3 floats per row** (VASP tolerates one-float-per-line but pymatgen `Structure.from_file` does NOT — row-per-line or pymatgen rejects it)
- `INCAR` — LDMATRIX protocol: `ISMEAR=0 SIGMA=0.05 ALGO=N ADDGRID=T NCORE=4 NSIM=16 ISPIN=2 NUPDOWN=2 NELECT=862 ISYM=3 LDMATRIX=T LWAVE=F NSW=0 IBRION=-1` + `EDIFF=1E-6 ENCUT=400 LREAL=Auto`. **ISYM must be 3** (LDMATRIX requirement). Add `LCHARG=.TRUE.` when CHGCAR is needed (~46 MB/structure, physics unchanged — verified bit-identical ZFS tables).
- `KPOINTS` — Gamma-only: `Automatic\n0\nGamma\n1 1 1\n0 0 0`
- `POTCAR` — C+N PAW at `experimental/step0_results/scf/POTCAR` (verify with `grep TITEL`)
- `submit.slurm` — copy from repo root (test partition, 40 cores, `.completed`/`.failed` markers)

## Submit with QOS cap workaround
`crisp submit` queues in the daemon and can stall; direct sbatch via crisp exec is faster:
1. `tar czf jobs.tar.gz jobdirs...`, `crisp put duguex_113 jobs.tar.gz /home/phys/duguex/work/`
2. `crisp exec duguex_113 "cd ... && tar xzf ... && for d in dirs; do (cd \$d && sbatch submit.slurm); done"`
3. **QOSMaxSubmitJobPerUserLimit** (~4-6, shared with other users' jobs) rejects submissions when full. Run a remote retry loop: `nohup bash retry_submit.sh &` where the script loops dirs, tries `sbatch`, sleeps 300 on QOS errors, skips dirs with `.completed`/`.failed`.

## Parse ZFS tables (cluster, stdlib only)
Regex the OUTCAR for `Spin-spin contribution to zero-field splitting tensor (MHz)` + the 6-float row (leading whitespace; use line-based search, not anchored multiline regex), then the `after diagonalization` block (rows: `D_diag x y z`, skip separator lines). D = λ2 − 0.5(λ0+λ1), E = 0.5(λ1−λ0) from sorted eigenvalues. Parser: worktree `analysis/experiments/spin_multitask/label_noise_floor/parse_zfs_tables.py`-style.

## CHGCAR analysis traps
- **Units**: VASP CHGCAR grid values are in e/cell, NOT e/Å³ — divide by the cell volume before any integral (integrated spin should come out 2.0 for NUPDOWN=2).
- FFT dipolar integral over the CHGCAR diff density does NOT reproduce LDMATRIX (PAW augmentation gap 28–74 MHz, structure-dependent) — analytic density→D is dead for precision; use it only for sanity.
- `spin_model/spin_density.py` soft-assignment projection SMEARS defect localization (7% vs 61% hard-sphere truth) — do not trust its moments as physical features until fixed.

## Monitoring
- `crisp exec duguex_113 "squeue -u duguex -o '%.10i %.8T %.10M'"` for queue
- `sacct -j <id> --format=WorkDir,State -P` to map jobs to dirs
- `.completed`/`.failed` markers in each job dir; ZFS table appears at SCF convergence
- Runtime: 215-atom LDMATRIX single point ≈ 25–40 min CPU (test partition, 40 cores)
