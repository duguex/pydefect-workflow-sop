# Zero / tiny band-gap systems fail `unitcell.yaml` (`pydefect_vasp u`)

**Date:** 2026-07-14  
**Severity:** P1  
**Example:** SeO2 (`pydefect_vasp u` fails; `unitcell_build_status.json` → failed)  
**Related:** #0015, #0004

## Problem

`build_unitcell_yaml` calls `pydefect_vasp u` with band + dielectric. For metals / near-zero gap or broken band structure, `u` fails and the whole defect energetics path stays blocked (no `unitcell.yaml` → no efnv with proper dielectric/band edges).

## Desired strategy (decide + implement)

Pick one or more, document in FEATURES / agent-conventions:

1. **Fallback unitcell.yaml** from dielectric-only / manual VBM-CBM placeholders when band gap ~0 (with explicit warning + status)  
2. **Skip formation-energy full** and mark system `NO_TARGET` / `failed_analyze` with reason `zero_gap`  
3. **Hybrid functional / different band task** only for these formulas (plan.yaml flag)  
4. **Operator override**: allow shipping a hand-written `unitcell.yaml` that analyze trusts

## Acceptance

- [ ] Decision recorded in issue or FEATURES  
- [ ] SeO2 (and any similar) either gets a valid `unitcell.yaml` path or a **terminal honest status** (not endless UNITCELL_DEFECT with silent skip)  
- [ ] `unitcell_build_status.json` reason codes distinguish `zero_gap` vs missing vasprun vs other  

## Non-goals

- Full metal defect formalism research paper
