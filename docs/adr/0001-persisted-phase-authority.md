# Persisted phase is authoritative over filesystem inference

**Superseded by [ADR 0011](0011-disk-single-source-of-state.md)** — disk is the single source of state; phase memory removed.

# Persisted phase is authoritative over filesystem inference

A system's pipeline phase is persisted to `state.json` on every advance, and the persisted phase wins over filesystem re-inference on each poll. Inference remains the fallback only when `state.json` is absent or corrupted, and the phase-gate audits (empty `target_vertices.yaml`, missing `standard_energies.yaml`) still guard downstream transitions.

We chose this over "inference is always authoritative" because the filesystem is ambiguous: once the chemical-potential diagram has written its targets, the same file states recur during later phases, and re-deriving from disk made the machine's memory unreliable across restarted loops. The trade-off is that a genuine filesystem regression underneath a stale marker will not self-correct — the marker is the memory, not a cache.