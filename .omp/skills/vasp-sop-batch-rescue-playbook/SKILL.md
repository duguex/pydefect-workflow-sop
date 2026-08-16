---
name: vasp-sop-batch-rescue-playbook
description: "Rescue and drive vasp-sop batches (2026/2025) to completion: phase-regress unlock for cpd/stage2, stale-record retry, duplicate CPD resolution, soc2 electronic-fail handling with the NELM rule, force-gate recipe family, and hub-managed autonomous supervision. Use when asked to 推进计算/看进度/解锁卡死体系 or any batch below COMPLETE target."
---

# vasp-sop batch rescue → completion playbook

Drive a stuck vasp-sop batch (e.g. `2026_undergo_spin_defect`) to 10/10 COMPLETE.
Ground truth: `batch status` / `batch blockers /path`, loop logs under first root,
`~/.vasp_sop/jobs.db` (JobStore, dir_path/status/source), `~/.crisp/data/agent.db`.

## 0. Loop hygiene first
- Loop is a systemd user unit `vasp-sop-loop.service` (ExecStart = `vasp-sop batch run <root> --loop --poll 120`, all systems). It self-restarts. Its stale Description lied before — trust ExecStart, not Description.
- `batch run` single-instance lock is host-wide (SOP_ROOT/.batch_loop.global.lock) — a second in-line `--loop` refuses to start ("Another unified batch loop is already running"). Always use the systemd service, never a second loop.

## 0b. Priors (phase-scheduler structure, 2026-08-12 facts)
- cpd submit + cpd-stage2-SOC legs live ONLY in the COMPETING branch; defect-stage2 lives in the defect leg (COMPETING and UNITCELL_DEFECT). Systems past COMPETING structurally cannot resubmit cpd / stage2 → unlock by phase-regress.
- Two-phase SOC consistency: formation energy = E_dir − Σμ(hull). Defect SOC single points make E_dir SOC, so the cpd hull MUST be all-SOC (or all non-SOC). Mixed hull = invalid ("stage1/stage2 不可比") — stage2 supplements on one leg force supplements on the other (Y2Sn invalid until its 13 cpd SOC supplements ran).
- old-full-SOC cpd dirs (LSORBIT+converged, no soc_stage2 record) are equivalent to stage2 — **pre-mark them** with a JobStore record `source='soc_stage2'` before phase-regress, else `_stage2_soc_pending` re-runs them as useless SOC single points.

## 1. Classify blockers (never_ran vs unconverged vs stale)
Run `vasp-sop batch blockers <root>`. Categories:
- **never_ran in defect/unitcell**: check JobStore latest status; `source='restored'` + status `submitted` + no crisp job = STALE record deadlocking the submission leg (`latest==submitted → continue`). Fix: `vasp-sop batch retry <root> <rel dir...>` → next cycle resubmits. This is the #1 silent deadlock (ADR 0006).
- **never_ran in cpd AFTER phase advanced** (system in UNITCELL_DEFECT/COMPLETE but cpd missing): structural — cpd submission + stage2 SOC legs ONLY live in the COMPETING branch (orchestrator wave2). Fix = **phase-regress unlock**:
  ```
  mkdir -p <sys>/.phase_bak_<date>   # system root, NOT inside cpd/
  mv <sys>/cpd/{target_vertices,standard_energies,composition_energies,relative_energies,chem_pot_diag}.yaml* <sys>/.phase_bak_<date>/
  ```
  Loop next cycle re-infers COMPETING → cpd + stage2 legs fire → converges → CPD post re-runs → advances again. NEVER delete, always backup (recoverable). **Pre-mark old-full-SOC cpd dirs (see 0b) BEFORE regressing.**
  - **Sequencing rule**: phase-regress BEFORE supplements is wrong (re-runs CPD post on a mixed hull). Supplements first, then regress, then CPD post, then wave3. For a system past COMPETING with cpd all-converged, call `_submit_stage2_soc(child, sys, js, False, info_fn, priority=10)` directly for the 待补 dirs, wait for all convergence, then regress. For a system **mid-wave3** (analyze running), don't regress unless its hull is invalid (SOC/mixed) — that overrides "don't interrupt".
  - Regress exposes stale JobStore on the target dir too: if target has no OUTCAR (cleared by regen batch), phase returns STRUCTURE_OPT and wave1 resubmits the target — that's correct (fresh protocol energy), just slower.
- **unconverged force_gate_fail capped** (loop logs "N ionic restart(s) without convergence — auto-restart capped"): read the dir's slurm `*.log` FIRST — the ZBRENT/EDIFF signature is only there (#131). Then recipe family:
  - `ZBRENT: fatal error in bracketing` / `can't locate minimum` → patch `EDIFF=1e-6` (loop also auto-patches via `_has_zbrent_failure`; idempotent)
  - metallic near-gate (max_f < 0.02, flat energy) → `EDIFFG=-0.02`
  - slow but progressing at step 50 → `NSW=100`
  - mag pinned at integer + restart drift → `NUPDOWN=<observed total μB>` (e.g. Fe2B2O5=16) — NUPDOWN forces total spin, NOT MAGMOM (initial guess only)
  then `batch retry`. Per-dir, user-approved protocols.

