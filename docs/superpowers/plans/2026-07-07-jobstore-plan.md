# JobStore — Per-Calculation State Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace system-level PhaseStore with per-calculation JobStore (SQLite). Each VASP calculation directory tracks `waiting → running → done`. System phase is derived from JobStore + marker files.

**Architecture:** JobStore class wraps `~/.vasp_sop/jobs.db` (SQLite). `_submit_or_skip` records `running`, polling loop records `done`. `_phase()` reads from JobStore instead of cache. `batch status` calls `_phase()` and shows Running/Done/Total columns.

**Tech Stack:** Python 3.10+, sqlite3, pytest

## Global Constraints

- SQLite with WAL mode (same pattern as `submissions.db`)
- connection-per-call (each public method opens its own connection)
- No new dependencies beyond stdlib
- `_phase()` keeps its signature but internal cache_lookup calls → JobStore calls
- PhaseStore files deleted after migration

---

### Task 1: Create `JobStore` class

**Files:**
- Create: `vasp_sop/core/job_store.py`

**Interfaces:**
- Produces: `JobStore` class with:
  - `__init__(self, db_path: Path | None = None) -> None`
  - `record(self, dir_path: str, status: str, source: str = "batch_run") -> None`
  - `latest(self, dir_path: str) -> str | None`
  - `latest_all(self) -> dict[str, str]`
  - `history(self, dir_path: str) -> list[dict]`
  - `close(self) -> None`

- [ ] **Step 1: Create the file**

```python
"""Per-calculation VASP job state tracking — SQLite-backed.

Tracks each VASP calculation directory through:
    waiting → running → done

System-level phase is derived from per-calculation states + marker
files in ``_phase()`` (see ``vasp_sop/cli/main.py``).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


_DEFAULT_DB = "jobs.db"


def _db_path(given: Path | None) -> Path:
    if given is not None:
        return given
    from vasp_sop.core.cache import CACHE_ROOT
    return CACHE_ROOT / _DEFAULT_DB


_VALID_STATUSES = frozenset({"waiting", "running", "done"})


class JobStore:
    """Record and query per-calculation VASP job states."""

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
                CREATE TABLE IF NOT EXISTS job_history (
                    dir_path    TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    timestamp   REAL NOT NULL,
                    source      TEXT NOT NULL DEFAULT 'batch_run'
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jh_dir_time
                ON job_history(dir_path, timestamp)
            """)
            db.commit()
        finally:
            db.close()

    def record(self, dir_path: str, status: str,
               source: str = "batch_run") -> None:
        """Insert a job state record."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}; "
                             f"must be one of {sorted(_VALID_STATUSES)}")
        db = self._connection()
        try:
            db.execute(
                "INSERT INTO job_history (dir_path, status, timestamp, source) "
                "VALUES (?, ?, ?, ?)",
                (dir_path, status, time.time(), source),
            )
            db.commit()
        finally:
            db.close()

    def latest(self, dir_path: str) -> str | None:
        """Return most recent status for *dir_path*, or None."""
        db = self._connection()
        try:
            row = db.execute(
                "SELECT status FROM job_history "
                "WHERE dir_path = ? ORDER BY timestamp DESC LIMIT 1",
                (dir_path,),
            ).fetchone()
            return row["status"] if row else None
        finally:
            db.close()

    def latest_all(self) -> dict[str, str]:
        """Return {dir_path: latest_status} for every dir with records."""
        db = self._connection()
        try:
            rows = db.execute("""
                SELECT dir_path, status FROM job_history
                WHERE (dir_path, timestamp) IN (
                    SELECT dir_path, MAX(timestamp)
                    FROM job_history GROUP BY dir_path
                )
            """).fetchall()
            return {r["dir_path"]: r["status"] for r in rows}
        finally:
            db.close()

    def history(self, dir_path: str) -> list[dict]:
        """Return chronologically ordered state records."""
        db = self._connection()
        try:
            rows = db.execute(
                "SELECT status, timestamp, source FROM job_history "
                "WHERE dir_path = ? ORDER BY timestamp ASC",
                (dir_path,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def close(self) -> None:
        pass
```

- [ ] **Step 2: Verify import**

```bash
python3 -c "from vasp_sop.core.job_store import JobStore; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Quick smoke test**

```bash
python3 -c "
from vasp_sop.core.job_store import JobStore
import tempfile, pathlib
j = JobStore(pathlib.Path(tempfile.mkdtemp()) / 'test.db')
j.record('/path/band', 'running')
j.record('/path/band', 'done')
assert j.latest('/path/band') == 'done'
assert len(j.history('/path/band')) == 2
assert '/path/band' in j.latest_all()
j.close()
print('smoke ok')
"
```

Expected: `smoke ok`

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/job_store.py
git commit -m "feat: add JobStore — per-calculation VASP job state tracker"
```

