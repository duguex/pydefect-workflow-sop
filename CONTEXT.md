# vasp-sop

An orchestrator for high-throughput VASP point-defect calculations: it receives prepared project trees, drives the VASP calculation waves via crisp, and post-processes with pydefect into defect formation-energy results.

## Language

**System**:
One material's prepared project tree, run through the pipeline from structure optimization to complete defect analysis.
_Avoid_: project (when meaning the runnable unit), calc tree

**Phase**:
A stage in a system's lifecycle: structure optimization, competing-phase set, chemical potential diagram, unit-cell defect, complete. COMPLETE requires every calculation directory on disk to have converged and the full defect summary to exist — a dir that ran and failed keeps the system in unit-cell defect (ADR 0004).
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
The capability of answering "has this calculation been run, what was its result" for previously-computed calculations. Owned by crisp (`crisp cache`, wrapping the `vasp-cache` library); vasp-sop never touches the result store — crisp caches completed results and materializes cached outputs back into the worktree.
_Avoid_: vasp-cache (as a vasp-sop concept), results cache, cache lookup

**Calculation state**:
The JobStore record of one calculation directory (`submitted` / `converged` / `failed`), pipeline accounting that may lag the disk or outlive a deleted directory. Never a source of truth for "is this calc done" — that is the convergence verdict's job.
_Avoid_: status, done flag

**Status table**:
`vasp-sop batch status`'s per-system view. D/T columns are disk truth — the convergence verdict over every directory on disk; Run counts live `submitted` records; % is completed-dirs-over-all-dirs (100% when the defect summary exists).
_Avoid_: progress report (the old `batch progress` command)