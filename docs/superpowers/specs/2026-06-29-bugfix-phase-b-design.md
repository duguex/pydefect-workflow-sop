# VASP SOP Bug Fix — Phase B: Cache Correctness & Integrity

> Date: 2026-06-29
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Status: Design — Phase B of 3-phase bug-fix campaign

## Background

Phase A fixed 14 pipeline-critical bugs (v0.2.0). Phase B addresses cache-layer issues from audit findings across `cache.py` and `pipeline.py`.

## Phase B Scope

Fix all cache-layer bugs that cause incorrect query results, data loss, or silent failures.

### B1: #43 — `vasp_results_get` misses mpid-lookup

**Problem:** `_check_calc_cache` in `pipeline.py:78` calls `vasp_results_get(formula_pt, mpid)` where `mpid` is a bare number like `"804"`. Inside `vasp_results_get`, it tries `content_hash == "804"` (miss) then `task_name == "804"` (miss — stored as `"GaN_mp-804"`). Always returns None.

**Fix:** Add a third fallback: `task_name == f"{formula}_mp-{key}"` before returning None.

**File:** `vasp_sop/core/cache.py:580-584`

### B2: #44 — `_check_calc_cache` calls nonexistent Outcar methods

**Problem:** `pipeline.py:110-111`: `Outcar.from_dict()` and `Outcar.write_file()` don't exist in pymatgen. The `except Exception` swallows the AttributeError, and the function returns `True` (success) even though OUTCAR was not restored.

**Decision:** The blob restore branch is fundamentally fragile. Since the `source_dir` copy path (lines 85-91) works correctly when the source directory exists, the blob restore branch is dead code that silently produces broken results. Remove it entirely.

**Fix:** Delete the blob restore branch (lines 108-113). The `source_dir` copy path is the only restore path.

**File:** `vasp_sop/defect/pipeline.py:108-113`

### B3: #46 — `query()` regex escaping

**Problem:** `cache.py:641-654` passes user input directly to `$regex` without `re.escape()`. Tags like `"DFT+U"` fail because `+` is a regex quantifier.

**Fix:** Wrap all user-supplied regex values with `re.escape()`.

**File:** `vasp_sop/core/cache.py:641-654`

### B4: #46 alt — query() duplicate criteria when functional+tags_contains combined

**Problem:** When both `functional` and `tags_contains` are set, `criteria["tags"]` from the functional path remains in the dict alongside the `$and` block, causing the functional regex to apply twice.

**Fix:** Delete `criteria["tags"]` after building the `$and` block.

**File:** `vasp_sop/core/cache.py:649-652`

### B5: #59 — TaskDoc silent bad data

**Problem:** `_parse_vasp_dir:339-358` runs `TaskDoc.from_directory()`. If it succeeds but returns `output.energy is None`, the function returns `converged=False, total_energy=None` without falling through to the regex fallback. `vasp_results_put` sees both falsey and skips caching.

**Fix:** After the TaskDoc branch, if `total_energy` is None, fall through to regex instead of returning.

**File:** `vasp_sop/core/cache.py:339-358`

### B6: backfill_all crashes on lattice-skipped entries

**Problem:** `_parse_and_build` returns `None` for entries exceeding `MAX_LATTICE`, but `backfill_all` appends the None to `results` and then crashes with `None["meta"]`.

**Fix:** Filter out None results before processing.

**File:** `vasp_sop/core/cache.py:709-720`

### B7: _parse_and_build missing lattice fields

**Problem:** `_parse_and_build` meta dict omits `a`, `b`, `c`, `max_abc` that `vasp_results_put` stores. Backfilled entries lack lattice parameters, breaking `query(lattice_max=)` filter.

**Fix:** Add the four lattice fields to the meta dict.

**File:** `vasp_sop/core/cache.py:750-753`

### B8: Migration blob format incompatible

**Problem:** `migrate_from_sqlite` stores blob fields as flat dict keys (`"outcar_dict": ...`, `"vasprun_dict": ...`) but `vasp_results_get` expects `"blob_json": "..."` containing the full JSON string.

**Fix:** Store migrated blobs in the `{"content_hash": ch, "blob_json": json.dumps(...)}` format.

**File:** `vasp_sop/core/cache.py:823-833`

### B9: Field name inconsistency `n_sites` vs `nsites`

**Problem:** `vasp_results_put` and `_parse_and_build` store `n_sites`, but `migrate_from_sqlite` stores `nsites`, and `list_cache` queries for `nsites`. CLI reads `entry.get('n_sites')`. Neither path works for the other's data.

**Fix:** Standardize on `nsites` everywhere (matching the query projection and migration).

**Files:** `vasp_sop/core/cache.py:547,750,814,856`, `vasp_sop/cli/main.py:612`

### B10: Regex catches first TOTEN instead of last

**Problem:** `_re.search(r"free energy TOTEN...")` in both `_parse_vasp_dir` and `_build_blob` returns the first match (initial step energy), not the last (final converged energy).

**Fix:** Use `_re.findall(...)[-1]` to get the last energy value.

**Files:** `vasp_sop/core/cache.py:374-376, 465-469`

### B11: break skips POSCAR on corrupt CONTCAR

**Problem:** In `_parse_vasp_dir` regex fallback, `break` is at the `if cand.is_file():` level, not inside the try block. If CONTCAR exists but is corrupt, `Structure.from_file` raises, the loop breaks without trying POSCAR.

**Fix:** Move `break` inside the try block; change `except Exception: pass` to `except Exception: continue`.

**File:** `vasp_sop/core/cache.py:387-399`

### B12: Same break-outside-try in `_build_blob`

**Problem:** Same pattern as B11 in `_build_blob:481-488`.

**Fix:** Same fix as B11.

**File:** `vasp_sop/core/cache.py:481-488`

## Verification

| Check | Method | Expected |
|-------|--------|----------|
| Tests pass | `python3 -m pytest tests/ -v` | All 130+ tests PASS |
| Cache query tags | `vasp-sop cache query --tags DFT+U` | Returns results (not 0) |
| Cache query lattice | `vasp-sop cache query --max-lattice 20` | Returns correct results |
| Cache query functional | `vasp-sop cache query --functional PBE` | Returns results (not 0) |
| Batch dry-run | `vasp-sop batch run . --dry-run` | No crashes, correct phases |
