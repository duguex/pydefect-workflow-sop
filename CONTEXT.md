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

**重试风暴 (Retry storm)**:
A calculation directory resubmitted unboundedly because its failure is classified transient (SIGKILL/OOM/time-limit) while the root cause is persistent (insufficient memory, oversized cell). The retry state machine retries transient failures every cycle *by design*, so a genuinely permanent resource problem loops without bound — observed 274× (2025 `CaMg2(SO4)3/unitcell/band`) and 243× (2026 `Gd2GaSbO7:Bi/cpd`) — until a human or an OOM-specific detection interrupts it. The storm is the failure mode to detect, not the retry itself; bounded retries are the intended behaviour.
_Avoid_: 无限重试 (as if the retry policy were broken), calling every retry a storm

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

**形成能交互报告 (formation-energy interactive report)**:
The self-contained HTML page (`formation_energy_interactive.html`) written into a system dir by the report generator (`vasp_sop.report.interactive.generate_interactive_html`): one page embedding the CPD vertex selector and per-charge formation-energy plot as an interactive canvas widget. The CPD selector is a non-Euclidean 2D map of the target phase's stability region: geometry lives in the host-element subspace (impurity μ is a per-vertex branch, not a region dimension), drawn as the exact 2D polygon (3 host elements) or a spring-layout embedding of the 3D polytope (4+ hosts), with each boundary segment labeled by the competing phase that bounds it there (edge phase = intersection of the endpoint phase lists). Generated after defect analysis (analysis phase COMPLETE). Its visual language duplicates the crisp webui's design tokens (light theme) so the iframe render is indistinguishable from native webui chrome; the HTML remains self-contained (no external stylesheet fetch). **The file on disk is the webui's source, not a user-facing artifact** — `/reports` is the only consumption path; no CLI or documentation points end users at the local HTML.
_Avoid_: 交互图, FE widget, 报告页 (as the artifact), 离线报告 (the user no longer opens the file directly)

**形成能停靠读数 (formation-energy docked readout)**:
The readout docked over the chemical-potential-diagram card while the pointer is in the formation-energy plot: at the cursor's Fermi level it lists every currently visible defect in descending formation energy (highest first). Its surface never overlaps the formation-energy chart, so inspecting the chart never covers the data; it is sized to its content (width clamped 240–320 px, height capped at the viewport), is scrolled by wheeling over the chart, and hides when the pointer leaves the formation-energy plot (a touch tap docks/undocks it). Each row carries one tuple `(价态, μ)` — the defect ion's oxidation state (host-site valence + q for substitutions, q itself for vacancies, written 5+/2− style; host-site valences are inferred from the charge-neutral host formula) and the absolute value of the magnetization of that charge state, both flipping when the cursor crosses a charge transition. It is an inspection surface, never an additional thermodynamic calculation — hiding defects from it never alters the intrinsic charge-neutrality Fermi level.
_Avoid_: tooltip (a cursor-following overlay), inspector (a separate side column — the retired right-hand panel)
_Avoid_: inspector (the retired fixed right-hand panel), tooltip (when meaning a transient few-row peek)

**参考相（Reference phase）**:
The phase declared by `plan.yaml` `project.poscar_src` as the host that all supercell/perfect/defect structures derive from. **It must be the formula's polymorph with the lowest `e_above_hull` on the MP convex hull** — never the first MP formula-search result (`_docs[0]`). Distinct from the *competing phase set* (cpd): cpd phases are an independent thermodynamic set; the reference phase is the defect host. When the reference phase is not hull-stable, its `e_above_hull` must be recorded, not silently ignored.
_Avoid_: 母相 (ambiguous), poscar_src (the config key, not the concept)

**宿主身份（Host identity）**:
The shared parent-structure **topology** of a system's supercell, perfect, and defect structures — not its formula, lattice metric, or space-group symbol. Two structures with identical composition, near-identical cell metrics, and even the same space group can have different host identities (Y₂Ti₂O₇: P2 near-tetragonal 88-atom cell vs Fd-3m pyrochlore; BaAl₄O₇: two Pnma arrangements). Host identity is judged by StructureMatcher (structure match, not md5 or cell metrics). A defect set is only thermodynamically meaningful on its declared host identity; results are void when the host is later shown to be the wrong polymorph.
_Avoid_: 结构相同 (based on metric/SG), mp-id equality

**电荷态 (Charge state)**:
A defect's net charge q — the `_q` suffix of a defect directory (`Va_O1_1` is the +1 charge state of V_O). Each charge state is a separate calculation directory with its own formation energy and magnetization; the formation-energy plot is per charge state, and the docked readout shows each defect at its lowest-formation-energy charge state for the cursor's Fermi level. The row's 价态 label is the defect ion's oxidation state derived from it — host-site valence + q for a substitution X_Yn, q for a vacancy — flipping when the cursor crosses a charge transition. Host-site valences come from the charge-neutral host formula (O fixed at −2), never from a species table.
_Avoid_: 氧化态表, 空位形式电荷 (retired readout labels — static chemistry that never varies with the Fermi level)

**磁矩 (Magnetization)**:
The total magnetic moment (signed, in μB) of a converged defect calculation, read from the directory's `calc_results.json` `magnetization`. A property of a charge state, not of a defect: the docked readout shows the absolute value of the magnetization of the defect's stable charge state at the cursor's Fermi level, as the second element of the row's `(价态, μ)` tuple. It is an observable — values like 0.57 / 1.16 / 1.73 / 2.31 μB (sign ignored in the display) do not map to half-integer spin S in these hosts.
_Avoid_: spin state, 自旋态 (as a half-integer S label), 磁矩数组 (the per-atom magnetization array)

**两阶段 DFT+U（stage1/stage2）**:
The two-phase Hubbard-U protocol (ADR 0025): a directory containing U-table elements relaxes twice — **stage1** runs with `LDAU = False` (spin segment ISPIN=2/MAGMOM kept; no-U coarse geometry optimization, energies never enter formation energies) — and once stage1 converges, **stage2** (`apply_final_protocol`) flips `LDAU` back to `True` (adding LSORBIT when the system needs SOC) and continues relaxing from the stage1 CONTCAR; stage2 energies are the ones that enter the CPD hull and defect formation energies (wave3 hard gate: no analysis until every relaxation leg reaches final protocol). stage1 keeps the LDAUL/LDAUU rows and only disables the `LDAU` value, so stage2 re-enables by flipping one tag; the physical pending test (`_stage2_pending`, `_final_protocol_pending_dirs`) checks the LDAU *value* via `protocol.ldau_enabled`, never row presence. Single-point legs (band/dos/dielectric) are generated with U and no SOC and do not participate.
_Avoid_: 一遍带 U (a one-pass +U relaxation that skips stage1), LDAU 行存在即最终协议 (row presence is not enablement — `LDAU = False` still needs stage2)
