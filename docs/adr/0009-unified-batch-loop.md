# One unified batch loop across roots, strict dispatch priority via crisp

Two long-running per-root loops (2025 and 2026 project trees) were replaced by a single unified loop process serving both roots, because two loops sharing the global JobStore SQLite file deadlocked each other (`database is locked` on startup) and duplicated each other's active-task restores — the 2026 loop was resubmitting 2025-root unconverged dirs it had restored from the shared `tracked` table, and both processes died in the same cycle (14:27Z) under write-lock contention.

`vasp-sop batch run <root1> <root2> … --loop` now accepts multiple roots; left-to-right order sets each root's dispatch priority. Priority is enforced by a new `jobs.priority` column in crisp (default 0): the daemon dispatches `ORDER BY priority DESC, created_at ASC`, so higher-priority jobs exhaust before any lower-priority job starts — strict priority, never proportional fairness. vasp-sop derives the priority from the job's batch root (`10 * (n - 1 - root_index)`; the 2026 root is 10, the 2025 root 0) and passes it through `crisp submit --priority`. The daemon has no knowledge of project paths. The unified loop is supervised by a systemd user unit (`vasp-sop-loop.service`, `Restart=always`); its log and snapshot live under the first root.

## Considered options

- **Shared parent directory as one root** — rejected: `_collect_systems` scans one level, so the container dirs (not the systems) would be treated as systems.
- **External orchestrator script** alternating per-root `batch run` invocations — rejected: each invocation re-runs the active-task restore, and `--loop` never exits so true alternation is impossible.
- **Daemon-side hardcoded path prefix** for 2026 priority — rejected: embeds vasp-sop project paths into the generic scheduler; the `priority` column keeps it generic.

## Consequences

- A unified-loop crash stops both roots — acceptable because systemd restarts it, and the previous two-loop setup died together anyway (same DB, same signals).
- Strict priority means the 2025 root's 915 queued jobs wait until the 2026 root's ~600 jobs are dispatched; this was an explicit operator choice (2026 is the active work), reversible by reordering roots or editing `jobs.priority` directly.
- Existing queued 2026 jobs were backfilled to priority 10 with a one-time SQL update; new submissions carry the priority automatically.
- The retry state machine (ADR 0008) now runs inside the single loop rather than per root.
