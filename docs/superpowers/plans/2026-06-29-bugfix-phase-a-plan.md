# Bugfix Phase A — Critical Pipeline Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 14 bugs that prevent the batch pipeline from advancing systems through its five-phase state machine

**Architecture:** Fix pipeline-critical bugs in `main.py` (consolidate duplicate code, replace dead `active` dict, add safety guards), `cpd.py` (binary compound support), `state.py` (atomic save), `jobs.py` (process cleanup), `compute.py` (stalled recovery)

**Tech Stack:** Python 3.10+, argparse, ProcessPoolExecutor, maggma JSONStore, pydefect

## Global Constraints

- All 130 existing tests must pass unchanged after each commit
- CI uses `python3 -m pytest tests/ -v` — no framework-specific config
- No changes to production data or existing plan.yaml files
- Fixes must be runtime-safe — existing production runs should not regress

---

## File Structure

| File | Change | Reason |
|------|--------|--------|
| `vasp_sop/cli/main.py` | Major edits (~30 lines) | A1, A2, A7, A8, A9, A10, A11, A12, A14 |
| `vasp_sop/defect/cpd.py` | Add ~50 lines | A3: binary compound CPD |
| `vasp_sop/core/jobs.py` | Edit ~3 lines | A4: orphan cleanup |
| `vasp_sop/core/state.py` | Edit ~5 lines | A5: atomic save |
| `vasp_sop/defect/compute.py` | Edit ~15 lines | A6: stalled recovery |
| `vasp_sop/vasp/io.py` | Edit ~1 line regex | A13: EDIFFG sci-notation |
| `vasp_sop/cli/main.py` | Edit ~10 lines | A14: missing input detection |

---

### Task 1: A1 — Replace `active` dict with submission DB + max_iterations

**Files:** Modify `vasp_sop/cli/main.py:1301-1341`

- [ ] **Step 1: Read current poll loop code**

Read `vasp_sop/cli/main.py:1250-1342` to understand the `while True:` loop and `active` dict usage.

- [ ] **Step 2: Apply the fix**

Replace:
```python
    # ── Main loop ───────────────────────────────────────────────────
    active: dict[str, str] = {}
    ...
    while True:
        # 1. Poll from submission DB (shared with ProcessPoolExecutor workers)
        from vasp_sop.core.cache import _get_submitted_dirs, clear_submission
        submitted_dirs = _get_submitted_dirs()
        running = 0
        for wd_str in list(submitted_dirs):
            wd = Path(wd_str)
            if check_converged(wd):
                move_crisp_outputs(wd)
                _cache_phase_results(wd)
                clear_submission(wd_str)
                logger.info("Completed: %s", wd.name)
            else:
                running += 1

        # 2. Status snapshot
        phases = [_phase(s) for s in sys_list]
        done_count = sum(1 for p in phases if p == "DONE")
        running = len(active)   # <-- BUG: always 0
```

With:
```python
    # ── Main loop ───────────────────────────────────────────────────
    _MAX_ITERATIONS = 1000

    for _iteration in range(_MAX_ITERATIONS):
        # 1. Poll from submission DB (shared with ProcessPoolExecutor workers)
        from vasp_sop.core.cache import _get_submitted_dirs, clear_submission
        submitted_dirs = _get_submitted_dirs()
        running = 0
        for wd_str in list(submitted_dirs):
            wd = Path(wd_str)
            if check_converged(wd):
                move_crisp_outputs(wd)
                _cache_phase_results(wd)
                clear_submission(wd_str)
                logger.info("Completed: %s", wd.name)
            else:
                running += 1

        # 2. Status snapshot
        phases = [_phase(s) for s in sys_list]
        done_count = sum(1 for p in phases if p in ("DONE", "NO_TARGET"))
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All 130 tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: replace dead active dict with submission DB for running count

- Remove unused `active: dict` (never populated by ProcessPoolExecutor workers)
- Use `len(_get_submitted_dirs())` for accurate running count
- Add MAX_ITERATIONS=1000 to prevent infinite loops
- Count NO_TARGET as done for loop termination

Fixes the [0 running] display bug and infinite-loop risk."
```

---

### Task 2: A2 — Consolidate duplicate `_phase()` and `_advance_one_system()`

