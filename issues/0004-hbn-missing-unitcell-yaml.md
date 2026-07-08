# hBN: DONE but missing unitcell/unitcell.yaml

**Date:** 2026-07-07
**System:** hBN (BN, mp-13150)
**Severity:** Medium — stale analysis output, unreliable formation energies

## Investigation

| Artifact | Present? |
|---|---|
| `cpd/target_vertices.yaml` | ✅ |
| `unitcell/band/OUTCAR` | ✅ |
| `unitcell/dos/OUTCAR` | ✅ |
| `unitcell/dielectric/OUTCAR` | ✅ |
| `unitcell/unitcell.yaml` | **❌** |
| `defect/defect_energy_summary.json` | ❌ (removed — see Fix) |

## Root Cause

`pydefect_vasp u` requires `band/vasprun.xml`, which was never collected from the cluster VASP runs. No `unitcell.yaml` was generated as a result (the `build_unitcell_yaml()` catches the exception and continues). The analysis pipeline nevertheless ran and produced `defect_energy_summary.json` — but without the band-edge/dielectric reference data that `unitcell.yaml` provides, the formation energies are unreliable.

All 16 DONE systems lack vasprun.xml on disk, yet 15 have unitcell.yaml. hBN is the only one whose VASP output collection was incomplete.

## Fix

1. Removed stale `defect_energy_summary.json` (generated without unitcell reference — unreliable)
2. System reverts to UC_DF phase — consistent state
3. To complete: retrieve `vasprun.xml` from cluster (crisp remote host), then re-run pipeline
4. After pipeline completes, unitcell.yaml and a correct defect_energy_summary.json will be generated
