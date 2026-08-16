---
name: vasp-sop-protocol-sandbox-verify
description: "Run the vasp_sop protocol sandbox acceptance loop (no calculation, input generation only): scripts/protocol_sandbox_prepare.py + protocol_sandbox_verify.py against a system tree, hard-gate INCARs against LEG_PROTOCOL/U_TABLE/INITIAL_MAGMOM, plus NELECT. Use after any change to the protocol module or VASP input generation (io.py), or to prove a system's inputs would be protocol-clean before re-running it."
---

# Protocol sandbox verification (vasp_sop)

Prove the protocol-driven generator (ADR 0024, `vasp_sop/vasp/protocol.py`) emits protocol-clean INCARs — **no VASP run, inputs only**. Full-tree run on a real system, then a hard-gate diff against the protocol tables (LEG_PROTOCOL / U_TABLE / INITIAL_MAGMOM / ENCUT rule + defect NELECT).

## When
- After editing `vasp_sop/vasp/protocol.py` (LEG_PROTOCOL/U_TABLE/INITIAL_MAGMOM/ENCUT rules) or `vasp_sop/vasp/io.py` (prepare_inputs CLI/API paths).
- Before mass-regenerating inputs for a system (acceptance evidence that regeneration would be protocol-clean).
- To reproduce the "protocol drift" acceptance for a candidate tree.

## Scripts

- `scripts/protocol_sandbox_prepare.py <src_sys> <sandbox_root> [--limit N]` — copies the system tree (outputs excluded: OUTCAR/vasprun.xml/CONTCAR/logs/.failed/backup dirs), clears INCARs, regenerates inputs per leg with real vise: cpd → CLI structure_opt leg, defect → API path with charge from dirname `_(-?\d+)$`, unitcell → per-task (4 legs). Idempotent (skips dirs whose INCAR exists). `--limit` for a smoke pass first. ~0.4 dir/s; Gd2GaSbO7:Bi full tree (200 dirs) ≈ 8 min.
- `scripts/protocol_sandbox_verify.py <sandbox_root> [--json out]` — per-dir INCAR vs LEG_PROTOCOL / U_TABLE / INITIAL_MAGMOM / ENCUT rule + defect NELECT (builder.verify_nelect). Hard gate: exit 1 on any violation. Checks per dir: ENCUT = 1.3×max ENMAX of that dir's POTCAR; leg keys (NSW/NELM/EDIFF/EDIFFG/SIGMA/LORBIT); ISPIN=2 for U-elements/defect/SOC; MAGMOM high-spin for INITIAL_MAGMOM elements (Gd=7); LDAUU/LDAUL numeric per species order.
- Exemptions: cpd ENCUT = per-dir 1.3×max ENMAX (partition-legal), cpd mol_* phases (NELM/EDIFF only), dielectric (NSW=1/LREAL=.FALSE., DFPT).

## Proven flow (2026-08-16, Gd2GaSbO7:Bi full tree)

1. Smoke: `--limit 2` on a throwaway root (~35 s) — catch script bugs before the 200-dir full run.
2. Full run to a persistent dir outside the batch root (e.g. `/mnt/shared/home/2sidesniddle/vasp/protocol_sandbox_<date>/`) — ~8 min for 200 dirs; run async and wait.
3. Verify; fix generator until 0 violations. **Violations → fix the generator/protocol, not the sandbox**, then regenerate the failing dirs (rm INCAR in sandbox, re-run prepare — it re-copies POTCAR from source, healing vise-rewritten POTCARs).

Acceptance evidence sandbox: `/mnt/shared/home/2sidesniddle/vasp/protocol_sandbox_20260815/` (Gd2GaSbO7:Bi, 200 dirs, 0 violations).

## Known violations discovered this way (fix in generator, not in verify)

- **vise rewrites POTCAR unconditionally** (CLI maps Ga_d→bare Ga 08Apr2002 while ENCUT was computed from the pre-rewrite POTCAR; both CLI `vise vs` and API `create_input_files` use built-in potcar_set: Ga→bare Ga 08Apr2002 ENMAX 282.7→134.7) → `prepare_inputs` backs up the preset POTCAR before generation and restores it after (io.py now; commit after ADR 0024 addendum). Any new generator path that shells to vise needs the same backup/restore. If you see ENCUT mismatch only on Ga-bearing dirs, check the sandbox POTCAR TITEL lines first.
- **plan pp bare Ga** (alphabetical-first pick diverged from vise potcar_set normal default Ga_d) → pp now defaults to the vise normal variant; passed to `--potcar` otherwise.
- **LDAUU numeric comparison in verify must float-normalize** (`5` vs `5.0` are equal).
- **NELECT check**: sandbox defect INCARs get NELECT from vise ZVALs; charged dirs must carry it.

## Gotchas

- POTCAR variants are owned by the PSP library (`/mnt/shared/VASP_POT/POT_GGA_PAW_PBE`) — never trust vise's built-in potcar_set for the final file.
- The check_results protocol-baseline dimension reads OUTCAR echoes; the sandbox has no OUTCAR, so verify reads INCAR directly — they are complementary.