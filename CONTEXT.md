# vasp-sop

An orchestrator for high-throughput VASP point-defect calculations: it receives prepared project trees, drives the VASP calculation waves via crisp, and post-processes with pydefect into defect formation-energy results.

## Language

**System**:
One material's prepared project tree, run through the pipeline from structure optimization to complete defect analysis.
_Avoid_: project (when meaning the runnable unit), calc tree

**Phase**:
A stage in a system's lifecycle: structure optimization, competing-phase set, chemical potential diagram, unit-cell defect, complete. COMPLETE requires every calculation directory on disk to have converged and the full defect summary to exist — a dir that ran and failed keeps the system in unit-cell defect (ADR 0004). Phases are always derived from the filesystem (`derive_phase`); there is no persisted phase memory (ADR 0011 supersedes ADR 0001).
_Avoid_: state, step

**Wave**:
One of the three scheduled submission rounds that move a system through its phases.
_Avoid_: batch, round

**Convergence verdict**:
The single authoritative answer to whether a VASP calculation is converged, with the reason it reached (or failed) that conclusion.
_Avoid_: converged flag, check result

**Stalled relaxation**:
A relaxation whose ionic force progress has stopped improving between consecutive evaluations.
_Avoid_: frozen job, hang detection

**Chemical potential diagram (CPD)**:
The phase-boundary diagram from which a defect's formation-energy chemical potentials are read.
_Avoid_: phase diagram (when meaning the CPD specifically)

**Competing phase set**:
The set of phases considered when building a system's chemical potential diagram.
_Avoid_: competitor list

**calc_results**:
pydefect's per-calculation result record, including whether the calculation ionically converged; the currency of defect post-processing.
_Avoid_: results json

**Result reuse**:
The capability of manually admitting a converged structural-relaxation result
and using its CONTCAR to prefill a later normal run. Owned by crisp (`crisp
cache`, wrapping the `vasp-cache` library); vasp-sop never reads or writes the
result store and never skips a submission because of a cache entry.
_Avoid_: vasp-cache (as a vasp-sop concept), results cache, cache lookup

**Calculation state**:
The JobStore record of one calculation directory (`submitted` / `converged` / `failed`), pipeline accounting that may lag the disk or outlive a deleted directory. Never a source of truth for "is this calc done" — that is the convergence verdict's job.
_Avoid_: status, done flag

**Status table**:
`vasp-sop batch status`'s per-system view. D/T columns are disk truth — the convergence verdict over every directory on disk; Run counts live `submitted` records; % is completed-dirs-over-all-dirs (100% when the defect summary exists). The crisp webui progress view consumes the same verdict-based disk truth for its 已收敛 column (routes_progress) — one authority, two views.
_Avoid_: progress report (the old `batch progress` command)

**Excluded phase**:
A competing phase deliberately removed from a system's calculation set via `cpd_excluded_phases.yaml` (issue #93), because it is not worth computing at all — out of project scope or known-irrelevant. An exclusion is a scope decision, never a record of convergence difficulties: a phase that ran and failed must be re-run or fixed, not excluded.
_Avoid_: failed phase, skip list for failures

**Chemical-environment system**:
A system whose `plan.yaml` declares `scope: chemical-environment`: competing phases and the chemical-potential diagram only — no unit-cell or defect calculations. COMPLETE is reached when the CPD is done (ADR 0005); the batch loop never builds or submits UC/defect legs for it.
_Avoid_: non-defect system, CPD-only flag

**Production testbed**:
The production system tree that exists to exercise and validate vasp-sop itself. Each system is a validation case: completing one is evidence the tool works — never a goal in itself. An operation on the testbed is either an existing tool capability or a discovered gap that becomes one (ADR 0006).
_Avoid_: production job queue, deliverables

**Stale record**:
A calculation-state entry that says `submitted` for a directory whose job is no longer running — disk or crisp already has the truth. Repairing it is *reconciling*; an un-reconciled stale record deadlocks progress because the machine skips dirs it believes are still running (ADR 0006).
_Avoid_: stuck job, ghost entry

