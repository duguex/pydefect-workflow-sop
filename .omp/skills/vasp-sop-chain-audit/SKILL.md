---
name: vasp-sop-chain-audit
description: "Audit vasp-sop charge-state chain compliance: classify defect queue by chain role (root/seeded/waiting), verify seeding with md5, catch violations."
---

# vasp-sop Charge-State Chain Compliance Audit

Audit that every defect calculation obeys the chain-seeding rule (ADR 0010): non-root charges are either seeded from a converged sibling's CONTCAR or waiting; chain roots (median charge) submit unconditionally; terminal-failed siblings do NOT unlock the chain (operator repairs via `batch retry`).

## When to use
User asks "还有多少中位在算" / "现在算的都是中位吗" / "满足条件的都提交了吗", or after any cancel/reset wave to verify the queue is compliant.

## Audit script skeleton
```python
Q = re.compile(r"_(-?\d+)$")           # charge suffix
# group = Q.sub("", name)               # Va_Gd1_-3 → Va_Gd1 (complex keeps +)
# roots = median of group charges (two middle for even count)
# conv(d): OUTCAR contains b"reached required accuracy"
```

## Correctness traps (each cost a debug cycle)
1. **active 判定**: use ONLY crisp `jobs` latest status in `('submit','submitted','running','ready_fetch')`. Never JobStore `submitted` history — old records make every dir look active (1126 false violations).
2. **播种判定**: compare `md5(POSCAR)` vs `md5(sibling/CONTCAR)`. mtime comparison LIES — `shutil.copy2` preserves source mtime, and a later run of the source dir updates its CONTCAR mtime.
3. **conv 判定**: `convergence_verdict` reads a 256KB tail window; huge OUTCARs (2MB+) can put "reached required accuracy" beyond it → false unconverged. Verify with full-file grep when in doubt.
4. **JobStore source semantics**: `seeded_from_<sib>` = wave2 seed; `reason="restart,prev_f=…"` + POSCAR md5 match = poll seed (legal, source is task_name not seeded_from); `restored` = loop-restart sync, not a submission; `chain_wait` = waiting (legal).
5. **running 旧作业** (pre-restart era): let them finish — poll handles them on completion (seed if sibling converged, else chain_wait).
6. **等链的**: non-root + no converged sibling + no crisp job = CORRECT waiting (1399 of them is normal mid-chain).

## Queue breakdown by role
Classify each crisp-active defect dir: 链根(中位) / 播种 / 等链 / 存量异常. Submit-state violations (non-root, non-seeded) → `crisp cancel --name <task>` and wave2 re-handles them next cycle (seeds or waits). Never cancel running/submitted (let finish).

## Verification evidence
- Seeded POSCAR must byte-match sibling CONTCAR (md5) and INCAR must have `ISTART = 0`, no WAVECAR.
- After cleanup: violations should be 0; queue = roots + seeded + in-flight-legacy only.
