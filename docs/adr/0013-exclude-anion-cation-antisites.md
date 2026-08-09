# Anion-cation antisites excluded from the defect set

pydefect's `DefectSetMaker` enumerates every substitution between any host
element and any enumerated species — including physically unreasonable
"anion-cation" antisites: an anion on a cation site (e.g. `O_Ga1`, `S_Ba1`)
and a cation on an anion site (e.g. `Ti_O1`, `Bi_O1`). These are high-energy
configurations that never enter the useful defect set, yet they dominated
the non-converging tail of the production runs (2026 root: 851 such dirs,
2025 root: 448; combined, 466 of them had already converged after wasted
core-hours). They also hold charge-state chains hostage: a chain whose root
is such an antisite stalls until a human retries it, blocking its
chemically meaningful siblings.

The defect set now excludes anion-cation antisites **at the directory
validity gate** (`is_valid_defect_dir`), the single entry point shared by
wave2 submission and post-processing enumeration. "Anion-role" elements are
O, S, Se, Te, F, Cl, Br, I, N, P; a single substitution `X_Yn_q` where
exactly one side is an anion-role element is excluded. Vacancies, complex
defects (`Gd_Ga1+Va_O1_-1`) and metal↔metal substitutions (`Gd_Sb1`,
`Sb_Ga1`, `Fe_Ca1`) are untouched; metalloids (Sb, Ge) count as cations
because they occupy cation roles in these oxide hosts.

## Considered options

- **Keep the full enumeration** (old-script parity) — rejected: the
  enumerated set is not a plan decision but a pydefect default, and the
  excluded configs are never wanted in the final defect energies; the audit
  confirmed parity only, not intent.
- **Filter by first-cycle formation energy** — rejected: adds a cheap-run
  pass and a decision layer for configurations that are excluded by
  chemistry alone, for free.
- **Delete the excluded directories** — rejected (operator decision): the
  gate excludes without destroying; the 466 already-converged results stay
  on disk but leave the analysis, and the filter can be lifted by a one-line
  revert.

## Consequences

- Applied to **both batch roots** (2026 + 2025) for consistency (ADR 0010
  chain consistency was already extended to 2025).
- Excluded dirs remain on disk but are never submitted, counted, or
  analyzed; `defect_energy_summary.json` and pydefect post-processing
  automatically omit them.
- The +U/SOC regeneration batch skips excluded dirs.
- If a physically meaningful anion-cation substitution is ever needed
  (e.g. a halogen dopant on an O site), lift the filter for that system —
  the dirs are still on disk.
