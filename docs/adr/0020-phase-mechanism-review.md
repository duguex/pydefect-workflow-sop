# ADR 0020 — Phase mechanism review: scheduler-vs-report split (decision deferred)

- Status: accepted (review only — no code change)
- Date: 2026-08-12
- Deciders: user (deferred implementation), agent

## Context

The 2026 batch incident (Gd2GaSbO7:Bi cpd 37 dirs regenerated at
06:56 but never resubmitted for 24h+) exposed structural costs of the
phase state machine.  The user asked for a review of whether the phase
mechanism still earns its place.

## Evidence (all from 2026-08-11/12 production)

1. **Monotonic advancement conflicts with rerun reality.**
   `_infer_phase_locked` treats `target_vertices.yaml` as an
   irrevocable point past COMPETING ("we never return COMPETING
   again").  But parameter protocols change (SOC/U/EDIFF/EDIFFG), the
   regeneration batch rewrites inputs and clears outputs, results go
   stale.  Any rerun need that arrives after the phase advanced has no
   code path: cpd submission lives only in the COMPETING branch
   (`wave2_submit` phase split, orchestrator.py:1044), so
   UNITCELL_DEFECT systems structurally cannot resubmit cpd dirs.
   Gd2GaSbO7:Bi cpd 37 is the direct victim; Y2Ti2O7 Bi defects and
   post-COMPLETE dopant additions are the same family.
2. **Exclusion logic is triplicated.** Phase gates use
   `is_anion_cation_antisite` (system.py), the scheduler uses
   `is_valid_defect_dir` (orchestrator.py), cpd uses
   `cpd_excluded_phases.yaml` (`_is_excluded_phase`).  Every consumer
   needs separate synchronization (four were touched in the 2026-08-12
   fix: blockers, status, preflight, calc_dirs).
3. **Dispatch is written twice.** Phase branches exist both in
   `advance_one_system` and inside `wave2_submit` — the same decision
   maintained in two places.
4. **Status-table phases mislead.** "COMPLETE" coexisted with a 73/185
   Defect display (the 185 included ADR 0013 exclusions); phase
   semantics and D/T counting diverged until the 2026-08-12 fix.

## Retained value (why the machine still exists)

- Post-processing gates are real data dependencies: CPD computation
  needs every competing phase converged (missing phases corrupt
  chem_pot_diag); wave3 pydefect needs the defect set done.
- Progress reporting ("where is this system") and wave ordering
  (structure_opt before defect building) are user-facing value.

## Conclusion (recorded, not implemented)

The phase machine earns its keep as a **read-only progress view plus
post-processing precondition**, not as the scheduler.  Target shape:

- One unified submission leg per cycle: every cpd/defect/unitcell dir
  whose disk state is unconverged + inputs ready + not excluded + no
  live job + dependencies satisfied (the deps DAG already exists) gets
  submitted.  Regenerated/drift dirs are picked up automatically —
  the Gd incident class disappears.
- Phase becomes a derived, display-only value (status table, webui).
- Real gates stay as condition checks (cpd-all-converged before CPD
  post; defect-set-done before wave3), not state branches.
- Exclusion logic collapses to one function family.

## Deferred work

- Issue #122 (2026-08-12): phase scheduler/report split — unified
  submission leg + phase-as-view.
- Interim minimal fixes (cpd resubmission leg under UNITCELL_DEFECT,
  single exclusion gate) are tracked in the same issue; the operator
  chose manual unlock for the current batch rather than code change.

## Consequences

- No code change now; the 2026 batch proceeds with manual unlocks.
- Future scheduler work starts from this ADR rather than re-deriving
  the evidence.
