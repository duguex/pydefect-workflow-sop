---
name: vasp-canonical-same-host-defect-control
description: Diagnose deep-negative defect energies with a reservoir-free paired-antisite reaction and a canonical same-host VASP control; includes the pymatgen POSCAR species-regrouping trap.
---

# Canonical same-host control for deep-negative defect energies

Use this when opposite antisites or neutral defects have implausibly negative raw energy differences after parsing, convergence, POTCAR, and execution conditions have been checked.

## Decisive invariant

Construct a reservoir-free paired reaction:

`E(A_B^q) + E(B_A^-q) - 2 E(perfect)`

Atom reservoirs, net charge, `q·VBM`, and chemical potentials cancel. A deeply negative value in both U settings points to incompatible structural references rather than formation-energy postprocessing.

## Canonical control procedure

1. Select one immutable canonical host, preferably the structure embedded in `defect/supercell_info.json`.
2. Generate three structures directly from that same in-memory object:
   - unchanged perfect;
   - replace one canonical B site with A;
   - replace one canonical A site with B.
3. Preserve lattice and every fractional coordinate. Verify anonymous site assignment against perfect has RMS and max exactly 0 Å.
4. **Regroup sites by POTCAR order before writing POSCAR.** Pymatgen `Structure.replace()` preserves site order; writing directly can split a species into multiple blocks (for example `Y Ti Y Ti O`) and VASP will report five POSCAR species versus three POTCAR potentials. Rebuild site order as `[all Y, all Ti, all O]` (or the actual POTCAR order), then write POSCAR.
5. Verify species/count lines, POTCAR checksum, KPOINTS bytes, lattice, and NELECT. For charge `q`, `NELECT = neutral_valence - q` using actual POTCAR ZVAL.
6. Run matched `U=0`, SOC, `NSW=0`, `ISTART=0` single points with cache prefill disabled so POSCAR cannot be replaced.
7. Require electronic convergence from `vasprun.xml`, then compute each `E_def-E_perfect` and the paired reaction.

## Interpretation

If the old independently restored structures give a deeply negative paired reaction but the canonical same-host control becomes positive, structural-reference mismatch is causally established. Do not repair this by tuning U or chemical potentials; regenerate perfect and all defect structures from one immutable canonical host and rerun.

## Verified Y2Ti2O7 example

- Old mismatched U=0 paired reaction: `-10.572 eV`.
- Canonical same-host U=0 SOC result: `+8.007 eV`.
- Shift: `+18.580 eV`.
- Canonical individual raw differences: Ti_Y(+1) `+1.167 eV`; Y_Ti(-1) `+6.841 eV`.
