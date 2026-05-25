# PyDefect Workflow SOP

> Point-defect VASP calculation standard operating procedure based on **pydefect** + **vise**.
> Covers the complete lifecycle: perfect cell preparation → competing phases → defect generation → VASP calculation → post-processing (eFNV correction, formation energy plots, defect level analysis).

## Background

[PyDefect](https://kumagai-group.github.io/pydefect/) is a Python library for first-principles point-defect calculations in crystalline solids. It automates the tedious parts of the workflow — supercell generation, defect enumeration, charge correction (eFNV), and formation energy analysis — while maintaining rigorous physical standards.

This SOP codifies a battle-tested 7-phase workflow, built from the official pydefect documentation and validated end-to-end on **3C-SiC** and **diamond** systems. It captures every pitfall, naming quirk, and edge case discovered over months of production use.

## Workflow Overview

```
project/
├── unitcell/         # Phase 1: Perfect cell (opt, band, DOS, dielectric)
├── cpd/              # Phase 2: Competing phases (chemical potential diagram)
└── defect/           # Phase 3-7: Defects (generation, VASP, post-processing)
```

| Phase | Description | Key Tools |
|-------|-------------|-----------|
| 1 | **Perfect cell** — structure optimization, band, DOS (w/ AECCAR for interstitials), dielectric | `vise vasp_set`, `pydefect_vasp unitcell` |
| 2 | **Competing phases** — CPD construction & chemical potential diagram | `pydefect_vasp make_poscars`, `pydefect cpd_and_vertices` |
| 3 | **Defect generation** — supercell, defect set, interstitials, defect entries + VASP inputs | `pydefect supercell`, `defect_set`, `local_extrema`, `vise vasp_set -t defect` |
| 4 | **VASP calculation** — batch submission via crisp | `register_job()` Python API |
| 5 | **Post-processing** — eFNV correction, formation energies, defect levels | `pydefect efnv`, `defect_energy_infos`, `plot_defect_formation_energy` |
| 6 | **Incremental doping** — adding dopants to an existing project | `pydefect defect_set -d <element>`, CPD update |
| 7 | **Complex defects** — multi-body defects (vacancy clusters, dopant pairs) via `pydefect-complex` | `ComplexDefectMaker` API |

## Quick Start (Hermes Agent)

```bash
# Install the skill directly from GitHub
hermes skills install \
  https://github.com/duguex/pydefect-workflow-sop/raw/main/SKILL.md

# Then load it in any session
# /skill pydefect-workflow
# or
hermes -s pydefect-workflow
```

## Quick Start (Manual)

```bash
# Install dependencies
pip install pydefect vise pymatgen

# Verify installation
bash scripts/verify-installation.sh

# Start from Phase 1 (see docs/ for detailed guides)
cd your-project/
vise vasp_set -x pbesol -t structure_opt
```

## Key Features

- **Complete SOP** — from primitive cell to publication-ready formation energy diagrams
- **Proven on real systems** — validated on 3C-SiC (full test report) and diamond (2Va_C1 complex defect)
- **Pitfall documentation** — every edge case, `NotPrimitiveError`, monty serialization quirk, and crisp submission gotcha is documented
- **Complex defects** — dedicated `pydefect-complex` extension for N-body defects (pairs, trimers, dopant-vacancy clusters)
- **Hermes Agent integration** — native Hermes skill format; agents can load, follow, and execute the workflow autonomously

## Project Structure

```
pydefect-workflow-sop/
├── SKILL.md                 # Complete SOP in Hermes Agent skill format
├── README.md                # This file
├── LICENSE
├── references/              # Deep-dive reference docs
│   ├── 3c-sic-test.md           # Full 3C-SiC workflow validation
│   ├── diamond-2va-c1-verification.md  # Diamond complex defect verification
│   ├── defect-entry-json-fix.md       # Monty serialization fix for defect_entry.json
│   ├── crisp-batch-submit.md         # Batch submission via crisp Python API
│   ├── cpd-extension.md             # Adding dopant CPD phases
│   ├── complex-defects.md           # Complex defect generation details
│   ├── doped-comparison.md          # Comparison with doped toolkit
│   ├── interstitial-workflow.md     # Interstitial defect workflow
│   └── official-dos-command.md      # DOS command reference
├── docs/                   # Supplementary guides (coming soon)
└── scripts/
    └── verify-installation.sh  # Environment verification script
```

## Environment

Tested with:
- pydefect 0.9.12
- vise 0.9.1
- pymatgen 2025.6.14
- Python 3.11+
- VASP 6.x

## Limitations

- Only supports **non-magnetic, non-metallic** systems via VASP
- For magnetic/metallic defects, see the alternative [doped](https://github.com/SMTG-Bham/doped) toolkit (v3.2.1+), documented in `references/doped-comparison.md`
- Complex defect generation (`pydefect-complex`) is an independent extension library, not part of upstream pydefect

## License

MIT

## Citation

If you use this SOP in your research, please cite the pydefect paper:

> S. Kokott, F. Karsai, and M. Scheffler, "PyDefect: A Python library for point-defect calculations in solids", *J. Open Source Softw.* (2024)

And the vise paper:

> S. Kokott, F. Karsai, and M. Scheffler, "Vise: A Python library for VASP input generation", *J. Open Source Softw.* (2024)
