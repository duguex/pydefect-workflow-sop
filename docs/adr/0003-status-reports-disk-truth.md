# Status tables report disk truth; JobStore is accounting only

`vasp-sop batch status` D/T columns count **directories on disk** (cpd/
phases, unitcell tasks except structure_opt, defect dirs) with the
**convergence verdict** as the numerator. The Run column counts JobStore
`submitted` records whose directory still exists. The JobStore itself is
never a source of truth for "is this calculation done" — only for "is a
job believed to be in crisp's queue".

We chose this over "D/T counts JobStore records" because the record-based
count silently misreported progress in three ways found on the production
tree (2025_undergo_spin_defect): directories that were never submitted
did not enter the denominator at all (a system could read "CPD 5/5" while
seven phase dirs sat unrun), records for deleted directories inflated the
denominator (a deleted competing phase kept a 14/16 table from showing
the true 14/15), and disk-converged-but-unrecorded directories were
under-counted. `batch progress` already used disk truth; the two tools
disagreed by construction, so they were merged into one `batch status`.

Costs: status now pays an OUTCAR parse per directory, which the
persistent verdict memo (mtime-keyed sidecar at `~/.vasp_sop/
verdict_cache.json`) amortizes across invocations. `batch history --prune`
removes stale JobStore records (explicit, non-automatic). JobStore
records are intentionally not deleted automatically: they are the audit
timeline.