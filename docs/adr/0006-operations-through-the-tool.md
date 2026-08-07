# Operations through the tool: the production tree is a testbed

vasp-sop is the deliverable; the production system tree is its acceptance
testbed. Every operation we perform on the testbed — from a verdict check
to a JobStore repair — must run through the vasp-sop/crisp CLIs. A missing
command is a development task, not an invitation to reach into package
internals by hand. All project python runs from `.venv`.

This decision came from a concrete failure: BaSe's `Se_mp-570481` finalize
surfaced a stale `submitted` record on `defect/Ba_Se1_1` (converged on disk,
unreconciled) that deadlocked wave3's analyze gate — the machine skips dirs
it believes are still running. Instead of fixing the tool, the gap was
hand-patched with a direct `JobStore.record(...)` call — training the tool
to route around its own defect. The repair is now the `batch reconcile`
pass (settles stale records from disk/crisp truth, wired into every `batch
run` cycle) with regression tests. It also settles the never-executed ghost
dirs: a fully-prepared dir with a stale `submitted` and no OUTCAR reads
`failed` (orphaned) — matching the tracked-dir orphan policy — unblocking
the analyze gate instead of hiding forever; dirs lacking inputs stay
untouched (a human scope decision). Recovery from a terminal record goes
through `batch retry` (reset to `pending`), never through hand-edited
JobStore rows.

We chose "gap = development task" over "ad-hoc one-off" because one-off
operations are faster today but leave the tool with the same gap forever;
converting each gap into a command accumulates capability and keeps the
tool the single source of truth for its own operations. The cost is real:
routine actions take longer (test + CLI first), and the tool must absorb
every operation we find ourselves doing by hand.