## 2. Duplicate CPD compositions
`collect_cpd_phase_provenance` aborts CPD post on duplicate reduced formulas. Resolve by COMPARING PER-ATOM energy, never raw TOTEN (2× supercell vs primitive gives near-exact 2× TOTEN and will trick you):
```
n=$(awk 'NR==7{for(i=1;i<=NF;i++)s+=$i; print s}' <sys>/cpd/<dir>/POSCAR)
e=$(grep "free  energy   TOTEN" <sys>/cpd/<dir>/OUTCAR | tail -1 | awk '{print $5}')
python3 -c "print(f'{$e/$n:.6f}')"
```
Keep lower per-atom (even <1 meV/atom — skill rule), move the other OUTSIDE cpd/:
`mv <sys>/cpd/<higher> <sys>/.dup_bak_<date>/` (never inside cpd/ — preflight treats any cpd/* dir as a phase). Verify with the repo function: `collect_cpd_phase_provenance(<sys>/cpd)` for all systems → zero duplicates.

## 3. soc2 single-point electronic_conv=False (a.k.a. "correction missing")
Symptom: analyze partial, `efnv: no correction.json for X after run`, dir verdict is `not_relaxation conv=True` (NSW=0). Root: SOC single-point SCF did not converge within its NELM budget (charged antisites often exhaust NELM=30).
- **CRITICAL — OUTCAR string check lies**: a NSW=0 single point prints NO "reached required accuracy" even when converged (issue #125 family; across 187 dirs "no accuracy string" was ~all false positives; e.g. La_Sc1_0: slurm-log final dE≈1e-12, econv=True, no accuracy string in OUTCAR). The ONLY reliable checks:
  - `pydefect_vasp cr -d <dir>` (in the defect/ cwd) → read `calc_results.json .electronic_conv`
  - pymatgen rule: `converged_electronic = len(final_elec_steps) < parameters["NELM"]`
  - slurm log final `F= ... d E=1e-12..1e-23` = converged
- **False-positive trap**: `calc_results.json` is STALE if the dir was re-run (NELM bump / new fetch) — cr reads the old file. Fix: `rm <dir>/calc_results.json` then `pydefect_vasp cr -d <dir>` (one dir at a time — multi `-d` writes only the last); if the current vasprun is good (len(steps) < NELM in its `<parameters>`), econv comes back True. Verify vasprun: `grep '<scstep>' vasprun.xml | wc -l` + NELM in vasprun parameters.
- Genuine fails: patch `NELM=200` (keep LSORBIT + NSW=0), resubmit via crisp with soc_stage2 record (or `_submit_stage2_soc`). Bounded escalation — if NELM=200 still econv=False, re-run cr fresh before concluding (stale file is the 90% cause).
- Analyze auto-extracts cr for all defect dirs each cycle; most "missing correction" dirs with no calc_results.json are just NOT YET EXTRACTED (false positives) — let analyze run or `pydefect_vasp cr -d` to confirm.

## 4. INCAR protocol-strip detection
A resubmitted defect dir can silently run a "bare" INCAR (no SIGMA=0.02/LORBIT=11/NELM=30/+U). Detect by comparing against a converged sibling (`grep -oE '^(SIGMA|LORBIT|NELM|LDAU)\s*=\s*\S+' <dir>/INCAR`). If stripped, the loop's prepare_inputs skips re-gen when INCAR exists (input_ready gate) — regenerate by deleting INCAR or call prepare_inputs explicitly. Track as issue family (#132).

## 5. Autonomous supervision (hub-managed)
Loop process handles submission/poll/analyze/CPD-post. Edge classes it does NOT self-heal: soc2 electronic-fail (NELM), force-gate parameter patches, stale-record retry, electronic-fail extraction staleness. Pattern: a hub `start`-managed detached watcher script (see `/tmp/pr2026_supervisor.py`, `/tmp/watch_y2sn_stage2.py` onboarded in the 2026 rescue):
- poll every few minutes; read JobStore + crisp live set; skip dirs in live
- apply recipes 1-4 with a **ledger** (`/tmp/supervisor_applied.json`) so each dir gets each patch at most once (bounded; if still failing after one patch, log BLOCKER and stop — don't loop)
- never touch a dir whose job is in flight; never regress a system owned by another watcher
- exit when `batch status` shows 10/10 COMPLETE, or after N idle rounds with no live jobs (deadlock → stop, operator). Watchdog-stop only on ≥12 idle rounds with zero live jobs — never on a legit compute wait.
- ready-log for hub: first log line; log to /tmp/<name>.log
Two watchers must not both own the same phase regress (partition ownership). Watcher sequencing: wait for supplements converged (verdict + LSORBIT + not live), THEN move CPD artifacts (never before — re-regressing too early re-locks a mixed hull), verify `target_vertices.yaml` regenerated, verify `defect_energy_summary.json` mtime > target_vertices. ANY supplement failure → WATCH-STOP without touching hull.

## 6. Shared traps
- Jobs: "ready_fetch" backlog may be dominated by OTHER projects sharing crisp — don't mistake it for our queue.
- agent.db has historically lost submission rows (#124) — JobStore + disk are the authorities.
- Never delete JobStore/phase artifacts — backup to `.phase_bak_<date>`/ at system root.
- cluster cap 60; 2026 root gets crisp priority 10 (dispatch first).
- Exclusions (`cpd_excluded_phases.yaml`) are scope decisions, never failure buckets — never auto-write them.