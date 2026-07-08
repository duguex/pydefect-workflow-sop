# PhaseStore — Unified State Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PhaseStore backed by SQLite that records each system's phase transitions, unifies `batch status` output, and provides `batch history` for timeline queries.

**Architecture:** PhaseStore class wraps `~/.vasp_sop/phases.db` with a simple CRUD interface. `_advance_one_system()` records after each cycle. `batch status` reads from PhaseStore instead of scanning directories. `batch history` queries the timeline.

**Tech Stack:** Python 3.10+, sqlite3, pytest

## Global Constraints

- SQLite with WAL mode (same pattern as `submissions.db` in `cache.py`)
- connection-per-call (each public method opens its own connection)
- `cache.py`'s SQLite helpers (`_submission_db`) are NOT reused — PhaseStore is standalone
- All names: `snake_case`
- No new dependencies beyond stdlib

---

### Task 1: Create `PhaseStore` class

**Files:**
- Create: `vasp_sop/core/phase_store.py`

**Interfaces:**
- Produces: `PhaseStore` class with:
  - `__init__(self, db_path: Path | None = None) -> None`
  - `record(self, system_name: str, phase: str, source: str = "batch_run") -> None`
  - `latest(self, system_name: str) -> str | None`
  - `latest_all(self) -> dict[str, str]`
  - `history(self, system_name: str) -> list[dict]`
  - `close(self) -> None`

- [ ] **Step 1: Write the class**

Content of `vasp_sop/core/phase_store.py`:

```python
"""Phase transition tracking — SQLite-backed history of pipeline state.

Each system records a new row every time ``_phase()`` returns a
different value.  ``batch status`` reads the latest record per system
from this store instead of scanning directories.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


_DEFAULT_DB = "phases.db"


def _db_path(given: Path | None) -> Path:
    if given is not None:
        return given
    from vasp_sop.core.cache import CACHE_ROOT
    return CACHE_ROOT / _DEFAULT_DB


class PhaseStore:
    """Record and query pipeline phase transitions per system."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._path = _db_path(db_path)
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self._path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        db = self._connection()
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS phase_history (
                    system_name TEXT NOT NULL,
                    phase       TEXT NOT NULL,
                    timestamp   REAL NOT NULL,
                    source      TEXT NOT NULL DEFAULT 'batch_run'
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_ph_sys_time
                ON phase_history(system_name, timestamp)
            """)
            db.commit()
        finally:
            db.close()

    def record(self, system_name: str, phase: str, source: str = "batch_run") -> None:
        """Insert a phase transition record."""
        db = self._connection()
        try:
            db.execute(
                "INSERT INTO phase_history (system_name, phase, timestamp, source) "
                "VALUES (?, ?, ?, ?)",
                (system_name, phase, time.time(), source),
            )
            db.commit()
        finally:
            db.close()

    def latest(self, system_name: str) -> str | None:
        """Return the most recent phase for *system_name*, or None."""
        db = self._connection()
        try:
            row = db.execute(
                "SELECT phase FROM phase_history "
                "WHERE system_name = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (system_name,),
            ).fetchone()
            return row["phase"] if row else None
        finally:
            db.close()

    def latest_all(self) -> dict[str, str]:
        """Return {system_name: latest_phase} for every system with records."""
        db = self._connection()
        try:
            rows = db.execute("""
                SELECT system_name, phase FROM phase_history
                WHERE (system_name, timestamp) IN (
                    SELECT system_name, MAX(timestamp)
                    FROM phase_history
                    GROUP BY system_name
                )
            """).fetchall()
            return {r["system_name"]: r["phase"] for r in rows}
        finally:
            db.close()

    def history(self, system_name: str) -> list[dict]:
        """Return chronologically ordered phase records."""
        db = self._connection()
        try:
            rows = db.execute(
                "SELECT phase, timestamp, source FROM phase_history "
                "WHERE system_name = ? "
                "ORDER BY timestamp ASC",
                (system_name,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def close(self) -> None:
        """No-op: connections are short-lived (per-call)."""
        pass
```

- [ ] **Step 2: Verify import**

