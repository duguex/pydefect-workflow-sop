---
name: vasp-restart-spin-drift-diagnosis
description: "Diagnose a VASP relaxation that never converges across crisp restarts: correlate per-round mag= (integer jumps) and energy (eV-scale gaps) with LWAVE/MAGMOM settings to identify metastable-spin-basin drift, then lock moments with MAGMOM. Use when a vasp-sop/crisp job loops through NSW-exhausted/TIME-LIMIT restarts with no convergence, or when asked why a magnetic phase won't converge."
---

# VASP restart spin-drift diagnosis

## Symptom
A relaxation (cpd/defect) restarts every round (NSW exhausted or TIME LIMIT) and never reaches EDIFFG; each round's OUTCAR `F=` lines show integer magnet moment jumps (`mag= 80.0000` → `72.0000` → `56.0000`) and eV-scale total-energy gaps between rounds (-396.10 → -393.87 → -390.99).

## Root cause
Every restart re-runs SCF from scratch when the job has `LWAVE = False` (no WAVECAR) and INCAR has no `MAGMOM`: VASP starts from its default 1.0 μB/site, the SCF can land in a metastable spin basin, and the whole NSW budget is burned on the wrong spin state. The energy gap (up to ~5 eV for Fe oxides) is real physics, not noise — the round is wasted.

## Diagnosis procedure
1. List job rounds: `crisp jobs` / agent.db `jobs` table (submit_time, complete_time per id).
2. Per round, from the slurm `%j.log` (local dir): first/last `F=` lines — read `mag=` at ion step 1 and the last step.
3. Integer mag + large energy gap between rounds ⇒ spin drift (not force convergence).
4. Check INCAR: `LWAVE`, `MAGMOM`, `ISTART`. `ISTART=1` without WAVECAR does nothing.
5. Check EDIFF: vise-template 1e-7 on 20 cores burns ~170s/ion step; relax to 1e-4 (vasp-sop CLI path now does this; cpd EDIFFG governs accuracy).

## Fix
- `patch_incar_magmom` (vasp-sop vasp/io.py): MAGMOM in POSCAR atom order, magnetic species high-spin (Fe=5.0), others 0.0. Only species in `_MAGMOM_TABLE` trigger it.
- Cancel the current metastable round, let the loop resubmit from CONTCAR with the patched INCAR — the new round converges to the intended basin (e.g. 80 μB ferromagnetic ground state for Sr[FeO2]2).
- Verify after resubmit: first `F=` line mag matches the MAGMOM target.

## Pitfalls
- eval/IPython kernel caches the old module: `importlib.reload` before calling new io functions, or run via fresh subprocess with the repo venv.
- Production venv uses the repo's libs/vise fork, whose U table lacks Ti — set_hubbard_u alone silently emits no LDAU for Ti cells; rely on `patch_incar_u` fallback instead of vise's output.