---

### Task 2: Test `JobStore`

**Files:**
- Create: `tests/test_job_store.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for vasp_sop.core.job_store — JobStore record/query lifecycle."""

from pathlib import Path
import time
import pytest


@pytest.fixture
def store(tmp_path: Path):
    from vasp_sop.core.job_store import JobStore
    return JobStore(tmp_path / "jobs.db")


class TestJobStore:
    def test_record_and_latest(self, store):
        store.record("/sys/band", "running")
        assert store.latest("/sys/band") == "running"

    def test_history_ordering(self, store):
        store.record("/sys/band", "waiting")
        time.sleep(0.01)
        store.record("/sys/band", "running")
        store.record("/sys/band", "done")
        history = store.history("/sys/band")
        assert len(history) == 3
        assert [r["status"] for r in history] == ["waiting", "running", "done"]

    def test_empty_latest(self, store):
        assert store.latest("/nonexistent") is None

    def test_empty_history(self, store):
        assert store.history("/nonexistent") == []

    def test_latest_all_multiple(self, store):
        store.record("/sysA/band", "done")
        store.record("/sysB/band", "running")
        all_st = store.latest_all()
        assert all_st["/sysA/band"] == "done"
        assert all_st["/sysB/band"] == "running"

    def test_record_updates_latest(self, store):
        store.record("/sys/band", "waiting")
        assert store.latest("/sys/band") == "waiting"
        store.record("/sys/band", "done")
        assert store.latest("/sys/band") == "done"

    def test_custom_source(self, store):
        store.record("/sys/band", "done", source="init")
        assert store.history("/sys/band")[0]["source"] == "init"

    def test_persistence(self, tmp_path):
        from vasp_sop.core.job_store import JobStore
        db_path = tmp_path / "jobs.db"
        s1 = JobStore(db_path)
        s1.record("/sys/band", "done")
        s1.close()
        s2 = JobStore(db_path)
        assert s2.latest("/sys/band") == "done"
        s2.close()

    def test_invalid_status_raises(self, store):
        import re
        with pytest.raises(ValueError, match="Invalid status"):
            store.record("/sys/x", "invalid_status")
```

- [ ] **Step 2: Run**

```bash
python3 -m pytest tests/test_job_store.py -v
```

Expected: 9 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_job_store.py
git commit -m "test: JobStore — 9 tests for record/latest/history/persistence"
```

---

### Task 3: Rewrite `_phase()` to use JobStore instead of cache

**Files:**
- Modify: `vasp_sop/cli/main.py`

**Interfaces:**
- Consumes: `JobStore.latest()` from Task 1
- Modifies: `_phase()` function, `_competing_dirs()` helper

- [ ] **Step 1: Update `_phase()` — replace cache_lookup with JobStore**

Current code:
```python
def _phase(s: dict) -> str:
    from vasp_sop.vasp.io import check_converged
    from vasp_sop.core.cache import cache_lookup
    td = _target_dir(s)
    if td is None:
        return "NO_TARGET"

    cpd_root = s["root"] / _CPD
    target_vertices = cpd_root / "target_vertices.yaml"

    if target_vertices.is_file():
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

    if cache_lookup(td) is None:
        return "TARGET"
    if _competing_dirs(s):
        return "COMPETING"
    return "CPD_POST"