**Block reason**:
Why one calculation directory is not done, as reported by `batch blockers`: missing inputs, failed (crashed), unconverged, terminal, never ran, or live. One directory, one reason; the tool cannot claim to automate what it cannot enumerate (ADR 0007, extended by ADR 0008).
_Avoid_: 卡住原因, issue

**Auto-rerun**:
The one-shot retry policy of ADR 0007, **superseded by ADR 0008** (classified retry). A failed or unconverged *defect* directory is resubmitted exactly once by the machine, marked `auto_retry`; a second failure is terminal forever. The machine never retries beyond one shot, never touches CPD phases after the persistent gate, and never decides exclusions. _Historical term — new code speaks of failure classes and the retry state machine._
_Avoid_: 无限重试, resubmission loop

**Retry state machine**:
The automatic resubmission policy (ADR 0008): the long-running batch loop tracks failed calculation directories and resubmits them by *failure class* until they converge or become *terminal*. One unified loop serves every batch root (ADR 0009). It replaces the ADR 0007 one-shot rule.
_Avoid_: auto retry, 自动重试

**Failure class**:
How a failed calculation directory is categorized at the retry decision point, parsed from crisp's raw diagnostics (`.failed`, `{jobid}.log`) by vasp-sop. **Transient** failures (SIGKILL, time limit, submit/network errors) are retried every loop cycle — cluster recovery makes them self-healing. **Persistent** failures (ZBRENT, force-gate) are physics answers, retried a bounded number of times (default 2, configurable) before going *terminal*.
_Avoid_: crash reason, 失败类型

**Terminal**:
A calculation directory that has exhausted its retry budget and will not be automatically resubmitted again. Recorded as calculation state `failed` with `reason=terminal:<class>`; surfaced by `batch blockers` for human decision (retry via `--retry-failed` / `batch retry`, fix inputs, or exclude — an exclusion is a scope decision, never a failure bucket).
_Avoid_: dead, 放弃, gave up

**Input restore**:
Making a directory runnable again by restoring its missing inputs — POTCAR from the local PSP store, keyed by POSCAR species — so a never-ran or input-stripped directory becomes `input_ready` (ADR 0007). Restoring inputs does not by itself decide whether the calculation should run.
_Avoid_: fix inputs, 补输入

**Batch root**:
One project tree handed to the batch loop (`batch run <root>…`). Roots are ordered: the list's left-to-right order sets each root's dispatch priority — systems under earlier roots submit before later ones. One root alone is the legacy single-project loop.
_Avoid_: project, tree, 根目录

**Unified loop**:
The single batch-loop process that serves every batch root (ADR 0009): it collects systems across all roots, advances them in one cycle, and writes one log/snapshot under the first root. One process eliminates the cross-root JobStore write-lock contention and the duplicated `_restore_crisp_active` submissions that two per-root loops produced.
_Avoid_: 多 loop, per-root loop

**Dispatch priority**:
A crisp job attribute (integer, default 0) controlling daemon dispatch order: higher values dispatch first, then created-at FIFO. vasp-sop derives it from the job's batch root; the daemon never hardcodes project paths. Strict priority means the daemon exhausts higher-priority jobs before dispatching any lower-priority one (ADR 0009).
_Avoid_: 优先级, queue rank

**Charge-state chain**:
The ordered submission plan for one defect's charge states (ADR 0010): the median charge(s) submit first, then neighbors outward one layer at a time. A non-root charge submits only when a converged sibling exists (its geometry source) or a sibling is terminal-failed (pristine-structure fallback); a first failure does not unlock the chain, so the root's one-shot retry (ADR 0008) still happens first. Chains run in parallel across defects, so cluster utilization is preserved.
_Avoid_: 价态链, broadcast reuse, charge group

