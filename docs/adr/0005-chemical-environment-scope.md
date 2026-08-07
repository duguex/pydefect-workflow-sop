# Chemical-environment systems: COMPLETE is reached at CPD completion

A system may declare `scope: chemical-environment` in `plan.yaml`. Such a
system runs competing phases and the chemical-potential diagram only —
no unit-cell tasks, no defect calculations. Its COMPLETE gate is: target
relaxed, `target_vertices.yaml` + `standard_energies.yaml` +
`composition_energies.yaml` + `chem_pot_diag.json` present, and every
competing-phase directory converged (or explicitly excluded). The batch
loop never builds defect structures, prepares unit-cell inputs, or
submits UC/defect jobs for it.

We chose this over "infer scope from absence" because absence is
ambiguous: a defect-scope system mid-flight also lacks UC/defect
directories, and the batch loop would happily build and submit them —
which is exactly what happened to CsEuCl3 (a chemical-environment
reference system that had 37 unit-cell/defect jobs wrongly submitted
before the scope concept existed). Declaring intent in the plan makes
the machine refuse the unwanted leg.

Costs: the phase machine now has two completion shapes (full defect
workflow vs CPD-only), carried by `PipelineConfig.scope`. The persisted
phase (ADR 0001) can carry a stale UNITCELL_DEFECT written before the
scope was declared; one advance cycle re-derives and self-corrects it.