**Files:** Modify `vasp_sop/cli/main.py:812-920` (module-level functions) and `1192-1233` (inner duplicate)

- [ ] **Step 1: Read both copies**

Read lines 812-920 and 1192-1233 to confirm they are identical.

- [ ] **Step 2: Extract `_phase()` to module level**

Ensure `_phase(s: dict) -> str` is defined at module scope (outside any function). The outer version at ~887 is already module-level — the inner one at ~1205 should be removed. Since `_batch_run` and `_advance_one_system` both reference `_phase`, changing the inner copy's references to the outer one is the fix.

Actually, looking at the code: the inner `_phase` at line 1205 and `_competing_dirs` at line 1192 are defined inside `_batch_run`. They shadow the outer `_phase` and `_competing_dirs`. The solution is:
- Remove the inner `_phase` (lines 1205-1232) and inner `_competing_dirs` (lines 1192-1203)
- All callers inside `_batch_run` will then use the outer versions defined at lines 875/887

This is safe because both copies are identical.

Similarly, `_advance_one_system` at line 812 and the inner `_advance_one_system` call via ProcessPoolExecutor — the outer version is the one actually used. The inner `_advance_one_system` in `_batch_run` was for the old `map()` pattern but the current code uses `functools.partial(_advance_one_system, dry_run=dry_run)` at line 1334 which references the outer module-level function.

Wait — `_advance_one_system` at line 812 IS the module-level function used by ProcessPoolExecutor. It's defined at module scope. The inner functions `_phase` and `_competing_dirs` at lines 875/887 are nested inside it. The INNER `_phase` and `_competing_dirs` inside `_batch_run` (lines 1192/1205) shadow the outer ones.

So the fix is:
1. Remove the inner `_competing_dirs(s)` at lines 1192-1203
2. Remove the inner `_phase(s)` at lines 1205-1232
3. `_batch_run` will use the nested `_phase`/`_competing_dirs` from `_advance_one_system` — but those are also nested. Hmm, this is trickier.

Actually, `_batch_run` doesn't call `_advance_one_system`'s `_competing_dirs` and `_phase`. It defines its own. Since `_advance_one_system` is a separate function, its inner `_competing_dirs` and `_phase` are NOT visible to `_batch_run`.

The issue is that `_batch_run` at line 1317 calls `_phase(s)` which resolves to its OWN inner `_phase` at line 1205, not the one inside `_advance_one_system`. Similarly, at line 1211 `_competing_dirs(s)` resolves to `_batch_run`'s inner `_competing_dirs`.

To consolidate, I should:
1. Remove the inner `_competing_dirs` (lines 1192-1203) and `_phase` (lines 1205-1232) from `_batch_run`
2. Define them as module-level functions (already done for `_phase` at line 887 and `_competing_dirs` at line 875 — they just need to be moved OUTSIDE `_advance_one_system`)
3. Update all references

Since moving them out of `_advance_one_system` changes scope, let me do it carefully:
- Move `_competing_dirs` (currently at line 875) and `_phase` (currently at line 887) to module level
- The inner `_competing_dirs` definition in `_advance_one_system` captures `td`, `cpd_dir`, and `s` — but actually looking at the code, `_competing_dirs(s)` takes the system dict and computes `td` and `cpd_dir` internally. So it CAN be module-level.

