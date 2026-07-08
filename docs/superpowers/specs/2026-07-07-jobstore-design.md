# JobStore — Per-Calculation State Tracking

> Date: 2026-07-07
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Status: Design

## Background

The current PhaseStore records **system-level** phase (`TARGET`/`UC_DF`/`DONE`), but this is derived from aggregating many individual VASP calculations within a system. A system's phase can't tell you whether `unitcell/band` is still running or `defect/Va_N_0` failed.

The existing `submissions.db` records only "submitted" state with a staleness window, and the cache records only "completed" state. There's no unified view of every calculation's current status.

## Design

### JobStore

**Location**: New file `vasp_sop/core/job_store.py`

**Storage**: SQLite at `~/.vasp_sop/jobs.db`

**Schema**:

```sql
CREATE TABLE IF NOT EXISTS job_history (
    dir_path    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('waiting', 'running', 'done')),
    timestamp   REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'batch_run'
);
CREATE INDEX IF NOT EXISTS idx_jh_dir_time
    ON job_history(dir_path, timestamp);
```

**States**:

```
waiting  ──→  running  ──→  done
  (初始)       (已提交)      (OUTCAR 收敛)
```

No `failed` state — the simple model treats unconverged as `running` (pipeline retries). A `failed` designation can be added later if needed.

**Class interface**:

```python
class JobStore:
    def __init__(self, db_path: Path | None = None) -> None: ...

    def record(self, dir_path: str, status: str,
               source: str = "batch_run") -> None: ...

    def latest(self, dir_path: str) -> str | None:
        """Return latest status for a directory, or None if never recorded."""

    def latest_all(self) -> dict[str, str]:
        """Return {dir_path: latest_status} for every dir with records."""

    def history(self, dir_path: str) -> list[dict]: ...

    def close(self) -> None: ...
```

### Integration: status recording

| Trigger point | Action |
|---|---|
| `_submit_or_skip()` after `submit_vasp()` succeeds | `job_store.record(str(dir_path), "running")` |
| `_batch_run()` polling loop, when `check_converged(wd)` | `job_store.record(str(wd), "done")` |
| Backfill (one-time init) | `job_store.record(str(dir), "done")` for dirs with converged OUTCAR; `job_store.record(str(dir), "running")` if in `_get_submitted_dirs()` |

### Integration: system phase derivation

**Single source of truth**: The existing `_phase()` function is updated to use JobStore instead of `cache_lookup` for checking calculation completion. This way the pipeline's decision logic and `batch status` display are always consistent — no separate derivation function.

Changes to `_phase()`:

| Before | After |
|---|---|
| `if cache_lookup(td) is None: return "TARGET"` | `if store.latest(str(td)) != "done": return "TARGET"` |
| `uc_pending = any(cache_lookup(uc_root / t) is None ...)` | `uc_pending = any(store.latest(str(uc_root / t)) != "done" ...)` |
| `if cache_lookup(child) is not None: continue` (in `_competing_dirs`) | `if store.latest(str(child)) == "done": continue` |

The marker-file checks (`target_vertices.yaml`, `defect_energy_summary.json`, `composition_energies.yaml`) stay as-is — they are filesystem gates that don't depend on cache.

`batch status` calls `_phase()` (same function as the pipeline). No separate derivation code.

### CLI: `batch status`

Output a table with:

```
System                 P   Phase        Running   Done   Total
───────────────────────────────────────────────────────────────
AlN                    P4  DONE              0     10      10
BaTe                   P2  UC_DF             2     25      30
CaMg2(SO4)3            P1  TARGET            0      0       8
```

The Running/Done/Total columns show per-system calculation counts aggregated from JobStore.

No separate `job` subcommands needed per user choice.


### Cleanup

| Item | Action |
|---|---|
| `vasp_sop/core/phase_store.py` | Delete (replaced by job_store.py) |
| `.superpowers/sdd/progress.md` | Can be kept or cleaned up later |
| `AGENTS.md` PhaseStore references | Update to JobStore |

### Non-goals

- No change to `submissions.db` — still used for dedup guard (`is_submitted`)
- No change to cache — still used for cross-project result reuse
- No `failed` state in v1 (can be added when error diagnosis integration is ready)

### Testing

- `tests/test_job_store.py` — 6 tests (same pattern as PhaseStore tests)
- `tests/test_cli.py` — update `TestBatchStatus` to check new columns
- Walkthrough — verify JobStore recording at each submit/converge point

### Files changed

| File | Action |
|---|---|
| `vasp_sop/core/job_store.py` | Create |
| `vasp_sop/cli/main.py` | Modify: `_submit_or_skip` records running; polling records done; `_batch_status` derives phases from JobStore; replace `_phase` imports |
| `vasp_sop/core/phase_store.py` | Delete |
| `tests/test_job_store.py` | Create |
| `tests/test_cli.py` | Modify: status test format, walkthrough assertions |
| `AGENTS.md` | Update |
