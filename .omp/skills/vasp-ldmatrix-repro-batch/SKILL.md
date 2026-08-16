---
name: vasp-ldmatrix-repro-batch
description: "Batch VASP LDMATRIX ZFS label/reproducibility runs via crisp on the test partition: build variant job dirs (INCAR variants, POSCAR, KPOINTS, POTCAR), submit with the QOS retry loop, parse OUTCAR ZFS tables, compute label-noise spreads. Use when generating ZFS labels or measuring protocol reproducibility on duguex_113."
---

# VASP LDMATRIX ZFS Batch (crisp / test partition)

Generate or measure ZFS labels for the NV 215-atom supercell on cluster `duguex_113` (CPU test partition, free).

## Protocol (label-convention INCAR)

```
ISMEAR=0 SIGMA=0.05 ALGO=N ADDGRID=T NCORE=4 NSIM=16 ISPIN=2 NUPDOWN=2
NELECT=862 ISYM=3 LDMATRIX=T LWAVE=F NSW=0 IBRION=-1
```
- LDMATRIX requires **collinear** ISPIN=2 and **ISYM=3** (not 1/2); prints the ZFS table in OUTCAR.
- For density supervision add `LCHARG=.TRUE.` (CHGCAR ~46 MB @108³, does not change the ZFS table).
- Keep ENCUT=400 for cross-dataset comparability (absolute D not converged, but protocol biases cancel in differences to ~0.05 MHz — measured).
- KPOINTS: Gamma 1x1x1. POTCAR: C+N PAW_PBE (local copy: `experimental/step0_results/scf/POTCAR`).
- POSCAR: lattice rows must be ONE line per row (pymatgen rejects one-float-per-line; VASP tolerates it).

## Job dir layout & submission

Each job dir: `POSCAR INCAR KPOINTS POTCAR submit.slurm` (submit.slurm from repo root: `-p test -q qos_test -n 40`, writes `.completed`/`.failed` markers, `%j.log`).

- Upload: `tar czf jobs.tgz dirs...` → `python -m cli.cli put duguex_113 jobs.tgz /home/phys/duguex/work/` → extract via `crisp exec`.
- **QOSMaxSubmitJobPerUserLimit caps total submitted jobs** (user's other jobs count). Submit in waves; on `QOSMaxSubmitJobPerUserLimit` rejection, retry every 300 s. Proven pattern: a nohup retry loop on the login node:

```bash
for d in dirs; do
  while true; do
    out=$(cd "$d" && sbatch submit.slurm 2>&1)
    echo "$out" | grep -q Submitted && break || sleep 300
  done
done
```
Run with `nohup bash retry.sh > retry.log 2>&1 &` via `crisp exec`. ~25-40 min per single point, 4-6 concurrent.

## Parse & analyze

- Parse the ZFS table (robust to leading whitespace — locate the marker line, then regex the numeric row; the diagonalization block's separator line must be SKIPPED not break):
  - tensor row: 6 floats after "Spin-spin contribution to zero-field splitting tensor (MHz)"
  - D_diag rows: 4 floats per line after "after diagonalization"
- D = eig2 − 0.5(eig0+eig1), E = 0.5(eig1−eig0) (ascending eigenvalues).
- Reproducibility spread: per-structure D std over variants {ref, EDIFF 1e-7, LREAL=False, ENCUT 500} — pooled ~0.48 MHz absolute, but pairwise ΔD errors ≤0.06 MHz within the low/mid strain regime (bias cancellation); ENCUT bias flips sign at the highest-distortion structures (~1.3 MHz error crossing regimes).
- The working parse/analyze scripts live in `analysis/experiments/spin_multitask/label_noise_floor/` (`parse_zfs_tables.py` runs on the cluster login node, stdlib only; `analyze_label_noise.py` locally).

## Pitfalls

- crisp `submit` queues behind the daemon backlog — for immediate control use put + exec + sbatch (still through the crisp daemon).
- Parser regexes break on uppercase keys (`mae_D_mhz`) and leading whitespace; use line-based matching.
- CHGCAR grid values are e/cell — divide by the cell volume for e/Å³ before any density integral.
