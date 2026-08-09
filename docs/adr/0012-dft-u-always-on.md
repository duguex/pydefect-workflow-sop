# DFT+U always on — vise auto-adapts by element, plan field retired

The pre-automation script applied `set_hubbard_u True` unconditionally to every `vise vs` invocation, so every transition-metal / f-electron system got DFT+U automatically. The plan-driven pipeline replaced that with an explicit `hubbard_u` plan field, and the hand-written plans for the Fe systems (MAl4O7 ×3, SrGa4O7:Fe) and ZnO shipped with `hubbard_u: false` — a silent regression from "always +U" to "no +U" for every 3d system. The automatic generator (`needs_hubbard_u`, driven by `_DTFU_FALLBACK`) existed but only served generated plans; hand-written plans never saw it. We now restore the original semantics: **DFT+U is always enabled at the vise layer** (`set_hubbard_u=True` unconditionally in the defect and unitcell input paths), and vise auto-adapts per element — elements in its U table (3d Mn–Ni, Cu, Zn, lanthanides) get U, everything else gets none. The `hubbard_u` plan field is retired (ignored; generated plans keep writing it harmlessly, hand-written ones no longer matter).

This also fixes a unitcell inconsistency where `structure_opt` went through the API path (plan-controlled U) while band/dos/dielectric used the CLI template with unconditional `set_hubbard_u True`.

## Considered options

- **Keep the plan field but default it to auto-detection** — rejected: the failure mode was hand-written plans, which would still carry explicit `false`; only "always on at the vise layer" removes the data dependency entirely.
- **Extend the U table for Ti/Zr/Sn/Sc** — rejected for now: vise's default table omits these elements by design; adding custom U values is a physics choice that needs per-element parameterization and is deferred (the four systems whose `hubbard_u: true` was ineffective — La2Zr2O7, La2SrSc2O7, Y2Ti2O7, Y2Sn2O7 — are cleaned to `false`).

## Consequences

- Affected systems (contain U-table elements, currently without +U): Fe ×4 (MAl4O7 ×3, SrGa4O7:Fe) and ZnO — their INCARs get `LDAU=True` with vise defaults (Fe/Zn `LDAUU=3`/`5`) when regenerated.
- Regeneration is deferred until the 2026-root chain seeding wave finishes (operator decision); resumed calculations keep their CONTCAR as the +U starting geometry (restart semantics, no clean rerun).
- Systems without U-table elements (e.g. BaAl2B2O7) are unaffected — vise simply does not enable LDAU.
