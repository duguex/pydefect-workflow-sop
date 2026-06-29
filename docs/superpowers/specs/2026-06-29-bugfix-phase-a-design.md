# VASP SOP Bug Fix — Phase A: Critical Pipeline Reliability

> Date: 2026-06-29
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Production instance: `/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/`
> Status: Design — Phase A of 3-phase bug-fix campaign

## Background

vasp-sop v0.1.0 underwent a major cache redesign (content_hash as primary key, POTCAR fingerprinting, JSONStore dual-store) and Phase A production-readiness work (cache rebuild, plan.yaml generation for all 40 systems). During this work, a comprehensive code audit was performed, uncovering **30+ issues** across the codebase.

The project's design purpose is high-throughput VASP point-defect calculation automation: chemical formula → competing phases → chemical potential diagram → supercell → defects → VASP → formation energy analysis. This spec addresses the issues that prevent this pipeline from running correctly.

## Three-Phase Campaign

| Phase | Focus | Issues | Verification |
|-------|-------|--------|-------------|
| **A** | Critical pipeline reliability | 8 P0 + 11 P1 pipeline bugs | `pytest tests/ -v` passes; batch dry-run identifies correct phases |
| **B** | Cache correctness + edge cases | 1 P0 cache + 4 P1 cache + ~8 secondary | Cache queries return correct results; all systems cacheable |
| **C** | Test coverage + production dry-run | Test gaps + pymatgen deprecation | `pytest -W error` passes; full batch dry-run completes in <3 min |

**This document covers Phase A only.** Phases B and C have separate specs.

## Phase A Scope

Fix all bugs that prevent the batch pipeline from correctly advancing systems through the five-phase state machine (TARGET → COMPETING → CPD_POST → UC_DF → DONE).

### Design Decisions

#### ADR-A1: Consolidate Duplicate Code Paths

**Problem:** `_advance_one_system()` and `_phase()` are defined twice — once inside `_batch_run` (lines ~1192-1232) and once as standalone functions (lines ~875-914). The inner versions are identical copies. Two copies can independently drift and are a maintenance burden.

**Decision:** Extract one module-level `_phase(system_dict) -> str` and one module-level `_advance_one_system(system_dict, *, dry_run)` function. Delete the inner copies. The inner `_competing_dirs(s)` function (different from the module-level `_competing_dirs(path)` in cpd.py) stays inline — it's a short helper.

**Consequence:** Single source of truth. All callers (batch run, single-system pipeline, progress reporting) use the same logic.

#### ADR-A2: Filesystem Markers Over In-Memory State

**Problem:** ProcessPoolExecutor workers cannot share in-memory state (`active` dict). This causes:
- `[0 running]` display always shows 0 (line 1319: `running = len(active)` where `active` is never populated)
- Job completion detection was originally broken (fixed in 4fd5cf2 by switching to `_get_submitted_dirs()`)

**Decision:** Remove the `active` dict entirely. Use `len(_get_submitted_dirs())` for the running count. Add a `max_iterations` parameter (default 1000) to prevent infinite loops.

**Consequence:** Running count is accurate. Loop has a safety limit. No in-memory state to keep in sync.

#### ADR-A3: Binary Compound CPD Shortcut

**Problem:** hBN (B-N) and orth-SiC (Si-C) are binary compounds. pydefect's chemical-potential diagram pipeline (sre → cv → pc) expects ≥2 chemical-potential dimensions (a 2D diagram). Binary compounds have only 1 dimension (a line), causing pydefect failures:
- `pydefect sre` → `Element B does not exist in CompositionEnergies`
- `pydefect cv` → cannot solve convex hull in 1D
- `pydefect pc` → cannot plot

Both systems loop forever: CPD_POST → fail → CPD_POST → fail.

**Decision:** Add a `_compute_binary_chemical_potentials()` function in `cpd.py` that:
1. Detects ≤2 unique elements after excluding common gases (O, N, F, Cl, H)
2. Computes chemical potentials directly from competing-phase total energies using the formula: μ_A = (E_total(A_xB_y) - y * μ_B) / x
3. Writes synthetic `target_vertices.yaml`, `standard_energies.yaml`, and `chem_pot_diag.json`
4. For the reference element (the non-target one), use the elemental-phase energy

**Consequence:** Binary compounds skip pydefect's CPD pipeline and get chemical potentials computed directly. No infinite loop.

#### ADR-A4: CPD_POST Dry-Run Safety

**Problem:** The CPD_POST block in `_advance_one_system` calls `move_crisp_outputs()` for ALL cpd dirs and `vasp_results_put()` for the target dir — even in `--dry-run` mode. This causes real filesystem mutations during a dry run.

**Decision:** Wrap the entire CPD_POST processing block (lines ~991-1008) in `if not dry_run:`.

