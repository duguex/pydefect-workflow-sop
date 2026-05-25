# Complex Defect Generation with pydefect-complex

Reference for the `pydefect-complex` package: systematic generation of multi-component defect structures compatible with pydefect's post-processing pipeline.

## Package Location

```
~/pydefect-complex/    (v0.2.0+, PLAN-C graph-based Apriori enumeration)
├── src/pydefect_complex/
│   ├── __init__.py      # ComplexDefectMaker, ComplexDefect, ComplexDefectEntry
│   ├── core.py          # ComplexDefect — composition of N SimpleDefect objects
│   ├── graph.py         # HostGraph (crystal site registry) + ComplexDefectGraph (geometry-only)
│   ├── enumerate.py     # Apriori enumeration + wyckoff composition matching + structure generation
│   ├── structure.py     # ComplexDefectEntry + orientation counting + point group classification
│   ├── symmetry.py      # Cross-composition geometric deduplication
│   └── io.py            # pydefect-compatible POSCAR + YAML output
├── tests/
│   ├── test_core.py     # 13 tests
│   ├── test_maker.py    # 16 tests (incl. charges, N=3, performance)
│   └── validate.py      # Structure validation script
└── diamond_example/     # Example diamond defect generation results
```

**Two-stage API (v0.2.0+)**:
```python
maker = ComplexDefectMaker(supercell_info, dopants=['N','B'], charges=[0])
geoms = maker.make_all_pairs()           # → list[ComplexDefectGraph] (geometry only)
entries = maker.generate_entries(n=2)    # → list[ComplexDefectEntry] (with structures)
maker.write(entries, 'defect', merge=True)
```

**Physical filtering**:
```python
# Remove defects with no symmetry (C1 = trivial group)
entries = [e for e in entries if e.point_group != 'C1']
# Limit impurity atoms per complex
entries = [e for e in entries if sum(1 for a in e.complex_defect.in_elements if a) <= 2]
```

## pydefect Naming Conventions (Important!)

These were discovered through testing and differ from common assumptions:

| Assumption | Reality |
|-----------|---------|
| SimpleDefect("v", "C_1", None, charges) | SimpleDefect(None, "C1", charges) → name="Va_C1" |
| out_atom = "C_1" (with underscore) | out_atom = "C1" (element+index, no underscore) |
| Vacancy name "v_C_1" | Vacancy name "Va_C1" |
| Charges = list | Charges = tuple |
| SimpleDefect takes 5 args | SimpleDefect(in_atom, out_atom, charge_list) — 3 args |

These conventions are inherited from pydefect's internal `SimpleDefect.__init__` and `DefectSetMaker`.

## Directory Naming for Mixed Compositions (Critical Pitfall!)

When `ComplexDefectMaker.write()` creates directories, the directory names are determined by
`ComplexDefect.name`, which sorts defects by `d.out_atom` in **reverse** order (`core.py:61`).

This means the directory name is NOT the same as the composition name you might expect:

| You write / think | Actual directory name |
|-------------------|----------------------|
| `Si_C1+B_C1` | `B_C1+Si_C1` |
| `O_C1+B_C1` | `B_C1+O_C1` |
| `Si_C1+O_C1` | `O_C1+Si_C1` |
| `Va_C1+Si_C1` | `Va_C1+Si_C1` |

**Before batch glob operations, ALWAYS run `ls -d *+*001_0/` to verify actual names.**

## Public Defect Datasets

For ML training on defect structure-property relationships:

### Directly Downloadable (Point Defects)

| Dataset | Content | Format | Link |
|---------|---------|--------|------|
| **QPOD** | 1900+ defect systems, 500 intrinsic defects in 82 2D materials. Properties: formation energy, charge transition levels, hyperfine coupling, zero-field splitting, transition dipole moments. GPAW PBE. | `qpod.db` (ASE DB) | https://2dhub.org/qpod/qpod.html |
| **2DMD** | ~14,866 defect structures in 6 2D materials (MoS₂, WSe₂, hBN, GaSe, InSe, BP). Properties: formation energy, band gap, HOMO/LUMO, magnetization. VASP PBE. | CSV + CIF (5 MB zip) | https://constructor.app/platform/open/2d-materials-point-defects/ |
| **IMP2D** | >17,500 single impurity defects (interstitial + adsorption) in 53 2D hosts. Properties: formation energy, total energy, magnetic moment. VASP PBE, spin-polarized. | `imp2d.db` (ASE DB) | https://cmr.fysik.dtu.dk/imp2d/imp2d.html |

### PES / Force Field Training

| Dataset | Content | Link |
|---------|---------|------|
| **MatPES** | ~400k structures from 300K MD, PBE + r²SCAN. First high-fidelity r²SCAN PES dataset. | https://matpes.ai |
| **MPtrj** | 1.58M structures (energy, forces, stress, magmom) from Materials Project relaxations. Used to train CHGNet, M3GNet, MACE-MP-0, SevenNet. | https://figshare.com/articles/dataset/23713842 |
| **LeMat-Traj** | 113M+ atomic configurations from MP + Alexandria + OQMD. | https://huggingface.co/datasets/LeMaterial/LeMat-Traj |

### Complex Defect Generation

| Tool | Description |
|------|-------------|
| **DeFecT-FF** (Purdue, 2025/2026) | Only systematic complex defect generator for bulk materials. Cd/Zn-Te/Se/S zincblende. HSE06-level MLFF, >10,000× faster than DFT. nanoHUB public tool. arXiv:2510.23514. |
| **pydefect-complex** (our tool) | Systematic 2-body complex defect generation for any material with pydefect supercell_info. Symmetry-aware site enumeration + distance filtering. |

> ⚠️ **Key gap**: No public large-scale database of complex defects (clusters, pairs) exists for 3D bulk materials. 2D datasets (QPOD, 2DMD, IMP2D) are all single point defects.

## Charge State Selection for Complex Defects

### Default behavior

`ComplexDefect._estimate_charges()` returns `[0]` (neutral only) for all complex defects. The charge states of component `SimpleDefect` objects are NOT propagated — a divacancy Va_C1+Va_C2 defaults to neutral regardless of the individual vacancies' charge ranges.

### Gap-state-driven approach

From a systematic study of Ca interstitial clusters in diamond: charge state selection is electronic-structure-driven, not chemical-intuition-driven.

```
有 gap states 吗？
  ├── occupied + empty gap states → q = -2, -1, 0, +1, +2
  ├── only occupied gap states    → q = 0, +1, +2   (can only donate electrons)
  ├── only empty gap states       → q = -2, -1, 0   (can only accept electrons)
  └── no gap states               → q = 0            (electronically inert)
```

Key findings: Ca₃ and Ca₄ interstitial clusters are gap-state-free due to sp³ reconstruction — electronic inertness is non-monotonic with cluster size. Same physics applies to vacancy clusters in diamond and other covalent systems.

### Practical recommendations

**Vacancy clusters in diamond/Si/SiC**: Almost always introduce gap states (dangling bonds). Use a ±1~2 charge range via the `charges` parameter:

```python
maker = ComplexDefectMaker(
    supercell_info, dopants=["N", "B"],
    charges=[-2, -1, 0, 1, 2],  # override neutral-only default
)
entries = maker.generate_entries(n=2)
```

**Interstitial clusters**: May be electronically inert at certain sizes. Start with neutral-only, then check PBE electronic structure for gap states before expanding charge range.

**Future**: A two-phase approach (PBE single-point scan → detect gap states → determine charge range) would automate this, but requires VASP integration beyond current scope.