```

Replace with:
```python
def _phase(s: dict) -> str:
    from vasp_sop.vasp.io import check_converged
    from vasp_sop.core.job_store import JobStore
    _js = JobStore()
    td = _target_dir(s)
    if td is None:
        return "NO_TARGET"

    cpd_root = s["root"] / _CPD
    target_vertices = cpd_root / "target_vertices.yaml"

    if target_vertices.is_file():
        uc_root = s["root"] / _UC
        uc_tasks = ["band", "dos", "dielectric"]
        uc_has_inputs = any((uc_root / t / "INCAR").is_file() for t in uc_tasks)
        if not uc_has_inputs:
            return "UC_DF"
        uc_pending = any(
            _js.latest(str((uc_root / t).resolve())) != "done"
            for t in uc_tasks
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

    if _js.latest(str(td.resolve())) != "done":
        return "TARGET"
    if _competing_dirs(s):
        return "COMPETING"
    return "CPD_POST"
```

- [ ] **Step 2: Update `_competing_dirs()` to use JobStore**

Find `_competing_dirs()` in `main.py`. Replace `cache_lookup(pd)` check:

```python
        if check_converged(pd):
            continue
        if is_submitted(str(pd.resolve())):
            continue
        if cache_lookup(pd) is not None:
            continue
```

Replace the last line with:
```python
        from vasp_sop.core.job_store import JobStore
        _js_local = JobStore()
        if _js_local.latest(str(pd.resolve())) == "done":
            continue
```

- [ ] **Step 3: Verify syntax**

```bash
python3 -c "import ast; ast.parse(open('vasp_sop/cli/main.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/ -x -q
```

Expected: all pass (some tests may break due to monkeypatched cache_lookup no longer being called — need Task 5 fix)

- [ ] **Step 5: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "refactor: _phase() reads from JobStore instead of cache"
```

---

### Task 4: Add JobStore recording to submission + polling

**Files:**
- Modify: `vasp_sop/cli/main.py`

**Interfaces:**
- Consumes: `JobStore.record()` from Task 1

- [ ] **Step 1: Record `running` in `_submit_or_skip()`**

In `_advance_one_system()`, find the inner `_submit_or_skip()` function. After `job = submit_vasp(path.resolve())` succeeds, add:

```python
            from vasp_sop.core.job_store import JobStore
            _js_local = JobStore()
            _js_local.record(str(path.resolve()), "running")
            _js_local.close()
```

The function should look like:
```python
    def _submit_or_skip(path: Path, label: str, sys_name: str) -> object:
        if dry_run:
            ...
        try:
            job = submit_vasp(path.resolve())
            mark_submitted(str(path.resolve()), job.task_name)
            from vasp_sop.core.job_store import JobStore
            _js = JobStore()
            _js.record(str(path.resolve()), "running")
            _js.close()
            print(f"  → {sys_name:<18} {label}: {job.task_name}")
            return job
        except ...
```

- [ ] **Step 2: Record `done` in polling loop**

In `_batch_run()`, find the completed-jobs polling loop (around line 1390-1401):

```python
    for wd_str in list(_get_submitted_dirs()):
        wd = Path(wd_str)
        if check_converged(wd):
            move_crisp_outputs(wd)
            _cache_phase_results(wd)
            clear_submission(wd_str)
            logger.info("Completed: %s", wd.name)
            completed += 1
```

After `clear_submission(wd_str)`, add:
```python
            from vasp_sop.core.job_store import JobStore
            _js = JobStore()
            _js.record(str(wd.resolve()), "done")
            _js.close()
```

Also add the same recording in the backfill loop (around line 1365, where already-converged phase results are cached):

```python
            if cache_lookup(pd) is not None:
                continue
            if not check_converged(pd):
                continue
            from vasp_sop.core.jobs import move_crisp_outputs
            move_crisp_outputs(pd)
            formula, mpid = pd.name.split("_mp-", 1)
            _cache_put(pd, formula=formula, task_name=f"{formula}_mp-{mpid}")
            backfilled += 1
```

After `backfilled += 1`, add:
```python
            from vasp_sop.core.job_store import JobStore
            JobStore().record(str(pd.resolve()), "done", source="backfill")
```

- [ ] **Step 3: Verify syntax + tests**

```bash
python3 -c "import ast; ast.parse(open('vasp_sop/cli/main.py').read()); print('syntax ok')"
python3 -m pytest tests/ -x -q
```

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "feat: record job states — running on submit, done on converge"
```

---

### Task 5: Update `_batch_status()` to show Running/Done/Total + PhaseStore cleanup

**Files:**
- Modify: `vasp_sop/cli/main.py`
- Delete: `vasp_sop/core/phase_store.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Update `_batch_status()` to aggregate from JobStore**

Current `_batch_status()` reads `PhaseStore.latest_all()`. Replace with a call to `_phase()` + JobStore aggregation for counts:

```python
def _batch_status(root: Path) -> None:
    """Scan *root* for vasp-sop systems and print status table."""
    from vasp_sop.core.job_store import JobStore
    from vasp_sop.core.config import PipelineConfig

    store = JobStore()
    all_jobs = store.latest_all()
    store.close()

    rows: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        plan = d / "plan.yaml"
        if not plan.is_file():
            continue
        try:
            config = PipelineConfig.from_yaml(plan, root=d)
            src = config.poscar_src
            mpid = src.split("mp-", 1)[1] if src.startswith("MP mp-") else None
            s = {"name": d.name, "root": d, "config": config,
                 "formula": config.formula, "mpid": mpid}
        except Exception:
            continue

        phase = _phase(s)

        # Aggregate per-system counts from JobStore
        prefix = str(d.resolve())
        running = sum(1 for p, st in all_jobs.items()
                      if p.startswith(prefix) and st == "running")
        done = sum(1 for p, st in all_jobs.items()
                   if p.startswith(prefix) and st == "done")
        total = sum(1 for p in all_jobs if p.startswith(prefix))

        pri = _PRIORITY_MAP.get(d.name, "—")
        rows.append({"name": d.name, "pri": pri, "phase": phase,
                      "running": running, "done": done, "total": total})

    if not rows:
        print(f"No vasp-sop systems found in {root}")
        return

    print(f"{'System':<22} {'P':<3} {'Phase':<10} {'Run':>4} {'Done':>4} {'Total':>5}")
    print("-" * 52)
    for r in rows:
        print(f"{r['name']:<22} {r['pri']:<3} {r['phase']:<10} "
              f"{r['running']:>4} {r['done']:>4} {r['total']:>5}")
    print("-" * 52)
    done_count = sum(1 for r in rows if r["phase"] == "DONE")
    print(f"Total: {len(rows)}  Done: {done_count}  "
          f"Remaining: {len(rows) - done_count}")
```

- [ ] **Step 2: Delete PhaseStore**

```bash
rm vasp_sop/core/phase_store.py
rm tests/test_phase_store.py
```

- [ ] **Step 3: Update tests**

In `tests/test_cli.py`, update `TestBatchStatus` to check new column headers:

```python
class TestBatchStatus:
    """batch status shows phase column + Running/Done/Total."""

    def _make_system(self, tmp_path: Path) -> Path:
        d = tmp_path / "GaN"
        d.mkdir()
        plan = {
            "project": {"formula": "GaN", "dopant_elements": [],
                        "poscar_src": "MP mp-804"},
            "parameters": {"functional": "pbesol"},
            "supercell": {"tool": "doped", "min_distance": 10.0},
        }
        (d / "plan.yaml").write_text(yaml.dump(plan))
        return d

    def test_batch_status_header(self, tmp_path, capsys):
        self._make_system(tmp_path)
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "System" in captured
        assert "Phase" in captured
        assert "Run" in captured
        assert "Done" in captured
        assert "Total" in captured

    def test_batch_status_no_systems(self, tmp_path, capsys):
        from vasp_sop.cli.main import _batch_status
        _batch_status(tmp_path)
        captured = capsys.readouterr().out
        assert "No vasp-sop systems found" in captured
```

Remove PhaseStore-related imports and assertions from walkthrough tests. Replace with JobStore assertions.

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/ -x -q
```

Expected: all pass (some test count changes from PhaseStore removal — expect ~169)

- [ ] **Step 5: Commit**

```bash
git add vasp_sop/cli/main.py tests/test_cli.py
git rm vasp_sop/core/phase_store.py tests/test_phase_store.py
git commit -m "refactor: batch status from JobStore; delete PhaseStore"
```

---

### Task 6: Backfill production data + update AGENTS.md

**Files:**
- Modify: `AGENTS.md`
- Production dir: `/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/`

- [ ] **Step 1: Backfill JobStore from existing OUTCARs**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect && python3 << 'PYEOF'
from pathlib import Path
from vasp_sop.core.job_store import JobStore
from vasp_sop.vasp.io import check_converged

store = JobStore()
root = Path(".")
count = 0
for outcar in sorted(root.rglob("OUTCAR")):
    d = outcar.parent
    if d.name == "output" and (d.parent / "OUTCAR").is_file():
        continue
    if check_converged(d):
        store.record(str(d.resolve()), "done", source="backfill")
        count += 1

# Also mark any submitted-but-not-yet-converged dirs
from vasp_sop.core.cache import _get_submitted_dirs
for sd in _get_submitted_dirs():
    p = Path(sd)
    if not check_converged(p):
        store.record(str(p.resolve()), "running", source="backfill")

store.close()
print(f"Backfilled {count} completed calculations")
PYEOF
```

- [ ] **Step 2: Update AGENTS.md**

Replace `PhaseStore` references with `JobStore`:
- State module description: `| State | \`vasp_sop/core/job_store.py\` | JobStore (SQLite) — per-calculation VASP job states, queried by \`batch status\` and \`_phase()\` |`
- Persistence: `- Job state: \`~/.vasp_sop/jobs.db\` (SQLite — per-calculation \`waiting/running/done\`, queried via \`batch status\`)`

- [ ] **Step 3: Verify `batch status`**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch status .
```

Expected: shows Phase + Running/Done/Total columns with real data

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: update for JobStore (per-calculation state tracking)"
```