**Chain root**:
The charge state(s) a chain starts from — the median charge for odd-length ranges, the two middle charges (submitted in parallel) for even-length ones. Roots always submit and are the only charges with no sibling prerequisite.
_Avoid_: 起点价态, seed source

**Seeded geometry**:
The starting structure of a defect directory that was replaced by a converged sibling's CONTCAR (charge-state chain, ADR 0010). Geometry only — the WAVECAR is charge-specific and never carried over (`ISTART=0`); pydefect post-processing is unaffected because it reads the initial structure from `defect_entry.json`, not the on-disk POSCAR. **Seeding applies only to the first submission** (no JobStore history); any later retry restarts from the directory's own partial CONTCAR (ADR 0010 rev 2026-08-10) — re-seeding on every retry discarded hundreds of partially relaxed ionic steps.
_Avoid_: 播种, CONTCAR reuse, 复用结构

**Defect name**:
pydefect's directory-name scheme: `Va_Xn` = vacancy, `X_Yn` = substitution/antisite (X replaces the atom on site Yn — composition changes by −Y +X, verified from `defect_entry.json`), `X_iN` = interstitial (X at interstitial site N). The charge state is appended as `_q`. `X_Yn` with a real host site is never an interstitial; the two are frequently confused because both read "X near Yn".
_Avoid_: 间隙位 vs 反位混用, reading `O_Ga1` as an O interstitial

**Anion-cation antisite**:
A single substitution `X_Yn` where exactly one side is an anion-role element (O/S/Se/Te/F/Cl/Br/I/N/P) — an anion on a cation site or a cation on an anion site. Excluded from the defect set at the directory validity gate (ADR 0013): never submitted or analyzed, but left on disk. Metalloids (Sb, Ge) count as cations in these oxide hosts. Complex defects and metal↔metal substitutions are unaffected.
_Avoid_: 阴-阳错位, 反位全筛
**Cluster tag**:
A label on a crisp cluster (`clusters.json` `tags`) that gates which clusters a job may dispatch to (`--tag long` = only clusters carrying `long`). Today cluster-level only: `duguex_113/101` = `short`, `ckduan_167/duguex_5` = `long`. Deliberately *not* moved to partition level (crisp_light#137 kept open, decision 2026-08-10); `duguex_101`'s 1917 idle nodes (CPU-* partitions) stay unconfigured by decision — the `test` partition remains the only submittable queue on 113/101, so submit→start queueing of hours is an accepted constraint.
_Avoid_: calling the `test` partition "the short queue" as if it were a time limit — `qos_test` has not killed any job (no TIME-LIMIT observed); queueing comes from capacity, not from the tag.

**CPD phase refresh**:
The competing-phase set must cover every element in the defect chemistry — intrinsic elements plus dopants (ADR 0015). `ensure_cpd_phases` compares `cpd/mp_state.json` `elements` (recorded at fetch time) against the current plan; on mismatch it fetches the new phase set into a temp dir, moves only new dirs in (existing converged phases untouched), submits them, and rewrites mp_state.json. Fixes the failure mode where a dopant is added to plan.yaml after cpd was fetched — `standard_energies.yaml` then lacks the dopant's chemical potential and pydefect `dei` crashes (`KeyError` on formation-energy composition).
_Avoid_: 手动补 standard_energies, cpd 全量重建

**Electronic convergence gate**:
The convergence verdict refuses an OUTCAR that contains VASP's NELM-exhaustion warning (`increasing NELM`/`spurious results`) — VASP can print "reached required accuracy" on a false positive when the last electronic step hit NELM and the forces happen to fall below EDIFFG, but the energy is unreliable (ADR 0016). This aligns vasp-sop's verdict with pydefect's `electronic_conv` (read from vasprun scsteps). The warning can sit MBs before EOF, so the check falls back to a full-file scan with an mtime cache.
_Avoid_: 只信 "reached required accuracy", 忽略 NELM 警告
