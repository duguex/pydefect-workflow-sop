# Batch Loop Lifecycle Spec

**Goal:** Add start/stop/status subcommands to batch loop so users don't manage PIDs manually.

## Commands

```
vasp-sop batch start  <root>     daemonize --loop, write PID file, exit
vasp-sop batch stop   <root>     send SIGTERM to loop PID
vasp-sop batch status <root>     print PID, uptime, current phase counts
vasp-sop batch run    <root> --loop   foreground (unchanged)
```

## Behavior

### start

1. Check `{root}/.batch_loop.pid` exists and PID alive → error "already running"  
2. Fork → parent exits; child writes PID, sets up file logging, enters loop  
3. SIGTERM handler sets `_stop_requested = True`; loop finishes current round and exits

### stop

1. Read PID file  
2. If PID alive → `os.kill(pid, signal.SIGTERM)`, wait up to 10s for exit  
3. If PID gone / file missing → no-op (not an error)

### status

1. Read PID file, check alive  
2. Print: `PID 12345  running 2h13m` or `stopped (PID file stale)`  
3. Optionally print latest `batch_snapshot.json` phase summary

## Implementation

| File | Change |
|------|--------|
| `vasp_sop/cli/main.py` | Add `start`/`stop`/`status` to batch subparser, handlers, PID helpers |
| `tests/test_cli.py` | Add `TestBatchLifecycle` class: start → alive, stop → kills, double-start rejected |

- PID helpers in `_batch_loop_lifecycle` or inline (3 small functions)
- `signal.signal(signal.SIGTERM, handler)` sets a module-level flag
- Loop's `while True:` → `while not _stop_requested:`
- Fork via `os.fork()`; parent `sys.exit(0)`, child runs loop
- PID file: atomic write via `tempfile + os.rename`

## Non-goals

- Restart / resume from last round  
- Multi-user safety  
- PID file path override