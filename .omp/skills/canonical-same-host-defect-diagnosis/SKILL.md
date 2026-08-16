---
name: canonical-same-host-defect-diagnosis
description: "Diagnose unphysical deep-negative defect energies by cancellation reactions, structural-provenance auditing, and a canonical same-host VASP control experiment."
---

# Canonical same-host defect energy diagnosis

Use when defect formation energies remain deeply negative after parser freshness, execution-condition, chemical-potential, charge, and Hubbard-U checks.

## 1. Build a cancellation signal

Choose opposite substitutions with charges that sum to zero, e.g. `Ti_Y(+1)` and `Y_Ti(-1)`, and evaluate

`E_pair = E(Ti_Y,+1) + E(Y_Ti,-1) - 2 E(perfect)`.

This exactly cancels atom reservoirs, chemical potentials, and `q·VBM`. A deeply negative result localizes the fault to raw VASP energies/reference structures rather than formation-energy postprocessing.

Repeat under U=0 if U is suspected. If the pair remains deeply negative, U is not the sole cause.

## 2. Audit structural provenance

Load the canonical host from `defect/supercell_info.json`. Compare it to perfect and defect POSCAR/CONTCAR structures while ignoring species:

- same lattice and atom count where applicable;
- periodic assignment RMS/max displacement;
- count positions differing by >1 Å;
- inspect baseline and recovery git snapshots, not only current files.

A substitutional defect should preserve the host position multiset apart from species replacement. Many host positions differing by >1 Å means the compared calculations are not local defects of one common host.

## 3. Build the decisive same-host experiment

From one immutable canonical structure:

1. Write `perfect` unchanged.
2. Replace exactly the selected canonical host index for substitution A.
3. Replace exactly the selected canonical host index for substitution B.
4. Use identical lattice, POTCAR, KPOINTS, and INCAR protocol.
5. Set charged-defect NELECT from POTCAR ZVAL: neutral valence minus charge.
6. Start with U=0, SOC, `NSW=0`, `IBRION=-1`, `ISTART=0`.
7. Submit with crisp `--skip-prefill` so cache prefill cannot overwrite POSCAR.

## 4. Hard pre-submit gate

Ignoring species, require each defect structure versus canonical perfect:

- assignment RMS = 0 Å;
- maximum displacement = 0 Å;
- identical lattice;
- identical POTCAR checksum;
- byte-identical KPOINTS.

Record canonical source, site indices/fractional coordinates, compositions, charge, neutral valence, NELECT, and input hashes in a manifest.

## 5. Verdict

After all three jobs converge electronically, recompute the paired reaction energy. If it changes from deeply negative to physically positive/nonnegative, structural-reference mismatch is confirmed. If it remains deeply negative, continue with electronic-state/pseudopotential physics using the now-clean structural baseline.
