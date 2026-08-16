---
name: y2ti2o7-d12-reconstruction-control
description: "Check and interpret the existing Y2Ti2O7 doped 12 Å, 176-atom paired-antisite finite-size validation campaign."
---

# Y2Ti2O7 12 Å finite-size control

Use this when checking or interpreting the existing Y2Ti2O7 large-supercell validation campaign.

## Location and jobs

- Root: `/mnt/shared/home/2sidesniddle/vasp/validation/Y2Ti2O7_d12_reconstruction`
- `perfect`: crisp `30810b6f`, Slurm `211637`
- `Ti_Y_q+1`: crisp `2aa3cb48`, Slurm `211636`
- `Y_Ti_q-1`: crisp `09660319`, Slurm `211638`
- Cluster: `duguex_5`, partition `8259cl`, 48 tasks

Use `crisp jobs -n <task> --refresh`; never SSH directly.

## Controlled protocol

- Supercell generated through `vasp_sop.defect.builder._build_supercell_doped` with `min_distance=12.0`.
- Matrix `[[2,0,0],[0,2,0],[-1,1,1]]`; 176 atoms; lengths about 14.39/14.14/14.63 Å.
- All structures derive directly from one immutable host.
- Same POTCAR and KPOINTS; PBEsol, Ti U=4 eV, no SOC, `NSW=100`, `NELM=100`.
- Charges/electrons: Ti_Y(+1) `NELECT=1144`; Y_Ti(-1) `NELECT=1160`.
- Experiment metadata: `experiment.json`.

## Completion and interpretation

1. Require real VASP convergence from local outputs after fetch; do not trust a CRISP_COMPLETED marker alone.
2. Extract matched final energies and calculate:
   `ΔE_pair = E(Ti_Y_q+1) + E(Y_Ti_q-1) - 2 E(perfect)`.
3. Compare against the small-cell paired result and inspect relaxation displacement fields/local coordination.
4. Positive or near-zero large-cell `ΔE_pair` supports the finite-size/collective-reconstruction hypothesis.
5. Persistent deep-negative `ΔE_pair` refutes the simple undersized-cell explanation; then audit whether final structures entered a different host basin or phase.
6. Keep chemical potentials and VBM out of this verdict: the paired reaction cancels them.
