# Batch Run Logging & Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add file logging and machine-readable snapshots to `vasp-sop batch run --loop` with terminal silence in loop mode.

**Architecture:** Two new core modules (`logging.py` for file handler setup, `snapshot.py` for JSON state writer) plus wiring into `_batch_run` loop. No new dependencies. Non-loop mode unchanged.

**Tech Stack:** Python stdlib `logging`, `json`, `datetime`

## Global Constraints

- No new dependencies (pure stdlib)
- Non-loop single-pass mode must behave exactly as before
- Code paths must match existing `logging.getLogger(__name__)` pattern
- Commit after each task

---

### Task 1: `LogConfig` — file log handler for loop mode

**Files:**
- Create: `vasp_sop/core/logging.py`

**Interfaces:**
- Produces: `LogConfig.setup_file_logging(root: Path, *, log_path: Path | None = None) -> None`  
  Sets a `logging.FileHandler` at INFO level to `{root}/batch_run.log` (default).  
  Uplifts root logger's stderr handler to WARNING so terminal stays silent except warnings/errors.

```python
# vasp_sop/core/logging.py
"""Batch-loop file logging setup."""

from __future__ import annotations

import logging
from pathlib import Path

_STDERR_HANDLER_ATTR = "_vasp_sop_stderr_handler"


def setup_file_logging(root: Path, *, log_path: Path | None = None) -> None:
    """Enable file logging for batch loop mode.

    - File handler at INFO → ``{root}/batch_run.log``
    - Existing stderr handler lifted to WARNING (terminal quiet)
    - Call once at loop start.
    """
    fp = log_path or (root / "batch_run.log")
    root_logger = logging.getLogger()

    # Stash the current stderr handler so we don't add a second one.
    existing = getattr(root_logger, _STDERR_HANDLER_ATTR, None)
    if existing is not None:
        return  # already configured

    # Promote the console handler we already have (if any).
    for h in list(root_logger.handlers):
        if isinstance(h, logging.StreamHandler) and h.stream is not None:
            h.setLevel(logging.WARNING)
            setattr(root_logger, _STDERR_HANDLER_ATTR, h)

    fh = logging.FileHandler(str(fp), mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)
    logging.info("─── batch run loop started, log: %s ───", fp)
```

- [ ] **Step 1: Write module**

See code above.

- [ ] **Step 2: Write test**

```python
# tests/test_logging.py
"""Tests for vasp_sop.core.logging — file handler and terminal level."""

from pathlib import Path
import logging, time

from vasp_sop.core.logging import setup_file_logging


def test_file_handler_writes_and_terminal_warning_only(tmp_path: Path):
    root = logging.getLogger()
    # Remove any existing handlers for isolation.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.StreamHandler())  # stderr by default
    for h in root.handlers:
        h.setLevel(logging.INFO)  # simulate the current basicConfig state

    setup_file_logging(tmp_path)

    logging.info("should-only-be-in-file")
    logging.warning("should-be-in-both")

    log_file = tmp_path / "batch_run.log"
    assert log_file.is_file()
    content = log_file.read_text()
    assert "should-only-be-in-file" in content
    assert "should-be-in-both" in content

    # Terminal handler should be at WARNING.
    root_logger = logging.getLogger()
    for h in root_logger.handlers:
        if isinstance(h, logging.StreamHandler):
            assert h.level == logging.WARNING


def test_idempotent_calls_dont_duplicate(tmp_path: Path):
    setup_file_logging(tmp_path)
    before = len(logging.getLogger().handlers)
    setup_file_logging(tmp_path)
    assert len(logging.getLogger().handlers) == before
```

- [ ] **Step 3: Run tests**

