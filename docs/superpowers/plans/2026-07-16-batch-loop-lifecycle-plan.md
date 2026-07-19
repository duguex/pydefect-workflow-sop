# Batch Loop Lifecycle Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or inline execution.

**Goal:** Add `batch start/stop/status` subcommands with PID file + SIGTERM lifecycle.

**Architecture:** Three new batch sub-actions, `_stop_requested` module flag, `signal` handler, PID file helpers.

**Tech Stack:** Python stdlib `signal`, `os`, `time`

## Global Constraints

- `batch run --loop` foreground behavior unchanged
- PID file at `{root}/.batch_loop.pid`
- Atomic write via temp + rename
- No new dependencies

---

### Task 1: Add start/stop/status parsers and handlers

**Files:** Modify `vasp_sop/cli/main.py`

- [ ] Add `start`, `stop`, `status` subparsers under `batch_sub` with `root` arg
- [ ] Wire to handler dispatch in `_handle_batch`
- [ ] Stub handlers that print placeholder

### Task 2: Implement PID helpers + signal handler

**Files:** Modify `vasp_sop/cli/main.py`

- [ ] `_pid_file(root)` → `root / ".batch_loop.pid"`
- [ ] `_write_pid(root)` → atomic write via tempfile + os.rename
- [ ] `_read_pid(root)` → int or None
- [ ] `_is_alive(pid)` → `os.kill(pid, 0)` try/except
- [ ] `_stop_requested = False` + `signal.signal(signal.SIGTERM, _handle_sigterm)`
- [ ] Loop: `while not _stop_requested:` instead of `while True:`

### Task 3: Implement start/stop/status handlers

**Files:** Modify `vasp_sop/cli/main.py`

- [ ] `_batch_start`: check alive → reject; fork → parent exit; child writes PID, sets up logging, enters loop
- [ ] `_batch_stop`: read PID, check alive, send SIGTERM, wait
- [ ] `_batch_status`: read PID, print running/stopped + snapshot phase summary

### Task 4: Tests + commit

**Files:** Modify `tests/test_cli.py`

- [ ] `test_start_creates_pid_and_rejects_duplicate`
- [ ] `test_stop_kills_and_cleans_pid`
- [ ] `test_status_reports_running`
- [ ] `test_sigterm_exits_after_current_round`
- [ ] Run all: `pytest tests/test_cli.py -q -k 'batch or BatchLifecycle'`