# COMPLETE requires every calculation on disk to have converged

A system is COMPLETE only when **every calculation directory on disk has
passed the convergence verdict**: all cpd/ phases (except `combos` and
explicitly excluded phases), all unitcell tasks (band/dos/dielectric),
and every defect directory including perfect — plus the full analysis
summary `defect_energy_summary.json` (a partial one does not count). A
directory that ran and failed, or was never prepared, keeps the system in
UNITCELL_DEFECT.

We chose this over the previous failed-gate (issue #0005: "failed defects
do not block COMPLETE") because the exemption silently laundered failures:
on the production tree, systems read COMPLETE while 50 of 57 defect
calculations had failed and the only analysis output was a partial
summary. The status table's new % column exposed the gap, and the phase
machine now agrees with it — COMPLETE and 100% coincide.

Costs: a system with any failed/unconverged calculation stays
UNITCELL_DEFECT until a human resolves the failure (re-run, fix inputs,
or exclude the phase via `cpd_excluded_phases.yaml`). The batch loop does
not auto-resubmit `failed`/`unconverged` dirs (wave2 skips them), so no
resubmission loop is introduced. Phase-persistence (ADR 0001) is
unaffected: once `target_vertices.yaml` exists the system never returns
COMPETING, but it can now be pinned in UNITCELL_DEFECT by an unconverged
competing phase that was previously invisible.