# PhaseStore — Unified State Persistence

> Date: 2026-07-07
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Status: Design

## Background

vasp-sop currently has no persistent phase tracking. The pipeline's `_phase()` function derives the current stage (TARGET/COMPETING/CPD_POST/UC_DF/DONE) entirely from filesystem state on every call — no record is kept of when a system entered a given phase or what the progression looks like over time.

`batch status` runs its own independent scan (`_check_cpd`/`_check_unitcell`/`_check_defect`) that produces a different state representation (three columns of ✓/▶/·) than `_phase()`. This misalignment means a human operator and the pipeline logic can disagree on what's happening.

Legacy `StateStore` and `PipelineState` were removed in #77 but left behind: an empty `StepStatus` enum in `state.py`, stale references in `AGENTS.md`, orphaned `.pipeline_state.json` files in production, and no clear picture of how state is actually tracked.

## Scope

- Add a `PhaseStore` class backed by SQLite that records phase transitions
- Unify `batch status` to read from PhaseStore (single source of truth)
- Add `batch history <system>` CLI command for timeline queries
- Remove dead code: `state.py`, `test_state.py`, stale references

## Design

### PhaseStore

**Location**: New file `vasp_sop/core/phase_store.py`

**Schema** (`~/.vasp_sop/phases.db`):

```sql
CREATE TABLE IF NOT EXISTS phase_history (
    system_name TEXT NOT NULL,
    phase       TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'batch_run',
    -- source: 'batch_run' | 'manual' | 'init'
    FOREIGN KEY (phase) REFERENCES phase_names(name)
);
CREATE INDEX IF NOT EXISTS idx_ph_sys_time
    ON phase_history(system_name, timestamp);
```

**Class interface**:

```python
class PhaseStore:
    def __init__(self, db_path: Path | None = None) -> None:
        """Open or create phases.db (default: ~/.vasp_sop/phases.db)."""

    def record(self, system_name: str, phase: str, source: str = "batch_run") -> None:
        """Insert a phase transition record."""

    def latest(self, system_name: str) -> str | None:
        """Return the most recent phase for *system_name*, or None."""

    def latest_all(self) -> dict[str, str]:
        """Return {system_name: latest_phase} for every system with records."""

    def history(self, system_name: str) -> list[dict]:
        """Return chronologically ordered phase records for *system_name*.

        Each record: {"phase": str, "timestamp": float, "source": str}
        """

    def close(self) -> None:
        """Close the database connection."""
```

**Schema** (`~/.vasp_sop/phases.db`):

```sql
CREATE TABLE IF NOT EXISTS phase_history (
    system_name TEXT NOT NULL,
    phase       TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'batch_run'
    -- source: 'batch_run' | 'manual' | 'init'
);
CREATE INDEX IF NOT EXISTS idx_ph_sys_time
    ON phase_history(system_name, timestamp);
```

**Thread safety**: SQLite with WAL mode + connection-per-call pattern (same as `submissions.db`). No lock needed at Python level.

### Integration points

**`_advance_one_system()`** (`main.py`):

After computing `p = _phase(s)` and before returning, add:

```python
from vasp_sop.core.phase_store import PhaseStore
store = PhaseStore()
store.record(s["name"], p)
store.close()
```

This records the current phase after every cycle. No change to the existing submission/polling logic.

**`_batch_status()`** (`main.py`):

Replace `_scan_system()` / `_check_cpd()` / `_check_unitcell()` / `_check_defect()` with a read from PhaseStore:

```python
store = PhaseStore()
phase_map = store.latest_all()
for d in sorted(root.iterdir()):
    ...
    p = phase_map.get(d.name, "INIT")
    # Display p directly instead of scanning for ✓/▶/·
```

The output format changes from three columns (CPD/Unitcell/Defect) to a single phase column. This eliminates the inconsistency.

**`_batch_history()`** — new function:

```python
def _batch_history(root: Path, *, system: str | None = None) -> None:
    """Print phase timeline for one or all systems."""
    store = PhaseStore()
    if system:
        records = store.history(system)
        for r in records:
            ts = datetime.fromtimestamp(r["timestamp"]).isoformat()
            print(f"  {ts}  {r['phase']:12s}  {r['source']}")
    else:
        phase_map = store.latest_all()
        for name, phase in sorted(phase_map.items()):
            print(f"  {name:22s}  {phase}")
```

Registered as `batch history [--system NAME]` in the argument parser.

### Cleanup

| File/Reference | Action |
|---|---|
| `vasp_sop/core/state.py` | Delete entire file |
| `tests/test_state.py` | Delete entire file |
| `AGENTS.md` §State module | Replace description with PhaseStore reference |
| `AGENTS.md` §`.pipeline_state.json` | Remove, replace with PhaseStore |
| `main.py:275` `--root` help text | Remove "containing .pipeline_state.json" |
| `/2025_undergo/SrS/.pipeline_state.json` | Delete (orphaned artifact) |

### Non-goals

- No change to `_phase()` logic — still filesystem-derived, still the authoritative decision mechanism
- No change to cache or submissions.db
- No change to `_advance_one_system()`'s submission/polling behavior
- No migration from old `.pipeline_state.json` (only one orphan exists, just delete it)

### Testing

- `test_phase_store.py` — 6 tests:
  - `test_record_and_latest`: record → latest returns same phase
  - `test_history_ordering`: multiple records → chronological list
  - `test_empty_latest`: no records → None
  - `test_latest_all`: multiple systems → all present
  - `test_record_updates_latest`: second record overwrites latest
  - `test_persistence`: close and reopen → data survives
- Update `test_cli.py` — verify `batch status` output format matches phase column
- Update walkthrough test — verify PhaseStore.record() is called during each phase transition

### Files changed

| File | Action |
|---|---|
| `vasp_sop/core/phase_store.py` | Create |
| `vasp_sop/cli/main.py` | Modify: `_batch_status`, add `_batch_history`, add parser, update help text |
| `vasp_sop/core/state.py` | Delete |
| `tests/test_phase_store.py` | Create |
| `tests/test_state.py` | Delete |
| `tests/test_cli.py` | Update: batch status test + walkthrough PhaseStore assertions |
| `AGENTS.md` | Update stale references |
