---
name: vasp-sop-cpd-restart-convergence
description: "Diagnose vasp-sop cpd/defect ionic-restart convergence failures: magnetic-state drift across TIME-LIMIT restarts (mag= jumps in job logs, eV-scale energy gaps), vise parameter swallowing (NELM/EDIFF dropped from -uis, libs/vise fork U-table gaps like missing Ti), and EDIFF protocol relaxation. Use when a cpd phase keeps NSW-exhausting or restarting without converging."
---

# vasp-sop cpd/defect ionic-restart convergence diagnosis

Symptom: a cpd phase (e.g. Sr[FeO2]2) keeps exhausting NSW=50 rounds / TIME-LIMIT truncations without converging.

## 1. Magnetic-state drift (biggest cause, eV-scale)

Evidence: compare `mag=` column across restart rounds in `%j.log` / local slurm logs. Jumps (80→72→56 µB) with eV-level energy gaps (e.g. -396.10 → -393.04) = SCF landed in a different magnetic basin each round.

Root cause chain:
- `LWAVE=False` → no WAVECAR written
- No MAGMOM in INCAR → every restart re-runs SCF from VASP's default 1.0/site
- A TIME-LIMIT-truncated round's half-relaxed CONTCAR seeds the next SCF into a metastable basin → NSW burned on the wrong spin state

Fix (implemented 2026-08-11, commit 7369396):
- `patch_incar_magmom(work_dir)` in `vasp_sop/vasp/io.py`: MAGMOM in POSCAR atom order (Fe=5.0 high-spin, others 0.0), only when a magnetic species present, idempotent
- Wired into CLI-path `prepare_inputs`, API path `_prepare_inputs_vise_api`, and `cpd.py` generation (input_ready early-exit path needs the explicit call in cpd.py)
- Verify: `grep MAGMOM INCAR` — count entries == POSCAR atom count

## 2. vise parameter swallowing (silent protocol drift)

vise 0.9.5 drops NELM/EDIFF from `-uis` flags (vise_log records NSW only). Always patch after generation:
- CLI path (cpd/band/dos/dielectric/structure_opt): NELM=50, EDIFF=1e-4 (operator decision 2026-08-11)
- defect API path: NELM=30, EDIFF=1e-4, NSW=100
- `patch_incar` fallback after `run_local`/`create_input_files`

## 3. libs/vise fork U-table gaps

Production (.venv) uses the repo's `libs/vise` fork; its `u_parameter_set.yaml` lacks Ti (official vise has Ti:4). `set_hubbard_u=True` silently produces no LDAU for Ti cells — Y2Ti2O7 defects never had U while cpd (conda-env vise) had U=4.

Diagnose: `diff <(grep -E '^\s+\w+:' libs/vise/.../u_parameter_set.yaml) <(grep -E '^\s+\w+:' ~/.conda/envs/*/site-packages/vise/.../u_parameter_set.yaml)`
Fix: `_U_TABLE` in io.py includes all U-table elements (Ti 4.0,2) + `patch_incar_u` fallback in both generation paths (idempotent).

## 4. EDIFF protocol

- cpd/CLI path: EDIFF=1e-4 (relaxed from vise template 1e-7 — 1e-7 burns ~2x electronic steps for no force-level gain; EDIFFG governs ionic accuracy)
- defect: EDIFF=1e-4 already
- Sr[FeO2]2 special: EDIFFG=-0.01 (operator decision, only this dir — never let regeneration restore vise's -0.005; re-apply after prepare_inputs)

## 5. Test/QOS traps

test partition (duguex_113/101) has ~21 min time limit — TIME-LIMIT truncation is NORMAL there, loop auto-resubmits with `long` tag to 6138/compute clusters. A truncated round's log shows no `reached required accuracy`, stops mid-CG. Don't cancel long-cluster rounds unless they're on the wrong magnetic state (then: `crisp cancel -n <task_id>` → loop resubmits with the patched INCAR).

Workflow: (1) collect rounds from `~/.crisp/data/agent.db` jobs by local_dir; (2) grep `mag=` + energy in each %j.log; (3) if drifting → patch INCAR (MAGMOM + EDIFF) in place, cancel running round, loop auto-restarts; (4) verify new round's INCAR and partition before letting it run.

Tests: tests/test_io.py TestMagmomPatch (atom-order moments, no-magnetic-species no-op), TestCpdEdiffProtocol (1e-4 after mocked CLI gen).
