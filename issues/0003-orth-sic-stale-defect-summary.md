# orth-SiC: defect_energy_summary.json exists without CPD completion

**Date:** 2026-07-07
**System:** orth-SiC (SiC, mp-11713)
**Severity:** Medium — stale analysis output, unreliable formation energies

## Symptoms

Production data-integrity scan (`test_production.py::test_phase_artifact_inventory`) flagged:

```
orth-SiC: COMPETING but defect_energy_summary.json exists
```

## Investigation

| Artifact | Present? |
|---|---|
| `cpd/composition_energies.yaml` | ✅ |
| `cpd/standard_energies.yaml` | ❌ |
| `cpd/target_vertices.yaml` | ❌ |
| `unitcell/unitcell.yaml` | ❌ |
| `defect/defect_energy_summary.json` | ✅ (7 351 B) |

The system has 18 competing-phase OUTCARs, 170 defect subdirectories (215 defect OUTCARs), and a `defect_energy_summary.json` — but **no CPD phase diagram** was ever solved. The analysis pipeline ran against incomplete CPD data, producing a formation-energy summary that lacks proper chemical-potential reference points.

## Root Cause

The CPD diagram step (`pydefect cv` / `pydefect pc`) was never executed or failed silently. `composition_energies.yaml` has raw VASP energies, but `standard_energies.yaml` (molecule corrections) and `target_vertices.yaml` (chemical-potential vertices) were never generated. The analysis pipeline (`_analyze_defects`) was apparently invoked anyway — likely via a manual `batch run` cycle or direct CLI call — and produced an unreliable summary.

orth-SiC has 17 competing SiC polymorphs from MP (multiplicity is unusually high), which may have caused the CPD solver to struggle or time out.

## Fix

1. Complete CPD: run molecule corrections + standard energies generation + chemical-potential diagram
   - `pydefect sre` to generate `standard_energies.yaml`
   - `pydefect cv` to solve convex hull → `chem_pot_diag.json`
   - `pydefect pc` to plot → `target_vertices.yaml`
2. Re-run analysis: `build_unitcell_yaml` + `_analyze_defects` to regenerate `defect_energy_summary.json` with correct reference energies
3. The stale summary should be removed before re-analysis, or overwritten by the pipeline

## Related

- Issues #0001, #0002 cover other CPD-edge cases (target lookup, 4-element skip)
