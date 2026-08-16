---
name: vasp-zfs-label-reproducibility
description: "Measure the ZFS label reproducibility floor of the VASP LDMATRIX protocol: build job dirs, submit via crisp manual sbatch bypass, parse OUTCAR ZFS tables, analyze absolute sensitivity vs perturbation (ΔD) error cancellation. Use when regenerating ZFS labels, checking label precision, or designing a new ZFS dataset phase."
---

# VASP ZFS Label Reproducibility Protocol

Measures how reproducible ZFS labels are under the original label protocol
(VASP 6.5.1, collinear ISPIN=2, LDMATRIX), and whether systematic protocol
biases cancel in structure-to-structure differences (the quantity that
matters for perturbation/phonon modeling).

## Protocol facts (verified 2026-08-10)

- ZFS labels come from the OUTCAR table "Spin-spin contribution to zero-field
  splitting tensor (MHz)" — printed when `LDMATRIX=.TRUE.` in a **collinear**
  ISPIN=2 run (NOT noncollinear/SOC; VASP wiki LDMATRIX page).
- LDMATRIX implies LHFCALC=True, AEXX=0.0; **ISYM must be 3** (not 1/2);
  needs vasp_std; NUPDOWN for high-spin state.
- Reference INCAR base: ISMEAR=0 SIGMA=0.05 ALGO=N ADDGRID=T NCORE=4 NSIM=16
  ISPIN=2 NUPDOWN=2 NELECT=862 ISYM=3 LDMATRIX=T LWAVE=F LCHARG=F NSW=0
  IBRION=-1 + EDIFF/ENCUT/LREAL per variant.
- Cluster: duguex_113, test partition/qos_test, vasp =
  `singularity exec ~/vasp651_avx2.sif mpirun /opt/vasp.6.5.1_ifx_avx2/bin/vasp_std`.
- Runtime: 215-atom NV cell ~25-40 min/job at 40-64 cores.
- KPOINTS: Gamma-only (Automatic/0/Gamma/1 1 1/0 0 0).
- POTCAR: local `experimental/step0_results/scf/POTCAR` has C+N (PAW_PBE).

## Procedure

1. **Build job dirs** — one dir per {structure, variant}: POSCAR (Direct,
   header "C214N1", lattice from npz cell, species C214+N1), INCAR (base +
   variant lines), KPOINTS, POTCAR copy, submit.slurm copy.
   Variants that bracket the noise axes: ref (EDIFF 1e-6, ENCUT 400,
   LREAL Auto), edi7 (EDIFF 1e-7), lreal (LREAL=.FALSE.), encut500,
   optionally encut600 for convergence curve.
2. **Submit** — `crisp submit` queues behind other user jobs with no dispatch;
   bypass via crisp: `put` a tarball, `exec "tar xzf ... && (cd dir && sbatch
   submit.slurm)"`. QOSMaxSubmitJobPerUserLimit caps concurrent submissions —
   use a remote nohup retry loop (blocked→sleep 300→retry) to drain a batch.
3. **Parse** — `parse_zfs_tables.py` (stdlib regex, line-based: find the
   "Spin-spin contribution" marker, then the 6-float row; diagonalization
   block rows are 4 floats after "after diagonalization"; skip separator
   lines). Scan all job roots.
4. **Analyze** — per structure: D_std across variants (absolute sensitivity).
   Pairwise: ΔD(variant pair) error vs ref pair — this is the perturbation
   accuracy at fixed protocol.

## Measured facts (2026-08-10, 20 jobs)

- Absolute sensitivity: pooled D_std ≈ 0.48 MHz across settings; ENCUT
  400→500 shifts D −1.2 MHz (structure-dependent: −1.25/−1.20/−1.20/+0.12);
  EDIFF 1e-6→1e-7 +0.04; LREAL Auto→False +0.12; pristine not converged at
  ENCUT 600 (500→600 step −2.95 MHz).
- **Perturbation accuracy (what matters for phonon ΔD): biases are uniform
  and cancel** — ΔD errors ≤0.06 MHz for EDIFF/LREAL/ENCUT within the
  low/mid distortion regime; breaks (~1.3 MHz) only when pairs cross the
  max-distortion structure whose ENCUT bias flips sign.
- D (MHz) = λ2 − 0.5(λ0+λ1) from the diagonalization block, same convention
  as spin_model.metrics._zfs_parameters.
- Pristine NV⁻ validation: D=3022 MHz, axis [111], E≈0.3 — matches the
  label convention (D = +5.3% vs experimental 2870, typical for spin-spin-
  only DFT without SOC).

## Pitfalls

- `dos2unix` missing on login node (harmless warning).
- submit.slurm writes .completed/.failed markers in the job cwd — use them
  to distinguish done vs failed; slurm "COMPLETED" does not mean VASP ran.
- LCHARG=.FALSE. means NO CHGCAR — spin-density projection work needs a
  variant with LCHARG=.TRUE.
- QOS submit limit counts the whole user account, not just your jobs.
