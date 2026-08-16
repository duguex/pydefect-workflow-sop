---
name: partial-vasp-relaxation-structural-readout
description: Read structural evidence from electronically converged but ionically unconverged VASP relaxations without misusing provisional energies.
---

# Partial VASP relaxation structural readout

Use this procedure when a VASP defect relaxation finishes electronically but not ionically, and the question is whether it already contains useful structural evidence.

1. Read `vasprun.xml` with `Vasprun` and record `converged_electronic`, `converged_ionic`, ionic-step count, final total energy, and final maximum force.
2. Treat the energy as provisional whenever `converged_ionic` is false. Do not calculate or publish final formation energies from it.
3. Compare the initial `POSCAR` or canonical `defect_entry.json` structure with the latest `CONTCAR` using species-preserving assignment and the actual periodic lattice metric. Avoid raw fractional-coordinate differences and naive minimum-image approximations.
4. Report RMS displacement, p95, maximum displacement, and counts above 0.5/1.0/2.0 Å. These are valid trajectory indicators even before ionic convergence.
5. Inspect energy and force at several ionic steps. A continuing energy decrease and fmax well above `|EDIFFG|` means the structure is still moving; do not infer a final basin or final reaction energy.
6. If the structural signal is already large, the hypothesis that the larger supercell suppresses reconstruction is falsified at the trajectory level. Continue from the latest `CONTCAR` with a larger `NSW`; do not restart from the original POSCAR.
7. Only after all matched perfect/defect structures are ionically converged compute raw differences and paired reactions such as `E(A_B)+E(B_A)-2E(perfect)`.

A partial result can establish persistence of reconstruction, but never the final thermodynamic conclusion.