`pytest tests/test_logging.py -q` → 2 passed

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/logging.py tests/test_logging.py
git commit -m "feat: add LogConfig file handler for batch loop"
```

---

### Task 2: `SnapshotWriter` — JSON state snapshots

**Files:**
- Create: `vasp_sop/core/snapshot.py`

**Interfaces:**
- Produces: `SnapshotWriter(root: Path)` with `write(state: dict) -> None` and `last() -> dict | None`

```python
# vasp_sop/core/snapshot.py
"""Per-round batch state snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_SNAPSHOT = "batch_snapshot.json"
_TIMELINE = "batch_timeline.jsonl"


class SnapshotWriter:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._snapshot = root / _SNAPSHOT
        self._timeline = root / _TIMELINE

    def write(self, state: dict) -> None:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state["timestamp"] = ts
        payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
        self._snapshot.write_text(payload, encoding="utf-8")
        line = json.dumps(state, ensure_ascii=False) + "\n"
        with self._timeline.open("a", encoding="utf-8") as f:
            f.write(line)

    def last(self) -> dict | None:
        if not self._snapshot.is_file():
            return None
        try:
            return json.loads(self._snapshot.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
```

- [ ] **Step 1: Write module**

See code above.

- [ ] **Step 2: Write test**

```python
# tests/test_snapshot.py
"""Tests for vasp_sop.core.snapshot — JSON snapshot writer."""

from pathlib import Path
import json

from vasp_sop.core.snapshot import SnapshotWriter


def test_write_overwrites_snapshot(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    sw.write({"phases": {"COMPLETE": 10}})
    first = json.loads((tmp_path / "batch_snapshot.json").read_text())
    assert first["phases"]["COMPLETE"] == 10
    assert "timestamp" in first

    sw.write({"phases": {"COMPLETE": 12}})
    second = json.loads((tmp_path / "batch_snapshot.json").read_text())
    assert second["phases"]["COMPLETE"] == 12


def test_append_to_timeline(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    sw.write({"round": 1})
    sw.write({"round": 2})
    lines = (tmp_path / "batch_timeline.jsonl").read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["round"] == 1
    assert json.loads(lines[1])["round"] == 2


def test_last_returns_previous(tmp_path: Path):
    sw = SnapshotWriter(tmp_path)
    assert sw.last() is None
    sw.write({"x": 1})
    assert sw.last()["x"] == 1
```

- [ ] **Step 3: Run tests**

`pytest tests/test_snapshot.py -q` → 3 passed

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/snapshot.py tests/test_snapshot.py
git commit -m "feat: add SnapshotWriter for batch state JSON snapshots"
```

---

### Task 3: Wire logging + snapshots into `_batch_run`

**Files:**
- Modify: `vasp_sop/cli/main.py`

**Interfaces:**
- Consumes: `vasp_sop.core.logging.setup_file_logging`, `vasp_sop.core.snapshot.SnapshotWriter`
- Produces: loop-mode log file + snapshots; no behavior change for non-loop

**Changes in `_batch_run`:**

1. At loop entry (after `if dry_run` / before first poll), when `loop=True`:
   ```python
   from vasp_sop.core.logging import setup_file_logging
   from vasp_sop.core.snapshot import SnapshotWriter
   setup_file_logging(root)
   sw = SnapshotWriter(root)
   ```

2. After each advance cycle's status summary, build state dict and write:
   ```python
   # After _print_summary() call
   from vasp_sop.defect.analysis import classify_analyze_status
   an = {"full": 0, "partial": 0, "failed": 0}
   for s in sys_list:
       df = s["root"] / "defect"
       if df.is_dir():
           try:
               an[classify_analyze_status(df)] += 1
           except Exception:
               pass
   state = {
       "phases": dict(counts),
       "analyze": an,
       "errors": [{"system": name, "reason": reason} for name, reason in errs],
   }
   # add crisp counts
   try:
       import json, subprocess
       r = subprocess.run(["crisp", "jobs", "-a"], capture_output=True, text=True, timeout=30)
       jl = json.loads(r.stdout).get("jobs") or []
       prod = [j for j in jl if (j.get("local_dir") or "").startswith(str(root))]
       state["crisp_active"] = sum(1 for j in prod if j.get("status") in ("submit","submitted","running","ready_fetch","pending"))
       state["crisp_running"] = sum(1 for j in prod if j.get("status") == "running")
       state["crisp_failed"] = sum(1 for j in prod if j.get("status") == "failed")
   except Exception:
       state["crisp_active"] = -1
   sw.write(state)
   ```

3. Convert info-level `print()` calls to `logging.info()` in loop mode:
   - This is a targeted change; existing `logging` calls already hit the file.

- [ ] **Step 1: Add setup call and snapshot writer to loop**

Add imports and initialization at loop entry. Add state build + write after each advance summary.

- [ ] **Step 2: Run existing CLI tests**

`pytest tests/test_cli.py -q -k "batch"` — non-loop must pass unchanged

- [ ] **Step 3: Manual smoke test**

```bash
vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect --loop
# After 1 cycle, interrupt. Verify:
# - batch_run.log exists with logged events
# - batch_snapshot.json exists with phases/analyze
# - batch_timeline.jsonl has one line
# - Terminal only shows warnings/errors
```

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "feat: wire file logging and snapshot into batch run --loop"
```

---

### Task 4: Docs update

**Files:**
- Modify: `FEATURES.md` — add entry under CLI Commands or batch section
- Modify: `docs/architecture/06-convergence.md` or create `docs/architecture/07-logging-snapshot.md`

- [ ] **Step 1: Update FEATURES**

```markdown
### Loop Logging

When run with `--loop`, batch outputs are split:

| Destination | Level | Content |
|-------------|-------|---------|
| `{root}/batch_run.log` | INFO+ | All submission/poll/post-process events |
| Terminal (stderr) | WARNING+ | Errors and warnings only |
| `{root}/batch_snapshot.json` | — | Latest per-system phases, analyze status, crisp counts (overwritten each round) |
| `{root}/batch_timeline.jsonl` | — | JSON line per round (appended, with timestamps) |

Usage:
```bash
vasp-sop batch run . --loop         # silent terminal, logs + snapshots on disk
tail -f batch_run.log               # monitor progress
cat batch_snapshot.json | jq .phases
```
```

- [ ] **Step 2: Commit**

```bash
git add FEATURES.md
git commit -m "docs: document batch --loop logging and snapshot files"
```