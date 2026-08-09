# Automatic retry policy: track failures and resubmit until converged or terminal

**Status**: Supersedes ADR 0007

## Context

ADR 0007 defined the automation boundary as: failed defect directories get
**exactly one** automatic rerun (`source=auto_retry`), armed only by an
explicit `batch run --retry-failed`; a second failure is terminal forever.
Production experience invalidated that policy:

- 62 directories were auto_retried; only 10 converged, **49 are still in
  flight** — the one-shot marker does not track them to completion.
- The 2025 root (1344-dir NELECT recompute, 2026-08-08) stalled for 9 hours
  because no long-running loop existed to resubmit after the batch process
  died — "submit once and done" is not a retry policy.
- Failure causes split into two very different populations: transient
  (SIGKILL, TIME LIMIT, network — retry almost always succeeds once the
  cluster recovers) and persistent (ZBRENT, force-gate — retry has a real
  but bounded rescue rate). Treating both as "one shot" wastes either
  compute or completion.

## Decision

Replace the one-shot rule with a **classified retry state machine** driven
by a long-running batch loop:

1. **Failure classification** — crisp stores raw diagnostics (`.failed`,
   `{jobid}.log`); vasp-sop parses them into a failure class at the retry
   decision point (local files only, no SSH):
   - **transient**: SIGKILL, TIME LIMIT, network/submit errors → retried
     every loop cycle until success (cluster recovery makes these
     self-healing).
   - **persistent**: ZBRENT, force-gate fail, other physics → retried up to
     a configurable limit, **default 2** (override via `plan.yaml`/CLI),
     then marked terminal.
2. **Terminal state** — reuse the existing `failed` JobStore status with
   `reason=terminal:<class>`; no new status enum. `batch blockers` groups
   by reason prefix and shows: dir, failure class, attempt count, latest
   reason, suggested action.
3. **Trigger** — two persistent loops, one per project root
   (`batch run --loop` via the existing managed-process mechanism, cf.
   `batch-loops-2026`), each cycle re-checking failed dirs and resubmitting
   per class. No cooldown at the vasp-sop layer; crisp's global 60-job
   quota already provides natural queuing.
4. **Retry input handling** — by class:
   - `vasp_crash`/truncated → fresh resubmit;
   - unconverged → existing `handle_unconverged` CONTCAR-restart loop
     (**unchanged**, `_MAX_RESTART=5` + stalled gate — production-proven);
   - ZBRENT/persistent → plain resubmit (no parameter mutation; EDIFF/ALGO
     changes are physics decisions, not automation).
5. **CPD competing phases** — currently retried infinitely with no gate;
   now classified the same way, and a persistent-failure phase reaching its
   limit becomes a terminal blocker that keeps the system in COMPETING for
   human decision. Never auto-writes `cpd_excluded_phases.yaml` (an
   exclusion is a scope decision, not a failure bucket — CONTEXT.md).
   Never skips a failed phase to finish CPD (ADR 0004: COMPLETE requires
   every engaged calculation converged).
6. **vasp-cache poison defense** — two layers:
   - crisp: `vasp_cache.api.put` must reject non-converged runs (route to
     `discarded_candidates` instead of `entries`);
   - vasp-sop: retry path refuses to materialize non-converged cache hits.
   (2026-08-08: a crashed run was cached, collided on composition-based
   identity across defect sites, and produced an infinite
   `vasp_crash` retry loop until manually purged.)
7. **`--retry-failed` retained** — unchanged semantics: the human reset
   channel for already-terminal directories (complements automatic retry,
   which only covers not-yet-terminal failures).

## Consequences

- Failed dirs converge or reach a visible, enumerated terminal state
  instead of hanging forever or dying silently.
- Persistent failures bound their compute cost; transient failures recover
  automatically.
- CPD integrity preserved (no silent phase skipping, no exclusion abuse).
- Two code layers touch: crisp (cache `put` convergence gate) and vasp-sop
  (classifier, loop wiring, blockers report).
- ADR 0007's "second failure is the answer" philosophy survives only for
  the persistent class; transient failures are exempt by evidence.
