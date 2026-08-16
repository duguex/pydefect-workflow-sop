---
name: vasp-kpar-tetrahedron-crash-fix
description: "Diagnose crisp/VASP jobs failing with BZINTS Tetrahedron method fails (number of k-points 4): INCAR KPAR splitting a small irreducible k-point set below 4 under ISMEAR=-5. Fix with KPAR=1. Use when a batch job crashes with BZINTS/tetrahedron errors on large cells."
---

# KPAR vs tetrahedron k-point crash (crisp/VASP)

When a crisp VASP job fails with `VERY BAD NEWS! internal error in subroutine BZINTS: Tetrahedron method fails (number of k-points < 4)` (EXIT_CODE 1, slurm log):

1. **Read the failing slurm log first** — confirm the BZINTS line and which INCAR/KPOINTS the run actually used.
2. **Check INCAR KPAR** — batch-wide crisp templates inject KPAR=2 (sometimes 4). With ISMEAR=-5 (tetrahedron) VASP needs ≥4 irreducible k-points **per KPAR group**.
3. **Root cause pattern**: large cell + coarse k-mesh (e.g. 3×2×2 = 6 irrep) + KPAR=2 → 3 k-points/group → crash. Cells with denser meshes (3×3×3 ≈ 10+ irrep) never crash, which makes this look phase-specific.
4. **Fix**: set `KPAR = 1` in that dir's INCAR. It is a pure parallel-distribution parameter — energies and comparability are unchanged.
5. **No manual resubmit**: the batch loop's retry chain re-submits on the next cycle and picks up the edited INCAR. Verify via the next slurm log (`each k-point on N cores` shows 1 group; no BZINTS line).
6. A "did not specify MAGMOM" warning in the same log is benign convention for this batch (no MAGMOM set anywhere), not the cause.

Real case: Ba3W2O9/cpd/Ba8Mn8O21_mp-694888 failed 3× on duguex_5 (2026-08-14); KPAR=1 fixed it.
