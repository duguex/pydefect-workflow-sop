# ADR 0019 — Per-system git repositories for input/result snapshots

- Status: accepted
- Date: 2026-08-11
- Deciders: user (grill-with-docs rounds 1-3), agent

## Context

The 2026 batch (10 systems, ~1340 calc dirs, 9.3 GB on NFS) suffered a
state-consistency failure (issue #121): inputs regenerated outside the
pipeline left the JobStore disagreeing with disk, and nothing recorded
*what* changed in inputs or *when*. Drift detection (INCAR newer than
OUTCAR) answers "was it modified?" but not "modified into what?".

The user asked for git management of the calculation tree: input
auditability plus phase-boundary snapshots.

## Decision

1. **One git repository per system directory** (e.g. `BaAl2B2O7/.git`),
   `.git` inside the system root. Not one repo for the whole batch (user
   chose per-system isolation), not external bare repos.
2. **Tracked**: inputs (plan.yaml, INCAR, POSCAR, KPOINTS,
   defect_in.yaml, prior_info.yaml, vise_log.yaml, submit.slurm) plus
   result artifacts the user explicitly requested — **CONTCAR** (geometry
   snapshots) and **`*.log`** (slurm job logs).
3. **Ignored** (`.gitignore`, constant in `vasp_sop/core/git_snapshot.py`):
   POTCAR (regenerable from the PSP store, ADR 0007) and all binary/large
   outputs (OUTCAR, vasprun.xml, OSZICAR, CHG/CHGCAR/WAVECAR, ...). Their
   state is already tracked by verdicts, the JobStore and crisp — git
   would duplicate it at 25+ GB of continuously-changing blobs.
4. **Commit cadence**: the batch loop runs `git add -A` + commit per
   system every 30 cycles (~1h, `BatchOrchestrator._git_snapshots`),
   committing only when something changed; plus manual semantic commits
   during repair/parameter changes. Repos are initialised lazily with a
   baseline commit when first seen.
5. **Robustness**: git failures are logged, never raised — a git problem
   must not crash the batch loop. Repo-local identity
   (`vasp-sop <vasp-sop@localhost>`) so commits never depend on global
   config.
6. Drift detection stays as-is (git answers "what changed", mtime
   answers "did it change").

## Consequences

- Input history per system is auditable: `git -C <sys> log`, `git diff`
  between any two snapshots, blame on INCAR files.
- CONTCAR history records the geometry evolution of every calculation.
- Repository sizes stay small (inputs minus POTCAR + KB-MB text): ~tens
  of MB per system baseline; per-cycle deltas are tiny unless a CONTCAR
  or job log updates.
- `big_sc_bak/`, `output/`, `defect_generate_flag`, reports (*.pdf/html)
  are excluded — they are scratch/derived.
- NFS git caveats accepted: single writer (the loop plus the operator),
  no concurrent GC; repos live inside the NFS tree per user choice.
