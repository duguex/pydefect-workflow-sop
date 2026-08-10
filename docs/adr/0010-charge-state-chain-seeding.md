# Charge-state chain seeding for defect relaxations

Defect directories at different charge states of the same defect share a near-identical equilibrium geometry but were relaxed independently from the pristine defect structure (~100 ionic steps each, 94% of 2026-root defects have 3–7 charge states). We now submit a defect's charge states as an ordered chain — the median charge(s) first, then neighbors outward — and seed each subsequent charge's starting geometry from the nearest converged sibling's CONTCAR (geometry only: WAVECAR is charge-specific and never carried over, `ISTART=0`), cutting per-charge ionic steps to ~10–20 and the 2026-root ETA from ~25–30h to ~6–10h.

Seeding is safe for post-processing because pydefect's initial-structure reference is `defect_entry.json`'s `structure` field (with `defect_center`), not the on-disk POSCAR; only the on-disk starting geometry changes. Chain roots (median charge, two in parallel for even-length chains) always submit; a non-root charge submits once a converged sibling exists (its geometry source, nearest-charge preferred) or a sibling is terminal-failed (fall back to the pristine structure). A single first failure does not unlock the chain — only a terminal one does, so a root's one-shot retry (ADR 0008) still happens first.

## Considered options

- **Broadcast reuse** — any converged sibling seeds all remaining charges regardless of order. Rejected: the operator wants deterministic outward ordering; geometry similarity is highest between adjacent charge states, and a chain minimizes the worst-case distance to a source.
- **No reuse (status quo)** — every charge relaxes from the pristine structure. Rejected: ~80 wasted ionic steps per extra charge; convergence-rate gains from the NSW fix make the geometry the remaining bottleneck.
- **Seed from any finished (unconverged) sibling** — more sources, but risks propagating a bad geometry; convergence-only keeps the source semantics clean (extendable later).
- **Reuse WAVECAR too** — rejected on physics: NELECT differs per charge state, so a sibling WAVECAR cannot seed the electronic structure.

## Consequences

- Existing queued-but-undispatched defect jobs (crisp `submit`) are cancelled once and resubmitted under chain order (one-time operation; ~365 + 448 jobs across the two roots).
- Chain unlock delays a charge until its sibling converges; defects remain parallel across chains, so cluster utilization is preserved.
- A chain root that terminal-fails degrades the chain to pristine-structure starts (with unlock), never blocks it permanently.
- Seeds are recorded in JobStore as `source="seeded_from_<sibling>"`; POSCAR divergence from `defect_entry.json` is expected and harmless for analysis (post-processing reads the entry structure).


## 修订（2026-08-10）

播种只适用于**第一次提交**（JobStore 无历史）：后续任何重试（failed/unconverged/pending）一律 `restart_from_contcar`（从目录自己的部分收敛 CONTCAR 续，`ISTART=1`），直到收敛——不再重新从兄弟播种。动机：Va_Al3_-3 每次重提都从 Va_Al3_-2 重新播种，丢弃自己 100 步的部分收敛（ZBRENT 线搜索振荡 4 轮不收敛）。失败重提也不再依赖 `--retry-failed` 与 auto_retry 一次性限制。
