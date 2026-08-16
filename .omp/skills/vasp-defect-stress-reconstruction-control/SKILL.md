---
name: vasp-defect-stress-reconstruction-control
description: Test whether deep-negative defect reconstruction is driven by fixed-cell host stress using an ISIF=3 perfect followed by same-lattice ISIF=2 defects.
---

# Defect stress/reconstruction control

Use when a small-supercell defect shows deep-negative energies and collective structural distortion, and the hypothesis is that fixed-cell stress drives the reconstruction.

## Controlled experiment

1. Start from one immutable canonical host (`supercell_info.json.structure`), not historical defect CONTCARs.
2. Run the pristine host with the same electronic protocol but `ISIF=3`; require electronic and ionic convergence.
3. Quantify lattice changes: each lattice length, angles, and volume. This establishes whether the original host was stressed.
4. From the converged ISIF=3 perfect structure, generate matched defects by direct same-site substitution. Regroup POSCAR species into POTCAR order after substitution; otherwise pymatgen may create split species blocks and VASP will reject the input.
5. Run defects at the relaxed host lattice with `ISIF=2`, retaining matched POTCAR, KPOINTS, Hubbard U, charge-specific NELECT, and spin protocol.
6. Compare initial-to-final structures using species-preserving periodic assignment. Report RMS, p95, maximum displacement, and count above 1 Å.
7. For complementary antisites, compute the reservoir-free paired reaction only after both defects are ionically converged:
   `E(A_B^q) + E(B_A^-q) - 2 E(perfect_ISIF3)`.

## Interpretation

- ISIF=3 changes the host lattice and fixed-lattice defects stop reconstructing: host stress is a likely primary driver.
- Distortion weakens but remains collective: host stress contributes, but a defect-induced soft mode or competing structural basin remains.
- Distortion is unchanged: uniform host stress is not the main cause.
- `CRISP_COMPLETED` or NSW exhaustion is not ionic convergence. Energies with final forces above EDIFFG are trajectory evidence only, never final formation or paired energies.

## Required evidence

- `Vasprun.converged_electronic` and `converged_ionic` for the perfect and each defect.
- Final maximum force.
- Lattice/volume change for perfect.
- Periodic displacement metrics for defects.
- Paired reaction only after convergence.
