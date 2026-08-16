---
name: vasp-poscar-species-regroup-after-substitution
description: Validate and regroup POSCAR species blocks after pymatgen substitutions so they match concatenated POTCAR order before VASP submission.
---

# Canonical substitution POSCAR validation

Use when constructing a VASP control by replacing species directly in a pymatgen `Structure` while reusing a fixed POTCAR.

## Procedure

1. Start from one immutable canonical `Structure`.
2. Apply `Structure.replace(index, element)` for each substitution.
3. Before writing POSCAR, regroup sites in the exact POTCAR order:

```python
ordered = [site for el in ["Y", "Ti", "O"] for site in structure if str(site.specie) == el]
grouped = Structure.from_sites(ordered)
grouped.to(filename="POSCAR", fmt="poscar")
```

4. Read POSCAR back and verify:
   - species line exactly matches POTCAR segment order;
   - counts equal the intended composition;
   - number of species equals number of concatenated POTCAR datasets;
   - lattice is unchanged;
   - anonymous site assignment against canonical perfect has RMS/max 0 for substitution-only controls.
5. Verify charged controls use `NELECT = neutral_valence - charge` from actual POTCAR ZVAL values.
6. Submit with cache/prefill disabled when POSCAR identity is part of the experiment.

## Failure signature

```text
POSCAR found type information on POSCAR Y TiY TiO
POSCAR found : 5 types
ERROR: number of potentials on File POTCAR incompatible ... 5 POTCAR: 3
```

Cause: pymatgen preserves original site order after replacement, so identical elements can appear in separated POSCAR blocks even though the logical composition has only three elements.
