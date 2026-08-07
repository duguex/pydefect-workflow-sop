# Next Actions — Architecture Repair

> Last updated: 2026-07-20. Source: Architecture Review → GitHub #102.
> Read this file, then `gh issue view <N>` for full acceptance criteria.

## Execution Order

```
#96 → #103 → #95 → #93
```

P2 (#94, #97, #98, #99, #100, #101) interleaved anytime. #40, #51 independent.

---

## #96 — Fix daemon NameError (P0, ~10 min)

**File:** `vasp_sop/cli/main.py`

**Fix:** Add missing imports at top of file:
```python
from vasp_sop.core.batch_lifecycle import (
    daemonize as _batch_start,
    stop as _lifecycle_stop,
    is_stop_requested,
)
```

**Verify:** `vasp-sop batch start /tmp/test && vasp-sop batch stop /tmp/test` — no NameError.

---

## #103 — Domain abstractions (P1, largest item)

### 103a. System class

Create `vasp_sop/core/system.py`:

```python
class System:
    def __init__(self, root: Path, config: PipelineConfig):
        self.root = root
        self.config = config
        self.name = root.name

    @property
    def cpd_dir(self) -> Path: return self.root / "cpd"
    @property
    def uc_dir(self) -> Path: return self.root / "unitcell"
    @property
    def defect_dir(self) -> Path: return self.root / "defect"
    @property
    def target_dir(self) -> Path: ...  # resolve from cpd/ or unitcell/structure_opt

    def phase(self) -> str: ...  # land: see System.phase() in core/system.py (landed)
    def defect_dirs(self) -> list[Path]: ...  # filtered, no junk
```

Migrate `_advance_one_system` to accept `System` instead of dict.

### 103b. Pydefect adapter

Create `vasp_sop/defect/pydefect_adapter.py`:

```python
def calc_results(dirs: list[Path], cwd: Path) -> list[CrResult]: ...
def efnv(dirs: list[Path], cwd: Path, ...) -> list[EfnvResult]: ...
def defect_energy_summary(cwd: Path, ...) -> SummaryResult: ...
```

- Import from `libs/` directly (Python API, not subprocess)
- Return structured dataclass per-dir (success/failed/skipped + parsed values)
- Replace `run_local(f'pydefect_vasp cr ...')` calls in `analysis.py`

### 103c. Explicit state markers

Add `state.json` per system root:
```json
{"phase": "CHEM_POT_DIAGRAM", "updated": "...", "history": [...]}
```

`System.phase()` reads state.json first, falls back to filesystem inference for migration.

---

## #95 — Wave decoupling (P1, depends on #103)

Split `_advance_one_system` into:

```python
def wave1_optimize(sys: System, js: JobStore, dry_run: bool) -> None: ...
def wave2_submit(sys: System, js: JobStore, dry_run: bool) -> None: ...
def wave3_postprocess(sys: System, dry_run: bool) -> AnalyzeStatus: ...
```

- Each has explicit preconditions (assert artifacts exist)
- No cross-wave directory access except via `System` handoff methods
- Move eager defect-building from wave1 to wave2 (or a `prepare()` step)
- `_advance_one_system` becomes: determine phase → call appropriate wave

---

## #93 — CPD correctness (P1, depends on #95)

Once waves are independent:
```python
# vasp-sop cpd run <system_dir> -f FORMULA
sys = System(root, config)
wave2_submit(sys, ...)   # competing phases only
wave3_postprocess(sys)   # CPD solving, stops before UC/defect
```

Plus: deterministic composition selection (lowest energy-per-atom), `cpd_excluded_phases.yaml`.

---

## Quick wins (P2, no dependencies)

| Issue | What to do | File(s) |
|-------|-----------|---------|
| #98 | Rewrite FEATURES.md cache + batch sections | `FEATURES.md` |
| #99 | Extract `_parse_max_f` → `vasp.io.parse_max_force`; create `tests/conftest.py` | `vasp/io.py`, `tests/` |
| #97 | Add `__enter__`/`__exit__` to JobStore; pass instance through batch loop | `core/job_store.py`, `cli/main.py` |
| #94 | Add analyze_status column to `batch status` output; remove `_MP_FLAG` | `cli/main.py`, `materials/mp.py` |
| #100 | Filter `defect_new/` and non-`Name_Charge` dirs in scans | `defect/analysis.py`, `cli/main.py` |

---

## Design decisions (do not revisit)

- GitHub Issues = single issue tracker (no local `issues/`)
- `libs/` forks are canonical; integrate via Python import, not subprocess
- Wave decoupling = independent invocability, not just file moves
- Phase state → explicit `state.json`, filesystem as migration fallback
