---
name: vasp-bzints-irreducible-kpoint-diagnosis
description: "Diagnose VASP BZINTS \"Tetrahedron method fails (number of k-points 4)\" under ISMEAR=-5 when KPAR=1 alone doesn't fix it: verify the real irreducible k-point count with spglib, distinguish KPAR-split vs symmetry-folding, and densify the KPOINTS grid (keeping ISMEAR=-5 and shift) instead of only flipping KPAR. Use when a crisp/vasp-sop VASP job crashes with the BZINTS/tetrahedron error and KPAR=1 was already tried, or when a large-cell cpd/defect phase fails repeatedly with the same error across resubmits."
---

# VASP BZINTS tetrahedron k-point diagnosis

The stock fix for `BZINTS: Tetrahedron method fails (number of k-points < 4)` is "set KPAR=1" (KPAR splitting a small irreducible set below 4). **That is not always sufficient** — verified 2026-08-14 on Ba8Mn8O21_mp-694888: after KPAR 2→1 the job crashed with the identical error, because VASP's own symmetry reduction folded the nominal 3×2×2 grid (12 points) down to 3 irreducible k-points, while spglib (symprec=1e-5) reported 8. The real fix was a denser grid.

## Diagnostic sequence

1. **Check the actual irreducible count** before touching anything:
   ```python
   import spglib
   from pymatgen.core import Structure
   s = Structure.from_file('POSCAR')
   cell = (s.lattice.matrix, s.frac_coords, s.atomic_numbers)
   mapping, grid = spglib.get_ir_reciprocal_mesh([3,2,2], cell, is_shift=[0.0,0.5,0.5])
   print(len(set(mapping.tolist())))  # nominal irreducible count
   ```
   Note: this is an upper bound — VASP's ISYM reduction (coarser symprec) can fold further. If the count is already <4, the grid is simply too coarse.
2. **Verify KPAR actually took effect**: grep the slurm log for `distrk: each k-point on N cores, 1 groups` (1 group = KPAR 1). If it still says `2 groups`, the INCAR the daemon shipped was stale (submit-time snapshot — check INCAR.tuned / INCAR mtime vs submit time).
3. **If KPAR=1 and count still <4**: the error message's trailing number is VASP's NKPTS (`...k-points < 4) 3` means 3). Densify the KPOINTS grid — keep ISMEAR=-5 (energy comparability across a batch) and keep the same shift (usually `0.0 0.5 0.5` from vise):
   ```
   <comment>
   0
   Gamma
   4 3 3        ← was 3 2 2
   0.0 0.5 0.5
   ```
   Pick the smallest grid that gives a comfortable margin (target ≥6 irreducible after VASP's folding, e.g. 4×3×3 for a 37-atom cell). Do NOT switch to ISMEAR=0 unless the batch protocol allows it — Gaussian smearing energies are not comparable with ISMEAR=-5 phases.
4. **Verify the fix landed**: the next resubmit must get past the crash — the log should show `DAV:` electronic-step lines (BZINTS runs before the first SCF step, so any DAV lines mean the k-points are fine). If the batch loop was erroring on this dir (`Cannot parse CPD phase structure`), the parse error clears once the job finishes.

## Context traps

- The failing job may still be submitted from a stale submit-time snapshot: fix the on-disk files, then confirm the NEXT crisp task id (agent.db `jobs` id > last failed id) actually ran with the new KPOINTS.
- This crash class is most likely on large-cell cpd phases (small k-grid per kspacing) with high apparent symmetry; phases with 3×3×3 grids rarely hit it.
- A `POSCAR found : N types and M ions` banner plus an immediate BZINTS error with no DAV lines = crash before SCF, not an electronic-convergence problem.
