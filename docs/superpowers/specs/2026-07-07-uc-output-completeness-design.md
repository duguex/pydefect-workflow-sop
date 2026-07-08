# Auto-repair: Re-submit UC VASP When Output Files Missing

> Date: 2026-07-07
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Status: Design

## Background

Production data-integrity scan against `2025_undergo_spin_defect` found **hBN** marked DONE but missing `unitcell/unitcell.yaml`. Root cause: `band/vasprun.xml` was never collected from the cluster VASP output. The UC_DF submission loop treats a UC task as "complete" based solely on OUTCAR convergence (`check_converged()`). Since hBN's band OUTCAR is converged, the task isn't re-submitted. Later, `build_unitcell_yaml()` calls `pydefect_vasp u -vb band/vasprun.xml`, fails with `FileNotFoundError`, and silently continues without generating `unitcell.yaml`.

## Scope

Add output-completeness detection to the UC_DF VASP submission loop. When a UC task (band/dos) has a converged OUTCAR but is missing `vasprun.xml`, treat it as incomplete and re-submit via crisp.

**One function, one call-site change. No new dependencies.**

## Design

### `check_task_complete(path: Path, task_type: str = "") -> bool`

Add to `vasp_sop/vasp/io.py`:

```python
_REQUIRED_UC_OUTPUTS: dict[str, list[str]] = {
    "band":       ["OUTCAR", "vasprun.xml"],
    "dos":        ["OUTCAR", "vasprun.xml"],
    "dielectric": ["OUTCAR"],
}


def check_task_complete(path: Path, task_type: str = "") -> bool:
    """Check whether a VASP task's output artifacts are fully present.

    For band/dos tasks: requires converged OUTCAR + vasprun.xml.
    For dielectric:     requires converged OUTCAR only.
    For any other task: delegates to check_converged().
    """
    if not check_converged(path):
        return False
    if task_type in _REQUIRED_UC_OUTPUTS:
        return all((path / f).is_file() for f in _REQUIRED_UC_OUTPUTS[task_type])
    return True
```

### Call-site change in `_advance_one_system()` (UC_DF phase)

In `vasp_sop/cli/main.py`, replace:

```python
# line 1178
if check_converged(task_dir):
    continue
```

with:

```python
from vasp_sop.vasp.io import check_task_complete
if check_task_complete(task_dir, task):
    continue
```

This is inside the UC task loop (`for task in ("band", "dos", "dielectric"):`). When `task` is `"band"` or `"dos"`, `check_task_complete` checks for both converged OUTCAR and vasprun.xml. When `task` is `"dielectric"`, it checks only OUTCAR (matching current behavior).

### No change to defect-task loop

Defect tasks don't need vasprun.xml. The defect loop stays as-is with `check_converged()`.

### Entry points affected

- `_advance_one_system()` — UC_DF phase submission loop
- `_batch_submit()` — if it also uses `check_converged` for UC tasks (verify, but likely not needed)

### Error handling

- If vasprun.xml is missing AND OUTCAR is unconverged: task is submitted normally (current behavior)
- If vasprun.xml is present but OUTCAR is unconverged: task is submitted (current behavior — OUTCAR must converge)
- If both are present: task is skipped (current behavior)
- If neither exists: task is submitted (current behavior)

### Testing

- New unit test for `check_task_complete`: converged OUTCAR + vasprun.xml → True; converged OUTCAR alone → False for band, True for dielectric
- Update walkthrough test: in UC_DF phase, verify tasks without vasprun.xml are submitted despite converged OUTCAR
- Production test `test_unitcell_yaml_structure` now passes for hBN (after re-submit + regeneration)

## Files changed

| File | Change |
|---|---|
| `vasp_sop/vasp/io.py` | Add `check_task_complete()` + `_REQUIRED_UC_OUTPUTS` |
| `vasp_sop/cli/main.py` | One line: replace `check_converged(task_dir)` with `check_task_complete(task_dir, task)` in UC_DF loop |
| `tests/test_io.py` or `tests/test_cli.py` | Add `check_task_complete` tests + update walkthrough |
