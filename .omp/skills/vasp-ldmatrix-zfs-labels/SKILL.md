---
name: vasp-ldmatrix-zfs-labels
description: "Measure VASP ZFS label noise or generate LDMATRIX ZFS labels via crisp (duguex_113 test partition): protocol settings, job layout, QOS retry workaround, OUTCAR parsing, absolute-vs-perturbation interpretation."
---

# VASP LDMATRIX ZFS Label Reproducibility & Generation

Measure the ZFS label noise floor of a VASP protocol, or generate new ZFS labels, on the OMP clusters via crisp. Validated 2026-08-10 on duguex_113 (NV-center dataset, 20 jobs).

## Protocol (VASP 6.4+ LDMATRIX)

- **Collinear** ISPIN=2 (NOT noncollinear/SOC — wiki summary from web search is wrong on this). `LDMATRIX = .TRUE.` implies LHFCALC=True, AEXX=0.0 (pure DFT + reciprocal-space spin-spin integral, Rayson-Briddon 2008).
- **ISYM = 3 required** (LDMATRIX must not be combined with ISYM 1/2 — the repo's data/INCAR uses ISYM=2, so label runs used a different INCAR).
- NUPDOWN for the high-spin state; NELECT fixed charge; Gamma-only k-points fine for a 215-atom cell.
- Output: OUTCAR table "Spin-spin contribution to zero-field splitting tensor (MHz)" + "after diagonalization" block. Parse with the pattern in `zfs/experimental/read_zfs.py` (modified pymatgen Outcar) or the line-based parser at `.worktrees/spin-model-cpu-audit/analysis/experiments/spin_multitask/label_noise_floor/parse_zfs_tables.py`.
- Validation reference: pristine NV⁻ gives D=3022 MHz (exp 2870, +5.3% expected for spin-spin-only PBE), axis [111], E≈0.3.

## Cluster execution (duguex_113, test partition = CPU, cheap)

- VASP: `singularity exec ~/vasp651_avx2.sif mpirun /opt/vasp.6.5.1_ifx_avx2/bin/vasp_std` (also `vasp_latest.sif` present). Inventory in `/home/duguex/crisp/inventory/clusters.json` (never copy credentials).
- Job dir needs POSCAR/INCAR/KPOINTS/POTCAR. POTCAR C+N: `zfs_and_force/experimental/step0_results/scf/POTCAR`. KPOINTS Gamma: `Automatic / 0 / Gamma / 1 1 1 / 0 0 0`.
- Submit via the repo `submit.slurm` (test partition, qos_test, markers `.completed`/`.failed`/`.timeout`).
- **crisp submit queue trap**: the daemon's job queue can sit in `submit` status for a long time (user's other jobs ahead, dispatch stalled). Workaround: `tar czf` the job dir, `crisp put` to `/home/phys/duguex/work/`, then `crisp exec duguex_113 "cd ... && sbatch submit.slurm"`. Cancel any crisp-queued duplicate with `crisp cancel --name <task>`.
- **QOSMaxSubmitJobPerUserLimit ≈ 4-6 total submitted jobs** (shared with all user jobs). Batch submits fail with "Job violates accounting/QOS policy". Run a remote retry loop: `nohup bash retry_loop.sh` that tries `sbatch` every 300s per dir until accepted (script pattern in `/tmp/retry_submit.sh` from the 2026-08-10 session).

## Measurement design (reproducibility test)

- 4 structures (pristine + low/mid/max distortion) × variants: ref / EDIFF 1e-7 / LREAL=.FALSE. / ENCUT 500 (+ ENCUT 600 for convergence check) = ~20 jobs, each ~25-60 min at 40 cores.
- Parse all OUTCARs → zfs_tables.json → analyze (see `label_noise_floor/analyze_label_noise.py`).

## Interpretation — the key distinction

- **Absolute sensitivity**: D_std across settings (pooled ≈ 0.5 MHz; ENCUT non-convergence dominates; 400→500 shifts −1.2 MHz, still not converged at 600).
- **Perturbation accuracy** (what matters for phonon-ΔD modeling): with a FIXED protocol, systematic biases are uniform across structures and cancel in differences — measured ΔD errors ≤ 0.06 MHz (low/mid regime), EDIFF/LREAL axes ≤ 0.03/0.01 MHz. Exception: ENCUT bias flips sign at the most-distorted structure (0.08 Å) → ~1.3 MHz error for pairs crossing that regime boundary. E perturbations are noisier (~0.35 MHz).
- So: labels at fixed protocol support ~0.05-0.1 MHz relative (perturbation) claims in the low-strain regime; absolute claims need an ENCUT convergence study (≥700-800).
