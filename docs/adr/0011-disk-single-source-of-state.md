# Disk is the single source of state; display derives, ledgers stay internal

A system's pipeline phase and every calculation's outcome were carried by up to three overlapping state stores — `state.json` per system root (ADR 0001's persisted phase memory), the JobStore SQLite ledger (`pending/submitted/converged/unconverged/failed`), and the crisp job DB (8 scheduling states) — plus the filesystem itself. The persisted phase memory was redundant in practice: the orchestrator already inferred phases from disk on every cycle (all five `derive_phase` call sites), the phase-gate audits (empty `target_vertices.yaml`, missing `standard_energies.yaml`) removed ADR 0001's "ambiguous filesystem" objection, and the webui had to fall back to disk inference whenever `state.json` was missing (already-done or excluded systems showed UNKNOWN). We now treat **the filesystem as the single authority**: convergence verdicts from `OUTCAR` for jobs, `derive_phase` for system phases. `state.json` is no longer read or written; the webui derives phases from disk (ADR 0011 supersedes ADR 0001).

The two ledgers stay but are explicitly **internal**: crisp's job states drive scheduling (dispatch, cancel, fetch) and the JobStore drives dedup/retry/recovery bookkeeping. Neither is a state *source* for the UI — the progress view maps disk truth plus crisp queue activity into user states (queued / running / done / failed / waiting for chain), and stale-ledger conditions are repaired by the existing reconcile paths (disk truth wins, ADR 0003/0004).

## Considered options

- **Restore ADR 0001 memory authority** — make the orchestrator read `state.json` first. Rejected: it re-introduces the very divergence (stale marker vs disk regression) that produced today's 349 stale-converged records; disk inference has run reliably for a year and the phase-gate audits make it unambiguous.
- **Eliminate the JobStore too** — rejected: dedup, retry budgets and crash recovery need a persistent ledger; it is a bookkeeping implementation detail, not a state vocabulary.
- **Keep state.json as a display cache** — rejected: the cache's misses (done/excluded systems) were exactly the UNKNOWN bug, and full-disk inference costs ~6s once per 60s TTL — negligible.

## Consequences

- `System.phase()` is now an alias of `derive_phase()`; `save_phase`/`_read_state`/`_STATE_FILE` removed; orchestrator's post-cycle persist calls removed.
- The webui `_system_phase` derives directly from disk; UNKNOWN now means "unparseable system" (missing plan.yaml), not "cache miss".
- Legacy `state.json` files on disk are inert (never read) and will be cleaned up.
- Phase vocabulary in CONTEXT.md drops "persisted memory"; the ledgers are documented as internal.
