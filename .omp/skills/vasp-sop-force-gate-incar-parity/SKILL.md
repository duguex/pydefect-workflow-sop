---
name: vasp-sop-force-gate-incar-parity
description: "Diagnose force-gate/stall non-convergence in vasp-sop defect/cpd dirs: read slurm logs + verify INCAR tag parity against a converged sibling before any parameter decision, and unlock past-phase systems correctly."
---

# Defect/cpd force-gate INCAR parity diagnostic

Use when a vasp-sop defect or cpd phase keeps failing with `force_gate_fail` / `N ionic restart(s) without convergence` / stall / ZBRENT churn, and you are about to recommend a parameter decision (EDIFF/EDIFFG/MAGMOM/NUPDOWN/NSW).

## Order of operations (mandatory)

1. **Read the slurm logs first** (never diagnose from verdict+max_f alone): grep the newest `*.log` for `ZBRENT`, `DAV:`, `F=`, `mag=`, and the tail marker (`CRISP_COMPLETED` / `CRISP_FAILED`). ZBRENT `fatal error in bracketing` / `can't locate minimum` / `extrapolating` = rough energy surface → check electronic protocol, not just force gate.
2. **INCAR tag-parity check**: compare the stuck dir's INCAR against a CONVERGED SIBLING dir in the same leg (prefer same defect family). Keys: SIGMA, LORBIT, NELM, NSW, EDIFFG, LDAU, MAGMOM, NUPDOWN.
   - Missing SIGMA/LORBIT/NELM where siblings have them = the INCAR was silently stripped (vise API branch drops extra_uis + hubbard_u; pattern documented in `defect-incar-parity-regenerate`). SIGMA falling back to default 0.2 over-broadens smearing → blurred energy surface → force jumps + ZBRENT → permanent non-convergence. Fix by regenerating inputs (delete INCAR, re-run `prepare_inputs` with `extra_uis="SIGMA 0.02 LORBIT 11"` and charge), verify `verify_nelect`, restart from CONTCAR, `batch retry`.
3. Only after 1+2 are clean should you consider force-gate / spin / step-count parameter decisions (EDIFFG, NSW, MAGMOM/NUPDOWN).

## Traps

- `prepare_inputs` skips dirs that already have an INCAR → a stripped INCAR persists across retry cycles until something forces full regeneration (e.g. the loop's missing-POTCAR completion pass). Don't trust an existing INCAR as correct by presence alone.
- INCAR mtime newer than OUTCAR doesn't always mean the run was stale: check `INCAR.tuned` — that is the authoritative executed input (crisp uploads it renamed to INCAR); a plain INCAR rewritten after submission can be the stale one.
- A single stripped-INCAR event can hit several dirs at once (observed: 4 BaAl2B2O7 defect dirs), so after fixing one, scan the whole leg for the same tag-missing pattern.

## Phase-regress unlock addendum (ADR 0020 deadlock class)

When unlocking a system past COMPETING by removing `target_vertices.yaml` (back it up to `<sys>/.phase_bak_<date>/` along with standard_energies/composition_energies/relative_energies/chem_pot_diag.json):
- Pre-mark old_full_soc cpd dirs (LSORBIT+converged but no `soc_stage2` JobStore record) with a `soc_stage2` record BEFORE regress, else `_stage2_soc_pending` re-submits them as duplicate SOC single points.
- If the target dir's OUTCAR was cleared (stale converged JobStore record), the system parks at STRUCTURE_OPT, not COMPETING — the target must re-run; this is expected and the loop submits it automatically.
- Resolve duplicate CPD compositions by comparing per-atom TOTEN (guard against 2x-supercell-vs-primitive factor-of-2 traps), keep lower, `mv` the higher OUT of `cpd/` (e.g. `.<sys>/.dup_bak_<date>/`).
