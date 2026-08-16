---
name: vasp-sop-protocol-module-ops
description: "Operate vasp_sop's single-source protocol module (vasp_sop/vasp/protocol.py, ADR 0024): change any INCAR protocol value, diagnose protocol drift, or extend check_results protocol-baseline dimensions. Use when protocol values need editing, when 协议不符/无MAGMOM warnings appear, or when asked 协议统一/规范化."
---

# vasp_sop protocol module operations (ADR 0024)

`vasp_sop/vasp/protocol.py` is the single source of VASP protocol truth: per-leg parameters (LEG_PROTOCOL), DFT+U (U_TABLE), initial moments (INITIAL_MAGMOM), ENCUT rule. All generator paths (vise CLI / API / unitcell) and check_results read from it.

## Structure

- `LEG_PROTOCOL`: per-leg NSW/NELM/EDIFF/EDIFFG/SIGMA/LORBIT. defect: NSW=100/NELM=30/EDIFFG=-0.01/SIGMA=0.02/LORBIT=11; cpd+structure_opt: NSW=50/NELM=50/EDIFFG=-0.01; band/dos: NELM/EDIFF only (no EDIFFG — single point); dielectric: NSW=1 + DFPT.
- `U_TABLE`: Ti(4)/3d(Mn-Fe-Co-Ni 3)/Cu-Zn(5)/4f lanthanides(5, L=3). Ti included because the libs/vise fork's table lacks it.
- `INITIAL_MAGMOM`: high-spin for Mn/Fe/Co/Ni + 4f (Gd=7 fixes the SOC moment collapse); Ti/Cu/Zn excluded (d0/weak/d10).
- ENCUT: `effective_encut(config, work_dir)` = plan/config value first, else 1.3×max ENMAX of the directory POTCAR. Never lets vise's template decide.

## Key traps (empirically found 2026-08-16)

- **vise rewrites POTCAR on every generation** (both `vise vs` CLI and API `create_input_files`) using its built-in potcar_set — CLI maps Ga_d→bare Ga while ENCUT was computed pre-rewrite. `prepare_inputs` must backup/restore the preset POTCAR.
- **pp defaults align to vise potcar_set normal** (`list_potcar_variants` first entry = normal default, alphabetical fallback), not alphabetical first — bare Ga diverged from Ga_d and reached vise via `--potcar`.
- **stage2_soc is derived from soc** (`params.get("stage2_soc", soc)`) — ADR 0014 two-phase is the only SOC strategy; plan carries no stage2_soc line. Explicit false keeps a single-phase escape hatch.
- **plan `Available phases from MP` comment = host-formula polymorphs** (fetch_formula_polymorphs, E_hull-ascending, `(default)` = current poscar_src), NOT the cpd competing phases — those live in cpd/ + cpd_excluded_phases.yaml.
- **MP summary.search fields**: `spacegroup`/`lattice_parameters` are invalid (400); use `symmetry` + `structure`.
- **ISIF partition**: cpd=3 / defect=2 / unitcell-structure_opt=3 / **perfect=3** (defect-free supercell relaxes lattice; defect dirs inherit its cell at ISIF=2). check_results expects this.

## Editing workflow

1. Edit protocol.py → run `tests/test_protocol.py` + `tests/test_protocol_generation.py` + `tests/test_io.py`.
2. Prove generator output with the sandbox loop (see vasp-sop-protocol-sandbox-verify).
3. Re-run full-batch check_results (protocol-baseline dimension flags any deviation).