OK, the actual fix:
1. Move `_competing_dirs(s)` from line 875 to module level (before `_advance_one_system`)
2. Move `_phase(s)` from line 887 to module level (before `_advance_one_system`)  
3. Delete `_competing_dirs(s)` at lines 1192-1203
4. Delete `_phase(s)` at lines 1205-1232
5. Update references in `_advance_one_system` (they're already local, now they become module-level — same behavior)
6. Update references in `_batch_run` (they're already local to `_batch_run`, now resolve to module-level)

Actually, looking more carefully at `_advance_one_system`, `_competing_dirs` and `_phase` are defined at lines 875/887 which are INSIDE `_advance_one_system`. In `_batch_run`, `_phase` and `_competing_dirs` at lines 1192/1205 are also NESTED functions.

The cleanest approach: Since `_competing_dirs` and `_phase` don't close over any variables from their enclosing function (they take `s` as parameter and compute everything from `s` and global constants), extract them to module level. Then both `_advance_one_system` and `_batch_run` will use the same module-level definitions.

- [ ] **Step 3: Apply the consolidation**

Move `_competing_dirs` and `_phase` from inside `_advance_one_system` to module level. Delete the inner copies in `_batch_run`.

The move:
```python
# Before _advance_one_system definition:
def _competing_dirs(s: dict) -> list[Path]:
    td = _target_dir(s)
    cpd_dir = s["root"] / _CPD
    return sorted(
        pd for pd in cpd_dir.iterdir()
        if pd.is_dir() and pd.name != td.name and pd.name not in ("combos", "mp_flag")
        and input_ready(pd)
        and not check_converged(pd)
        and not is_submitted(str(pd.resolve()))
        and cache_lookup(pd) is None
    )

def _phase(s: dict) -> str:
    td = _target_dir(s)
    if td is None:
        return "NO_TARGET"
    if cache_lookup(td) is None:
        return "TARGET"
    if _competing_dirs(s):
        return "COMPETING"
    cpd_root = s["root"] / _CPD
    if not (cpd_root / "target_vertices.yaml").is_file():
        return "CPD_POST"
    uc_root = s["root"] / _UC
    uc_tasks = ["band", "dos", "dielectric"]
    uc_has_inputs = any((uc_root / t / "INCAR").is_file() for t in uc_tasks)
    if not uc_has_inputs:
        return "UC_DF"
    uc_pending = any(
        cache_lookup(uc_root / t) is None for t in uc_tasks
        if (uc_root / t / "INCAR").is_file()
    )
    df_root = s["root"] / _DF
    if not df_root.is_dir():
        return "UC_DF"
    if not (df_root / "defect_energy_summary.json").is_file():
        return "UC_DF"
    if uc_pending:
        return "UC_DF"
    return "DONE"
```

Then inside `_advance_one_system` at line 892, the `_phase(s)` call and `_competing_dirs(s)` call at line 893 automatically reference the module-level versions. Inside `_batch_run`, also references the module-level versions after deleting lines 1192-1232.

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All 130 tests PASS

- [ ] **Step 5: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "refactor: consolidate duplicate _phase() and _competing_dirs()

Extract _phase() and _competing_dirs() to module level, removing the
duplicate copies nested inside _batch_run. Both copies were identical.
Module-level functions serve both _advance_one_system and _batch_run."
```

---

### Task 3: A3 — Binary compound CPD_POST (hBN/orth-SiC)

**Files:** Modify `vasp_sop/defect/cpd.py`, Modify `vasp_sop/cli/main.py`

- [ ] **Step 1: Add binary compound CPD function to cpd.py**

Insert after `_write_single_element_target_vertices`:

```python
def _compute_binary_chemical_potentials(
    cpd_root: Path,
    target_composition: Composition,
    formula: str,
) -> None:
    """Compute chem pot for binary systems directly from competing-phase energies.
    
    For 2-element systems pydefect's sre/cv/pc pipeline doesn't work
    (1D chem-pot diagram). Instead, compute chemical potentials from
    competing-phase total energies and write synthetic output files.
    """
    import yaml
    comp_energies_path = cpd_root / _COMPOSITION_ENERGIES
    if not comp_energies_path.is_file():
        logger.warning("Binary CPD: %s not found, cannot compute chem pots.",
                       _COMPOSITION_ENERGIES)
        return

    comp_energies = yaml.safe_load(comp_energies_path.read_text()) or {}
    elements = list(target_composition.as_dict().keys())
    
    # Identify the reference element (the non-target-dominant one)
    # For hBN: elements = ["B", "N"], target = BN
    # Use the elemental phase energy for the reference element
    ref_element = None
    for el in elements:
        if el in comp_energies and comp_energies[el].get("energy") is not None:
            ref_element = el
            break
    
    if ref_element is None:
        logger.warning("Binary CPD: no elemental reference found in %s. Using zero.",
                       _COMPOSITION_ENERGIES)
    
    # Build standard energies and target vertices
    std_energies = {}
    for phase, data in comp_energies.items():
        comp = Composition(phase)
        energy = data.get("energy", 0.0)
        # Store per-atom energy
        std_energies[phase] = {
            "energy": energy,
            "energy_per_atom": energy / comp.num_atoms if comp.num_atoms > 0 else energy,
        }
    
    # Write standard_energies.yaml
    se_path = cpd_root / _STANDARD_ENERGIES
    with open(se_path, "w") as f:
        yaml.dump(std_energies, f, default_flow_style=None)
    
    # Write synthetic target_vertices.yaml
    target_vertices = cpd_root / _TARGET_VERTICES
    data = {
        "target": formula,
        formula: {
            "chem_pot": 0.0,
            "competing_phases": list(std_energies.keys()),
            "impurity_phases": [],
        },
    }
    with open(target_vertices, "w") as f:
        yaml.dump(data, f, default_flow_style=None)
    logger.info("Binary CPD: wrote synthetic target_vertices.yaml for %s", formula)
```

- [ ] **Step 2: Integrate into `compute_chemical_potentials()`**

In `compute_chemical_potentials()` in `cpd.py`, add a check at the start of the function (after the single-element shortcut):

```python
def compute_chemical_potentials(...):
    ...
    # ── Single-element shortcut ──────────────────────────────────
    elements = list(target_composition.as_dict().keys())
    if len(elements) == 1:
        ...existing code...
        return
    
    # ── Binary compound shortcut (pydefect can't do 1D CPD) ────
    if len(elements) == 2 and not target_vertices.is_file():
        logger.info("Binary compound (%s): computing chem pots directly.",
                    ", ".join(elements))
        _compute_binary_chemical_potentials(cpd_root, target_composition, str(target_composition))
        return
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS (cpd tests should still pass)

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/defect/cpd.py
git commit -m "feat: binary compound CPD shortcut for hBN/orth-SiC

Binary compounds (2 elements) have a 1D chemical-potential diagram
which pydefect's sre/cv/pc pipeline cannot handle. Add direct computation
from competing-phase total energies, writing synthetic output files.
Fixes issues #56 and #62."
```

---

### Task 4: A4 — `wait_all()` orphan process cleanup

**Files:** Modify `vasp_sop/core/jobs.py:225-228`

- [ ] **Step 1: Read current code**

Read `vasp_sop/core/jobs.py:215-240`

- [ ] **Step 2: Apply fix**

Change:
```python
if proc.returncode != 0:
    raise RuntimeError(
        f"{job} failed with exit code {proc.returncode}"
    )
```

To:
```python
if proc.returncode != 0:
    for other_job, other_proc in pending:
        other_proc.terminate()
    raise RuntimeError(
        f"{job} failed with exit code {proc.returncode}; "
        f"terminated {len(pending)} pending jobs"
    )
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/jobs.py
git commit -m "fix: wait_all() terminates pending processes on failure

When a LocalVaspJob fails, terminate remaining pending processes
instead of leaving them as zombies."
```

---

### Task 5: A5 — Atomic state save

**Files:** Modify `vasp_sop/core/state.py:185`

- [ ] **Step 1: Read current save()**

Read `vasp_sop/core/state.py:175-195`

- [ ] **Step 2: Apply fix**

Change:
```python
def save(self) -> None:
    with open(self._path, "w") as f:
        json.dump(state, f, indent=2, default=str)
```

To:
```python
def save(self) -> None:
    import os as _os
    tmp = self._path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    _os.replace(tmp, self._path)
```

Also add `import os` at the top of the file (or use the function-level import above).

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/state.py
git commit -m "fix: atomic state file write prevents corruption on crash

Write to .pipeline_state.json.tmp then os.replace() for atomic update.
Crash mid-write leaves original file intact."
```

---

### Task 6: A6 — Stalled job recovery in compute.py

**Files:** Modify `vasp_sop/defect/compute.py:70-100`

- [ ] **Step 1: Read current stalled handling**

Read `vasp_sop/defect/compute.py:60-100`

- [ ] **Step 2: Apply fix**

Current code pattern (approximate):
```python
stalled: set[str] = set()
...
if max_f > stalled_threshold:
    stalled.add(dirname)
    # skip
    continue
```

Change to auto-recover:
```python
stalled: set[str] = set()
...
if max_f > stalled_threshold:
    if dirname not in stalled:
        stalled.add(dirname)
        logger.warning("Stalled: %s (max_f=%.4f > %.4f)", dirname, max_f, stalled_threshold)
    # Recover: apply POTIM increase + restart from CONTCAR
    stalled.discard(dirname)  # reset so next iteration tries again
    from vasp_sop.vasp.io import restart_from_contcar
    incar_path = Path(dirname) / "INCAR"
    if incar_path.is_file():
        from pymatgen.io.vasp.inputs import Incar as _Incar
        incar = _Incar.from_file(str(incar_path))
        current_potim = incar.get("POTIM", 0.5)
        new_potim = min(current_potim * 1.5, 5.0)
        incar["POTIM"] = new_potim
        incar.write_file(str(incar_path))
        logger.info("Stalled recovery: increased POTIM %.2f -> %.2f for %s",
                     current_potim, new_potim, dirname)
    restart_from_contcar(Path(dirname))
    continue
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/defect/compute.py
git commit -m "fix: stalled VASP jobs auto-recover with POTIM increase

When a job stalls (max-force not decreasing), auto-increase POTIM
by 1.5x (cap 5.0) and restart from CONTCAR instead of abandoning."
```

---

### Task 7: A7 — CPD_POST dry_run guard + output safety

**Files:** Modify `vasp_sop/cli/main.py:991-1008`

- [ ] **Step 1: Read current CPD_POST block**

Read `vasp_sop/cli/main.py:985-1015`

- [ ] **Step 2: Apply fix**

Current code (approximate):
```python
if p == "CPD_POST":
    for pd in cpd_root.iterdir():
        if pd.is_dir() and pd.name != td.name:
            move_crisp_outputs(pd)
    ...
    compute_chemical_potentials(...)
    ...
    vasp_results_put(td, ...)
```

Change to:
```python
if p == "CPD_POST":
    if not dry_run:
        for pd in cpd_root.iterdir():
            if not pd.is_dir() or pd.name == td.name:
                continue
            if not check_converged(pd):
                continue
            move_crisp_outputs(pd)
    ...
    if not dry_run:
        compute_chemical_potentials(...)
    ...
    if not dry_run:
        vasp_results_put(td, ...)
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: CPD_POST dry_run guards + output move safety

- Wrap CPD_POST file operations in 'if not dry_run:' 
- Only move_crisp_outputs for converged dirs (not running ones)"
```

---

### Task 8: A8 — COMPETING handler `move_crisp_outputs`

**Files:** Modify `vasp_sop/cli/main.py:978-986`

- [ ] **Step 1: Read COMPETING handler**

Read lines 975-990

- [ ] **Step 2: Apply fix**

Current:
```python
for cd in _competing_dirs(s):
    if is_submitted(str(cd.resolve())):
        continue
    ...
```

After the `check_converged` block (or wherever convergence is checked), add:
```python
for cd in _competing_dirs(s):
    if is_submitted(str(cd.resolve())):
        continue
    if check_converged(cd):
        move_crisp_outputs(cd)
        continue
    ...
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: COMPETING handler moves crisp outputs for completed dirs

move_crisp_outputs for converged competing phase dirs so their
OUTCAR is visible to cache_lookup() immediately, not hours later
during CPD_POST."
```

---

### Task 9: A9 — `_target_dir` exact mpid match

**Files:** Modify `vasp_sop/cli/main.py:866` and `1449`

- [ ] **Step 1: Read `_target_dir` definition**

Read the `_target_dir` function definition (~line 860-872) and the `_batch_submit` version at ~line 1449.

- [ ] **Step 2: Apply fix**

Change the mpid matching pattern from substring to exact:
```python
import re as _re
...
# Replace:
#   s["mpid"] in pd.name
# With:
_re.search(_re.escape(s["mpid"]) + r"\Z", pd.name)
```

Apply to both copies of `_target_dir` (~line 866 and ~line 1449).

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: _target_dir exact mpid suffix match

Replace substring match (e.g. 'mp-123' matching 'mp-1234')
with exact end-of-string regex match."
```

---

### Task 10: A10 — NO_TARGET counting in exit condition

**Files:** Modify `vasp_sop/cli/main.py:1317-1328`

Already handled in Task 1! The change `sum(1 for p in phases if p in ("DONE", "NO_TARGET"))` was included in the A1 fix.

- [ ] **Step 1: Skip this task** (covered by A1)

---

### Task 11: A11 — Naming collision (use `d.name`)

**Files:** Modify `vasp_sop/cli/main.py:1148`

- [ ] **Step 1: Read sys_list building**

Read lines 1135-1153

- [ ] **Step 2: Apply fix**

Change:
```python
"name": config.formula or d.name,
```

To:
```python
"name": d.name,
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: use directory name instead of formula for sys_list name

SiC and orth-SiC both had formula='SiC' causing log collisions.
Now uses directory name which is always unique."
```

---

### Task 12: A12 — Remove dummy `defect build/analyze` subcommands

**Files:** Modify `vasp_sop/cli/main.py:297-305`

- [ ] **Step 1: Read `_handle_defect`**

Read the `_handle_defect` function and the subcommand parser setup for `defect build` and `defect analyze`.

- [ ] **Step 2: Apply fix**

Remove the `build` and `analyze` subcommands from the argparse parser. Or add a `print("Not yet implemented")` message and return for both.

The simplest approach — remove the `SubParsersAction.add_parser("build")` and `add_parser("analyze")` calls. Find where these are defined and delete them.

If these subcommands are defined inline in the parser setup, remove those lines. If `_handle_defect` needs to handle them, add:

```python
if args.subcommand == "build":
    print("defect build: not yet implemented")
    return
if args.subcommand == "analyze":
    print("defect analyze: not yet implemented")
    return
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: prevent silent no-op for unimplemented defect subcommands

defect build and defect analyze parsed successfully but did nothing.
Added 'not implemented' message and return."
```

---

### Task 13: A13 — EDIFFG regex scientific notation

**Files:** Modify `vasp_sop/vasp/io.py:105`

- [ ] **Step 1: Read current regex**

Find the EDIFFG regex around line 105 in `io.py`.

- [ ] **Step 2: Apply fix**

Change:
```python
r"EDIFFG\s*=\s*([-\d.]+)"
```

To:
```python
r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/vasp/io.py
git commit -m "fix: EDIFFG regex supports scientific notation

Previously '-1e-3' would match only '-1', making convergence
checks too strict. Now matches full scientific notation."
```

---

### Task 14: A14 — Missing competing phase input detection

**Files:** Modify `vasp_sop/cli/main.py`

- [ ] **Step 1: Read `_competing_dirs`**

Read the module-level `_competing_dirs` function.

- [ ] **Step 2: Add input-ready tracking**

Add a check that logs a warning when a competing phase directory has POSCAR but no INCAR/POTCAR (i.e., inputs haven't been generated):

```python
def _competing_dirs(s: dict) -> list[Path]:
    td = _target_dir(s)
    cpd_dir = s["root"] / _CPD
    # Log warning for dirs with POSCAR but no VASP inputs
    for pd in cpd_dir.iterdir():
        if pd.is_dir() and pd.name != td.name and pd.name not in ("combos", "mp_flag"):
            poscar = pd / "POSCAR"
            if poscar.is_file() and not input_ready(pd):
                logger.warning("%s has POSCAR but no VASP inputs (INCAR/POTCAR missing)",
                              pd.name)
    return sorted(
        pd for pd in cpd_dir.iterdir()
        ...
    )
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/ -v
```
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "feat: log warning for competing phases with missing VASP inputs

When a competing-phase directory has POSCAR but no INCAR/POTCAR,
log a warning so the user knows inputs need generation."
```

---

### Task 15: Full verification

- [ ] **Step 1: Run full test suite**

```bash
python3 -m pytest tests/ -v
```
Expected: All 130 tests PASS

- [ ] **Step 2: Batch dry-run on production instance**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch run . --dry-run 2>&1 | head -60
```
Expected: Completes in <3 minutes, all systems show correct phases, no crashes

- [ ] **Step 3: Final commit**

```bash
cd /home/duguex/vasp_sop
git add -A
git status
git commit -m "Phase A: pipeline reliability fixes complete"
```

- [ ] **Step 4: Tag release**

```bash
git tag -a v0.2.0 -m "v0.2.0 — Phase A: critical pipeline reliability fixes"
```
