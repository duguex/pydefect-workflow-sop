---
name: vasp-zfs-label-noise-floor
description: "Measure the ZFS label reproducibility floor of a VASP LDMATRIX protocol: build variant job dirs (EDIFF/LREAL/ENCUT), submit via crisp with QOS retry loop, parse OUTCAR ZFS tables, compute per-structure D spread. Use when asked how precise ZFS labels are, whether 0.1 MHz model accuracy is reachable, or before regenerating ZFS datasets."
---

# VASP ZFS Label Noise Floor Measurement

## When to use
- User asks whether a target model accuracy (e.g. 0.1 MHz ZFS) is reachable with existing labels
- Before regenerating a ZFS dataset with a new protocol
- The answer: model loss can never go below label noise (irreducible/Bayes error), so measure the floor first — it is the cheapest decisive check.

## Protocol facts (verified 2026-08-10)
- ZFS labels come from VASP **LDMATRIX = .TRUE.** in a **collinear** ISPIN=2 calc (NOT noncollinear/SOC; the web summary claiming otherwise is wrong).
- Implies LHFCALC=True / AEXX=0.0; **ISYM must be 3** (not 1/2).
- Output: OUTCAR table "Spin-spin contribution to zero-field splitting tensor (MHz)" + diagonalization block. Parse with `experimental/read_zfs.py` (modified pymatgen Outcar) or the line-based regex parser in the evidence dir.
- NV- reference: ISPIN=2, NUPDOWN=2, NELECT=862, Gamma-only, ENCUT 400, EDIFF 1e-6, LREAL Auto.
- Validation anchor: pristine NV D ≈ 3022 MHz (PBE spin-spin only, no SOC; +5.3% vs experiment 2870), principal axis [111], E ≈ 0.3 MHz.

## Procedure
1. **Build job dirs**: POSCAR (VASP Direct format; dataset npz → `data/CONTCAR_zx` reference, 214 C + 1 N layout, cell 10.667), INCAR variants: `ref` / `edi7` (EDIFF 1E-7) / `lreal` (LREAL=.FALSE.) / `encut500` (ENCUT 500) / `encut600` (convergence check), KPOINTS (Gamma 1x1x1), POTCAR (C+N: `experimental/step0_results/scf/POTCAR`), submit.slurm (repo root; `-p test --qos=qos_test`).
2. **Submit**: crisp submit is cwd-based but the daemon queue can be congested by other user jobs (QOSMaxSubmitJobPerUserLimit). Bypass: `crisp put` a tarball of job dirs to duguex_113, then via `crisp exec` extract and run a **nohup retry loop** that sbatch's each dir, sleeping 300s on QOS rejection — unattended slot filling.
3. **Parse**: upload a stdlib-only parser (line-anchored regex — OUTCAR lines have leading whitespace; never anchor patterns at line start without `^\s*`; `re.split(r"\s+", s)` keeps an empty first element — use `s.split()`). D = eig2 − 0.5(eig0+eig1), E = 0.5(eig1−eig0) from the diagonalization block (ascending eigenvalues).
4. **Analyze**: per-structure D std across variants = label noise floor; pooled = sqrt(mean(std²)).

## Measured result (2026-08-10, 20 jobs)
- Pooled D_std ≈ 0.48 MHz (0.055–0.568 per structure). Dominant source: **ENCUT non-convergence** (400→500: −1.2 MHz; 500→600: −2.95 MHz — still drifting at 600). EDIFF 1e-6→1e-7: 0.04 MHz; LREAL Auto→False: 0.12 MHz.
- Conclusion pattern: a 0.1 MHz target is unreachable on labels with ≥0.5 MHz settings sensitivity; the fix is a new protocol (ENCUT 700–800+ convergence study, EDIFF 1e-7, PREC=Accurate) and re-measurement.

## Evidence location
`/home/duguex/zfs_and_force/.worktrees/spin-model-cpu-audit/analysis/experiments/spin_multitask/label_noise_floor/` (zfs_tables.json, analyze_label_noise.py, evidence_summary.json).

## Gotchas
- ENCUT sensitivity varies by structure (max-distortion sample shifted only 0.12 MHz) — measure on multiple structures.
- VASP is deterministic: rerunning identical inputs measures nothing; the noise is settings sensitivity, so vary EDIFF/LREAL/ENCUT.
- Cluster login python3 has no numpy — parser must be stdlib-only.
