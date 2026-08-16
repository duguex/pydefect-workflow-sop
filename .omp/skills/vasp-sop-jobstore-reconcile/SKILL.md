---
name: vasp-sop-jobstore-reconcile
description: "Reconcile vasp-sop JobStore state against disk truth: find stale converged/submitted records, classify, batch-retry reset, and verify chain-seeding submissions. Use when jobs aren't submitting, the webui shows stuck tasks, or after mass cancel/reset operations."
---

# JobStore ↔ disk reconciliation (vasp-sop)

Diagnose "tasks not submitting / stuck forever" by comparing JobStore (`~/.vasp_sop/jobs.db`) against disk truth (OUTCAR convergence). The batch loop TRUSTS JobStore: `latest == "submitted"` skips submission, `latest == "converged"` skips forever — stale records deadlock progress.

## Key facts (learned the hard way)

- **Mass `crisp cancel --status submit` does NOT update JobStore.** Cancelled dirs keep `submitted` in JobStore → wave2 skips them. Must follow with `vasp-sop batch retry` (per root) to reset to `pending`.
- **Stale `converged`**: JobStore says converged but disk has no OUTCAR or unconverged OUTCAR (supercell switchover rebuilds dirs; 7月 backfill records). wave2 skips them forever. Reset via batch retry — but see the chain caveat below.
- **`reconcile`/wave2 backfill re-promotes dirs whose OUTCAR looks converged**: after resetting, a dir with an old-but-converged OUTCAR gets re-marked converged (correct if disk truly converged). But dirs with stale `calc_results.json` (from an earlier era) can be promoted even when the current OUTCAR is unconverged — delete the stale post-process products (`calc_results.json`, `correction.json`, `defect_structure_info.json`, `defect_energy_info.yaml`, `band_edge_*.json`) for unconverged dirs before resetting.
- **Convergence verdict window**: `convergence_verdict` reads a tail window of OUTCAR (now 256K; was 8K). Long runs can place `reached required accuracy` >64KB before EOF — check with a full-file grep before trusting a False verdict on big OUTCARs.
- **Charge-state chain (ADR 0010)**: non-root charge states wait for a converged sibling. "Unsubmitted" non-root dirs without a converged sibling are CORRECTLY waiting — don't reset them. Only chain roots submit unconditionally.

## Procedure

1. **Snapshot the gap** — for all defect dirs with INCAR:
   - disk converged = full-file grep `reached required accuracy` in OUTCAR (64KB tail may lie)
   - JobStore latest (ORDER BY timestamp DESC LIMIT 1 on `job_history`)
   - crisp active = `SELECT status FROM jobs WHERE local_dir=? AND status IN ('submit','submitted','running','ready_fetch')`
2. **Classify problems**:
   - `converged` in JobStore but NOT disk-converged → stale → reset list
   - `submitted` in JobStore but no crisp active (cancelled) → stale → reset list
3. **For stale dirs**: if not disk-converged, delete stale post-process products (calc_results etc.) so reconcile can't re-promote. If disk-converged, keep it: re-run `pydefect_vasp cr` if calc_results missing and mark `converged` again.
4. **Reset**: `xargs -a list.txt .venv/bin/vasp-sop batch retry <root>` — split by root; path args relative to root.
5. **Restart loop** (`systemctl --user restart vasp-sop-loop`) so new code loads, then verify next wave2 round.
6. **Re-audit**: rerun step 1; expect zero "should-submit-but-not" among UNITCELL_DEFECT systems (exclude COMPETING/COMPLETE phases — defect submission there is phase-correct). Non-root waiting dirs are fine.

## Chain-seeding verification (ADR 0010)

- Seeded dir: POSCAR == sibling's CONTCAR, `ISTART = 0`, no WAVECAR; log line `seeded geometry from <sibling> (ADR 0010)`.
- Chain roots: median charge (two in parallel for even count).
- Wait condition applies ONLY to never-run dirs (no OUTCAR) — has-OUTCAR non-roots submit directly.