Additionally, `move_crisp_outputs(pd)` should only be called for converged dirs within CPD_POST, not all dirs. A running competing-phase VASP job may have partial output in `output/` — moving it corrupts the in-progress calculation.

**Consequence:** Dry-run is truly read-only. Running CPD_POST doesn't disrupt in-progress competing-phase calculations.

#### ADR-A5: Stalled-Job Recovery

**Problem:** `compute.py:run_vasp()` marks stalled directories (consecutive restarts with no force reduction) as `stalled.add(dirname)`. Once stalled, the directory is skipped on every subsequent iteration of the restart loop — no fix is applied to INCAR, `restart_from_contcar` is never called, and the job remains unconverged permanently.

**Decision:** When a job is detected as stalled:
1. Apply the recommended fix from `errors.py` to INCAR (increase POTIM by 1.5x, cap at 5.0)
2. Reset the stalled flag for that directory
3. Restart from CONTCAR with the modified INCAR
4. Log the stall event and the fix applied

**Consequence:** Stalled jobs auto-recover. No manual intervention needed.

#### ADR-A6: Non-Atomic State Save

**Problem:** `StateStore.save()` writes directly to `.pipeline_state.json`. A process crash mid-write produces a truncated JSON file. On the next pipeline run, `json.load()` raises, discarding all prior pipeline state.

**Decision:** Write to `.pipeline_state.json.tmp`, then `os.replace(tmp, target)` for atomic rename. On Unix, `os.replace()` is an atomic filesystem operation.

**Consequence:** Crash-safe state persistence. Partial writes are invisible.

---

## Detailed Fix Descriptions

### Fix A1: `active` dict → submission DB (main.py:1319)

**Current:**
```python
running = len(active)  # active is always {} → always 0
```

**Fix:** Replace with the actual running count from the shared submission DB:
```python
running = len(_get_submitted_dirs())
```

Also add `max_iterations` to the main loop:
```python
MAX_ITERATIONS = 1000
for iteration in range(MAX_ITERATIONS):
    ...
else:
    logger.error("Batch run hit max iterations (%d), exiting.", MAX_ITERATIONS)
    break
```

**Files:** `vasp_sop/cli/main.py`

### Fix A2: Consolidate `_phase()` + `_advance_one_system()` (main.py:875-914, 1192-1232)

**Current:** Two copies of `_phase()` and `_advance_one_system()`.

**Fix:** Extract to module level. The module-level versions replace the inner copies. `_batch_run`'s `_submit_or_skip` stays as a nested function (it captures `dry_run`).

**Functions to extract:**
- `_system_phase(s: dict) -> str` — phase determination logic (replaces both `_phase` definitions)
- `_advance_system(s: dict, *, dry_run: bool = False) -> None` — per-system state machine (replaces `_advance_one_system`)

**Files:** `vasp_sop/cli/main.py`

### Fix A3: Binary compound CPD_POST (cpd.py + main.py)

**New function in `cpd.py`:**
```python
def compute_binary_chemical_potentials(
    cpd_root: Path,
    target_composition: Composition,
    formula: str,
) -> None:
    """Compute chemical potentials for binary systems directly from energies."""
```

**Logic:**
1. Parse `composition_energies.yaml` for all competing-phase total energies
2. Reference element: the non-target element in the binary compound
3. If the reference element has an elemental phase in the competing set, use its energy as μ=0 reference
4. Otherwise, compute μ from the reference-element-containing competing phase with the lowest energy per atom
5. Write `target_vertices.yaml`, `standard_energies.yaml`, `chem_pot_diag.json`

**Integration in `_advance_system()`:** Before the CPD_POST pydefect fallback, check `len(elements) <= 2` and call the binary shortcut.

**Files:** `vasp_sop/defect/cpd.py`, `vasp_sop/cli/main.py`

### Fix A4: `wait_all()` orphan process cleanup (jobs.py:225-228)

**Current:**
```python
if proc.returncode != 0:
    raise RuntimeError(f"{job} failed with exit code {proc.returncode}")
```

**Fix:** Before raising, terminate all pending processes:
```python
if proc.returncode != 0:
    for other_job, other_proc in pending:
        other_proc.terminate()
    raise RuntimeError(...)
```

**Files:** `vasp_sop/core/jobs.py`

### Fix A5: Atomic state save (state.py:185)

**Current:**
```python
with open(self._path, "w") as f:
    json.dump(state, f, indent=2, default=str)
```

**Fix:**
```python
tmp = self._path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(state, f, indent=2, default=str)
os.replace(tmp, self._path)
```

**Files:** `vasp_sop/core/state.py`

### Fix A6: Stalled job recovery (compute.py:83-95)

**Current:** Stalled directories are skipped and never recovered.

**Fix:** When a directory is detected as stalled:
1. Apply "increase POTIM" fix to INCAR
2. Remove from `stalled` set
3. Call `restart_from_contcar()`
4. Log stall + fix

