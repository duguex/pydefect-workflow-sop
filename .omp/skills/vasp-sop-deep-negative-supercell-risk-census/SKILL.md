---
name: vasp-sop-deep-negative-supercell-risk-census
description: "Rank vasp-sop systems for larger doped-supercell controls using raw E_diff, neutral defects, cell dimensions, and confounder separation"
---

# Similar-risk census for deep-negative defect energies

## Goal
Identify which systems deserve a larger-doped-supercell control without conflating formation-energy chemistry, high-charge artifacts, magnetic-state failures, or known structural-reference mismatch.

## Procedure
1. Use the freshest formation-energy CSV after stale parse repair.
2. Rank by raw $E_{diff}=E_{defect}-E_{perfect}$, never by $E_f$ alone.
3. Per system count:
   - $E_{diff}<-2$ eV;
   - $E_{diff}<-5$ eV;
   - neutral $q=0$ defects below -2 eV;
   - repeated non-vacancy substitution directions below -2 eV.
4. Read `defect/supercell_info.json` for atom count, transformation matrix, and lattice lengths; read plan `supercell.min_distance`.
5. Prioritize systems combining neutral deep negatives, repeated substitution anomalies, and cells around 10–12 Å. Atom count alone is insufficient.
6. Separate confounders before labeling a size effect:
   - structural-generation/reference mismatch;
   - high $|q|$ monotonic tails;
   - missing/incorrect MAGMOM in correlated magnetic hosts;
   - stale calc_results/corrections;
   - chemical-potential shifts (irrelevant to raw $E_{diff}$).
7. Design the larger-cell experiment from one immutable canonical host: perfect plus a neutral reciprocal substitution pair where possible. For charged pairs, require net charge and composition cancellation.
8. Interpret only after all members converge and final displacement fields are compared. A larger cell turning the paired reaction positive supports finite-size/reconstruction; persistent deep-negative values refute a simple small-cell explanation.

## 2026 reference ranking
After Y2Ti2O7: La2SrSc2O7 is the strongest clean target; Y2Sn2O7 is a small, clean cross-material check. Gd2GaSbO7:Bi needs magnetic-protocol cleanup before size attribution. La2Zr2O7 is lower priority because its strongest anomalies are charged.