```bash
python3 -c "from vasp_sop.core.phase_store import PhaseStore; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Quick smoke test**

```bash
python3 -c "
from vasp_sop.core.phase_store import PhaseStore
import tempfile, pathlib
p = PhaseStore(pathlib.Path(tempfile.mkdtemp()) / 'test.db')
p.record('GaN', 'TARGET')
p.record('GaN', 'DONE')
assert p.latest('GaN') == 'DONE'
assert len(p.history('GaN')) == 2
assert 'GaN' in p.latest_all()
p.close()
print('smoke ok')
"
```

Expected: `smoke ok`

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/phase_store.py
git commit -m "feat: add PhaseStore — SQLite-backed phase transition tracker"
```

---

### Task 2: Test `PhaseStore`

**Files:**
- Create: `tests/test_phase_store.py`

**Interfaces:**
- Tests: `PhaseStore` from Task 1

- [ ] **Step 1: Write tests**

```python
"""Tests for vasp_sop.core.phase_store — PhaseStore record/query lifecycle."""

from pathlib import Path
import time
import pytest


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, "PhaseStore"]:
    from vasp_sop.core.phase_store import PhaseStore
    db_path = tmp_path / "phases.db"
    return db_path, PhaseStore(db_path)


class TestPhaseStore:
    def test_record_and_latest(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        assert s.latest("GaN") == "TARGET"

    def test_history_ordering(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        time.sleep(0.01)
        s.record("GaN", "COMPETING")
        s.record("GaN", "DONE")
        history = s.history("GaN")
        assert len(history) == 3
        assert [r["phase"] for r in history] == ["TARGET", "COMPETING", "DONE"]
        assert all(r["source"] == "batch_run" for r in history)

    def test_empty_latest(self, store):
        _, s = store
        assert s.latest("nonexistent") is None

    def test_empty_history(self, store):
        _, s = store
        assert s.history("nonexistent") == []

    def test_latest_all_multiple_systems(self, store):
        _, s = store
        s.record("GaN", "DONE")
        s.record("SiC", "UC_DF")
        s.record("hBN", "DONE")
        all_phases = s.latest_all()
        assert all_phases["GaN"] == "DONE"
        assert all_phases["SiC"] == "UC_DF"
        assert all_phases["hBN"] == "DONE"

    def test_record_updates_latest(self, store):
        _, s = store
        s.record("GaN", "TARGET")
        assert s.latest("GaN") == "TARGET"
        s.record("GaN", "DONE")
        assert s.latest("GaN") == "DONE"

    def test_custom_source(self, store):
        _, s = store
        s.record("GaN", "DONE", source="manual")
        assert s.history("GaN")[0]["source"] == "manual"

    def test_persistence(self, tmp_path):
        from vasp_sop.core.phase_store import PhaseStore
        db_path = tmp_path / "phases.db"
        s1 = PhaseStore(db_path)
        s1.record("GaN", "DONE")
        s1.close()
        s2 = PhaseStore(db_path)
        assert s2.latest("GaN") == "DONE"
        s2.close()
```

- [ ] **Step 2: Run tests**

```bash
python3 -m pytest tests/test_phase_store.py -v
```

Expected: 8 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_phase_store.py
git commit -m "test: PhaseStore — 8 tests for record/latest/history/persistence"
```

---

### Task 3: Integrate PhaseStore into `_advance_one_system()`

**Files:**
- Modify: `vasp_sop/cli/main.py` — add PhaseStore.record() call near the end of `_advance_one_system()`

**Interfaces:**
- Consumes: `PhaseStore` from Task 1

- [ ] **Step 1: Add record call**

Find `_advance_one_system()` in `vasp_sop/cli/main.py`. Near the end, after the UC_DF handling block and before the function returns, find the natural exit point. The function has multiple return paths (COMPETING: at line 1103, CPD_POST: at line 1123, UC_DF: falls through to end). Add recording at each exit point.

The cleanest approach: at the TOP of `_advance_one_system()`, after the `p = _phase(s)` call (line 1031), record the current phase. This way every invocation records, regardless of which branch fires.

```python
    p = _phase(s)
    if p == "DONE" or p == "NO_TARGET":
        return

    root_dir = s["root"]
```

Change to:

```python
    p = _phase(s)
    if p == "DONE" or p == "NO_TARGET":
        return

    from vasp_sop.core.phase_store import PhaseStore
    _ps = PhaseStore()
    _ps.record(s["name"], p)
    _ps.close()

    root_dir = s["root"]
