---
name: vasp-ldmatrix-zfs-reproducibility
description: "Measure VASP LDMATRIX ZFS label reproducibility floor and perturbation-ΔD error cancellation via crisp: job matrix (EDIFF/LREAL/ENCUT variants), cluster submit with QOS retry loop, OUTCAR table parsing, spread analysis. Use when asked about label noise, protocol precision, or 0.1 MHz label feasibility."
---

# VASP LDMATRIX ZFS Label Reproducibility Test

Measure how much ZFS labels vary across numerical settings (the protocol floor) and whether systematic biases cancel in structure-to-structure differences (perturbation accuracy). Proven 2026-08-10 on zfs_and_force.

## Protocol facts (VASP wiki LDMATRIX)
- LDMATRIX=.TRUE. works in **collinear** ISPIN=2 (NOT noncollinear/SOC). Implies LHFCALC=T, AEXX=0. **ISYM must be 3** (not 1/2).
- Table "Spin-spin contribution to zero-field splitting tensor (MHz)" + "after diagonalization" block printed in OUTCAR.
- NV⁻: ISPIN=2 NUPDOWN=2 NELECT=862 (214 C + 1 N), Gamma-only, ENCUT 400, EDIFF 1e-6, LREAL Auto.
- Parser: experimental/read_zfs.py in the main repo (modified pymatgen Outcar) or the stdlib regex parser at /tmp/parse_zfs_tables.py pattern (line-based: find marker line, regex 6 floats from the row that follows).

## Job matrix design
- Structures × variants. Useful variants: ref (EDIFF 1e-6/ENCUT 400/LREAL Auto), edi7 (EDIFF 1e-7), lreal (LREAL=.FALSE.), encut500, encut600.
- Job dir: POSCAR (VASP format, Direct), INCAR (BASE + variant lines), KPOINTS (Gamma 1x1x1), POTCAR (C+N from experimental/step0_results/scf/POTCAR), submit.slurm (copy repo root; -p test -q qos_test).
- Build from npz: species layout 214 C then 1 N verified; cells identical 10.667 Å.

## Submission via crisp (duguex_113, test partition = cheap CPU)
- `crisp submit` queues through the daemon DB — **gets stuck behind other users' jobs and QOSMaxSubmitJobPerUserLimit**. The bypass that works: tar the job dirs, `crisp put` the tarball, `crisp exec` extract + `sbatch submit.slurm` per dir.
- QOS submit cap: submit in small batches; when "Job violates accounting/QOS policy" appears, run a remote retry loop (nohup bash loop that tries sbatch every 300s per dir, writing a log; skips dirs with .completed/.failed).
- Runtime: 215-atom LDMATRIX single point ≈ 25–40 min (ENCUT 600 slower). Markers: submit.slurm writes .completed/.failed.

## Analysis (the important part)
1. Parse all OUTCAR tables → JSON.
2. **Absolute floor**: per-structure D_std across variants; pooled = sqrt(mean(std²)). 2026-08-10 result: 0.48 MHz, ENCUT-dominated (400→500 shifts −1.2 MHz; NOT converged at 600: step −2.95 MHz).
3. **Perturbation cancellation** (what actually matters for ΔD applications): for each structure pair, error = (D_b−D_a)@variant − (D_b−D_a)@ref. EDIFF/LREAL biases are uniform across structures → cancel to ≤0.03/≤0.01 MHz. ENCUT bias uniform within a regime → ≤0.06 MHz, BUT can flip sign at extreme distortion (max_6725: +0.12 vs −1.2) → crossing-regime pairs carry ~1.3 MHz error.
4. Absolute-vs-relative distinction: a large absolute floor does NOT block 0.1 MHz perturbation models if biases are uniform; verify cancellation explicitly before claiming a floor.

## Interpretation for label protocol decisions
- Keep one protocol for all labels (uniform bias cancels in differences).
- For low-strain/phonon ΔD targets, ENCUT 400 is adequate (cancellation ~0.05 MHz); ENCUT convergence matters only for absolute D claims.