**Files:** `vasp_sop/defect/compute.py`

### Fix A7: CPD_POST dry_run guard + output safety (main.py:991-1008)

**Current:**
```python
for pd in cpd_root.iterdir():
    if pd.is_dir() and pd.name != td.name:
        move_crisp_outputs(pd)
```

**Fix:**
```python
if not dry_run:
    for pd in cpd_root.iterdir():
        if not pd.is_dir() or pd.name == td.name:
            continue
        if not check_converged(pd):
            continue
        move_crisp_outputs(pd)
```

And wrap the entire CPD_POST block in `if not dry_run:`.

**Files:** `vasp_sop/cli/main.py`

### Fix A8: COMPETING handler `move_crisp_outputs` (main.py:978-986)

**Current:** COMPETING handler checks convergence but doesn't move output files from `output/` to root.

**Fix:** After checking `check_converged(cd)`, call `move_crisp_outputs(cd)` before moving to the next dir.

**Files:** `vasp_sop/cli/main.py`

### Fix A9: `_target_dir` exact mpid match (main.py:866)

**Current:**
```python
s["mpid"] in pd.name
```

**Fix:**
```python
import re
# Match exactly the mpid suffix (e.g., "mp-804" at end of dir name)
pattern = re.compile(re.escape(s["mpid"]) + r"\Z")
...
pattern.search(pd.name)
```

**Files:** `vasp_sop/cli/main.py`

### Fix A10: NO_TARGET counting (main.py:1317-1328)

**Current:** `_phase()` returns `"NO_TARGET"` for systems without MPID, but the exit condition only checks `DONE`.

**Fix:**
```python
done_count = sum(1 for p in phases if p in ("DONE", "NO_TARGET"))
```

**Files:** `vasp_sop/cli/main.py`

### Fix A11: Naming collision (main.py:1148)

**Current:**
```python
"name": config.formula or d.name,
```

**Fix:**
```python
"name": d.name,
```

**Files:** `vasp_sop/cli/main.py`

### Fix A12: `defect build`/`defect analyze` dispatch (main.py:297-305)

**Current:** The argparse parser defines `build` and `analyze` subcommands for `defect`, but `_handle_defect` doesn't dispatch them.

**Fix:** Either add the dispatch cases or remove the subcommands from the parser. Add dispatch:
```python
if args.subcommand == "build":
    ...
elif args.subcommand == "analyze":
    ...
```

Since these are not currently implemented, remove them from the argument parser to avoid silent no-ops.

**Files:** `vasp_sop/cli/main.py`

### Fix A13: EDIFFG regex scientific notation (io.py:105)

**Current:** `r"EDIFFG\\s*=\\s*([-\\d.]+)"` doesn't match `-1e-3` or `5E-2`.

**Fix:**
```python
r"EDIFFG\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
```

**Files:** `vasp_sop/vasp/io.py`

### Fix A14: Missing competing phases with no inputs (main.py:894)

**Current:** `_competing_dirs()` filters by `input_ready(pd)` — competing phase dirs without INCAR/POTCAR are invisible. Phase jumps to CPD_POST with incomplete data.

**Fix:** Track whether expected competing phases lack inputs. A phase that was fetched from MP and has POSCAR but no VASP inputs should be counted as pending, not silently skipped.

**Files:** `vasp_sop/cli/main.py`

---

## Verification

| Check | Method | Expected |
|-------|--------|----------|
| Tests pass | `python3 -m pytest tests/ -v` | All 130+ tests PASS |
| Dry-run no mutations | `vasp-sop batch run . --dry-run` | No files moved; no cache writes; no VASP submissions |
| Phase display | `vasp-sop batch run . --dry-run 2>&1 \| grep GaN` | `GaN: DONE` |
| Running count | Poll-loop output | `[N running]` shows actual crisp queue count |
| Atomic save | Kill process mid-save, restart | State loads successfully from tmp-free path |
| Binary CPD | hBN or orth-SiC phase from dry-run | Shows `UC_DF` or `DONE` (not stuck at `CPD_POST`) |
| Stalled recovery | Monitor compute loop logs | Stalled dir shows "Applying POTIM fix" log entry |

## Architecture Decisions Summary

| ADR | Decision | Rationale |
|-----|----------|-----------|
| A1 | Extract module-level `_phase()` + `_advance_system()` | Single source of truth; no drift |
| A2 | Remove `active` dict; use submission DB | ProcessPoolExecutor can't share memory |
| A3 | Direct chem-pot computation for binary compounds | pydefect doesn't support 1D CPD |
| A4 | Dry-run guard + per-dir convergence check | Prevent mutations during dry-run; protect running jobs |
| A5 | POTIM increase + stalled-flag reset for stalled jobs | Auto-recovery without manual intervention |
| A6 | Atomic state file write | Crash-safe pipeline state persistence |