```

This imports lazily (inside the function) matching the existing pattern, and records every time `_advance_one_system` is called on a non-terminal phase.

- [ ] **Step 2: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('vasp_sop/cli/main.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 3: Verify tests still pass**

```bash
python3 -m pytest tests/ -x -q
```

Expected: all pass (PhaseStore will be empty — no test asserts on it yet; next task updates tests)

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "feat: record phase in PhaseStore on each _advance_one_system cycle"
```

---

### Task 4: Rewrite `_batch_status()` + add `_batch_history()`

**Files:**
- Modify: `vasp_sop/cli/main.py`

**Interfaces:**
- Consumes: `PhaseStore.latest_all()`, `PhaseStore.history()` from Task 1

- [ ] **Step 1: Replace `_batch_status()` body**

Current `_batch_status()` calls `_scan_system()` → `_check_cpd()` / `_check_unitcell()` / `_check_defect()`. Replace with PhaseStore read.

```python
def _batch_status(root: Path) -> None:
    """Scan *root* for vasp-sop systems and print status table with phases."""
    from vasp_sop.core.phase_store import PhaseStore

    store = PhaseStore()
    phase_map = store.latest_all()
    store.close()

    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan = d / "plan.yaml"
        if not plan.is_file():
            continue
        name = d.name
        phase = phase_map.get(name, "INIT")
        from vasp_sop.cli.main import _PRIORITY_MAP
        pri = _PRIORITY_MAP.get(name, "—")
        rows.append({"name": name, "pri": pri, "phase": phase})

    if not rows:
        print(f"No vasp-sop systems found in {root}")
        return

    print(f"{'System':<22} {'P':<3} {'Phase':<12}")
    print("-" * 40)
    for r in rows:
        print(f"{r['name']:<22} {r['pri']:<3} {r['phase']:<12}")

    done = sum(1 for r in rows if r["phase"] == "DONE")
    print("-" * 40)
    print(f"Total: {len(rows)}  Done: {done}  "
          f"Remaining: {len(rows) - done}")
```

- [ ] **Step 2: Add `_batch_history()`**

```python
def _batch_history(root: Path, *, system: str | None = None) -> None:
    """Print phase timeline for one or all systems."""
    from vasp_sop.core.phase_store import PhaseStore
    from datetime import datetime

    store = PhaseStore()
    if system:
        records = store.history(system)
        if not records:
            print(f"No history for system '{system}'.")
            return
        print(f"Timeline for {system}:")
        for r in records:
            ts = datetime.fromtimestamp(r["timestamp"]).isoformat()
            print(f"  {ts}  {r['phase']:<12s}  {r['source']}")
    else:
        phase_map = store.latest_all()
        if not phase_map:
            print("No phase records found.")
            return
        print(f"{'System':<22}  {'Phase':<12}")
        print("-" * 35)
        for name, phase in sorted(phase_map.items()):
            print(f"  {name:<22}  {phase:<12}")
    store.close()
```

- [ ] **Step 3: Register the CLI subcommand**

Find the `_add_batch_parser()` function (or equivalent — search for `"status"` in the argparser setup). Add a `"history"` subcommand after `"status"`:

```python
    # Inside _add_batch_parser or wherever batch subcommands are registered
    p_status = sub.add_parser("status", help="Show per-system phase table")
    p_status.set_defaults(batch_action="status")

    p_history = sub.add_parser("history", help="Show phase transition timeline")
    p_history.add_argument("--system", "-s", type=str, default=None,
                           help="System name (omit for all)")
    p_history.set_defaults(batch_action="history")
```

Then in the dispatch block inside `_handle_batch()`:

```python
    if args.batch_action == "status":
        _batch_status(root)
    elif args.batch_action == "history":
        _batch_history(root, system=args.system)
```

