---
name: vasp-sop-formation-energy-sanity-audit
description: "Sanity-audit unphysical vasp-sop defect formation energies and run controlled identical-geometry Hubbard-U comparisons without conflating raw E_diff, chemical potentials, or stale parse artifacts."
---

# vasp-sop defect formation energy sanity audit

Use when 2026/2025 batch formation energies look unphysical (deep-negative antisite/vacancy), or before trusting any defect-energy summary.

## Pipeline

1. Reconstruct `E_f = E_def − E_perfect + q·vbm − Σ std_k·v_k` from authoritative newest converged Slurm-log `F=` energies; local OUTCAR may be truncated.
2. Strip to `E_diff = E_def − E_perfect`. Any `E_diff < −2 eV`, especially both antisite directions negative, is suspect.
3. Compare perfect and defect execution conditions: NSW/ISMEAR/SIGMA/ISPIN/LSORBIT/ENCUT/EDIFF/EDIFFG/IBRION/ISIF/NELM/ISYM/LORBIT/PREC/KPOINTS/POTCAR order and hashes/LDAUU/LDAUL/MAGMOM/NELECT.
4. Verify geometry provenance and local character: index-aligned displacements, lattice, target-site identity, StructureMatcher, and converged-CONTCAR snapshot provenance.
5. Cross-check perfect against unitcell per formula unit.
6. Triangulate historical logs and isostructural systems.

## Controlled Hubbard-U test

To test whether U causes a deep-negative energy:

- Create a separate experiment tree; never mutate production.
- Copy each production `CONTCAR` byte-for-byte to control `POSCAR`; copy identical POTCAR and KPOINTS.
- Start from the executed `INCAR.tuned`; remove all LDAU/LDAUL/LDAUU/LDAUTYPE/LDAUPRINT/LMAXMIX tags, set `NSW=0`, `IBRION=-1`, `ISTART=0`, retain SOC and charge-specific NELECT.
- Record SHA-256 identities in a manifest.
- Submit with Crisp `--skip-prefill` so cache prefill cannot replace the controlled geometry.
- Require `Vasprun.converged_electronic` for every perfect/defect control.
- Compare paired `E_diff(U)=E_def(U)-E_perfect(U)`; never compare absolute total energies across U settings.
- First inspect production inputs: if they already lack LDAU tags, they are already U=0 and duplicating them is not a U test.

## Known Y2Ti2O7 result (2026-08-13)

Identical-geometry SOC single points disproved “Ti U=4 uniformly causes all deep-negative defects”:

- Ti_Y6_1: E_diff U4 −7.017 → U0 −9.048 eV (worse −2.031)
- Y_Ti5_-1: −6.832 → −1.524 eV (improves +5.308)
- Va_O10_2: −5.819 → −5.325 eV (only +0.495)

U has strong defect-dependent and opposite-direction effects, but is not the common root cause. Evidence lives at `/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect/_experiments/u0_deep_negative_20260813/`.

## Traps

- `grep "F="` matches NGXF; target ionic summary lines or parse strictly.
- `calc_summary.json` can report convergence while a newer failed log exists.
- Three-component `mag=` identifies SOC; one component is non-SOC.
- A completed Slurm job may sit in Crisp `ready_fetch`; wait for fetched outputs before analysis.
- Formation-energy chemical-potential terms may move values even when `E_diff` stays deep negative; keep those questions separate.