- [ ] **Step 4: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('vasp_sop/cli/main.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 5: Verify tests still pass**

```bash
python3 -m pytest tests/ -x -q
```

Expected: all pass. Note that `test_batch_status` tests currently assert on the old 3-column output format and will need updating (next task).

- [ ] **Step 6: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "feat: batch status reads from PhaseStore; add batch history command"
```

---

### Task 5: Update tests for new `batch status` format + PhaseStore integration

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `tests/test_production.py` (update phase check to match PhaseStore)

- [ ] **Step 1: Update `TestBatchStatus`**

Find `TestBatchStatus` in `tests/test_cli.py`. The existing tests check for the old 3-column format (cpd/unitcell/defect). Update them to check for the new single-phase column:

```python
class TestBatchStatus:
    """Issue #14: _batch_status must print a summary footer."""

    def _make_system(self, root: Path, formula: str) -> Path:
        """Create a minimal system dir for a *formula*."""
        d = root / formula
        d.mkdir()
        plan = {
            "project": {"formula": formula, "dopant_elements": [],
                        "poscar_src": f"MP mp-99999"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (d / "plan.yaml").write_text(yaml.dump(plan))
        return d

    def test_batch_status_one_system(self, tmp_path, monkeypatch):
        """Single system → header + one row + summary."""
        self._make_system(tmp_path, "GaN")
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "System" in captured
        assert "Phase" in captured
        # PhaseStore has no records → shows INIT
        assert "INIT" in captured
        assert "Total:" in captured

    def test_batch_status_with_phase(self, tmp_path, monkeypatch, capsys):
        """System with a PhaseStore record shows the recorded phase."""
        d = self._make_system(tmp_path, "GaN")
        from vasp_sop.core.phase_store import PhaseStore
        s = PhaseStore()
        s.record("GaN", "DONE")
        s.close()
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "DONE" in captured

    def test_batch_status_no_systems(self, tmp_path, monkeypatch, capsys):
        """Empty root → 'No vasp-sop systems found' message, no crash."""
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "No vasp-sop systems found" in captured
```

- [ ] **Step 2: Update walkthrough test to verify PhaseStore calls**

In `TestFullPipelineWalkthrough.test_walkthrough`, after each `_advance_one_system()` call, verify that PhaseStore now has a record:

```python
from vasp_sop.core.phase_store import PhaseStore

def _check_phase_recorded(self, system_name: str, expected_phase: str):
    s = PhaseStore()
    latest = s.latest(system_name)
    s.close()
    assert latest == expected_phase, f"PhaseStore: expected {expected_phase}, got {latest}"
```

Add calls after each phase transition:
```python
# After TARGET cycle:
_advance_one_system(s, dry_run=False)
self._check_phase_recorded("GaN", "TARGET")

# After COMPETING cycle:
_advance_one_system(s, dry_run=False)
self._check_phase_recorded("GaN", "COMPETING")
```

- [ ] **Step 3: Run tests**

```bash
python3 -m pytest tests/test_cli.py -x -q
```

Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py tests/test_production.py
git commit -m "test: update batch status tests for PhaseStore format"
```

---

### Task 6: Cleanup dead code + update docs

**Files:**
- Delete: `vasp_sop/core/state.py`
- Delete: `tests/test_state.py`
- Modify: `AGENTS.md`
- Delete: `/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/SrS/.pipeline_state.json`

- [ ] **Step 1: Delete `state.py`**

```bash
rm vasp_sop/core/state.py
```

- [ ] **Step 2: Delete `test_state.py`**

```bash
rm tests/test_state.py
```

- [ ] **Step 3: Update `AGENTS.md`**

Update line 85 (State module description):
```
| State | `vasp_sop/core/state.py` | PhaseStore (SQLite) — records per-system phase transitions, queried by `batch status` and `batch history` |
```

Replace `{root}/.pipeline_state.json` reference (line 286) with:
```
- Phase history: `~/.vasp_sop/phases.db` (SQLite — per-system phase transition logs, queried via `batch history`)
```

- [ ] **Step 4: Delete orphaned production file**

```bash
rm /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/SrS/.pipeline_state.json
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -q
```

Expected: all pass (some tests may have been removed with state.py/test_state.py)

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md
git rm vasp_sop/core/state.py tests/test_state.py
git commit -m "chore: remove dead state.py, update AGENTS.md for PhaseStore"
```

---

### Task 7: Run production test to verify

**Files:** (none)

- [ ] **Step 1: Run production test**

```bash
VASP_SOP_PROD_DIR=/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect \
  python3 -m pytest tests/test_production.py -v -k "not orphan"
```

Expected: all pass. Note that existing systems have no PhaseStore records, so `batch status` will show "INIT" for all of them until the next `batch run` cycle.

- [ ] **Step 2: Verify `batch history` CLI**

```bash
vasp-sop batch history /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect --system GaN
```
