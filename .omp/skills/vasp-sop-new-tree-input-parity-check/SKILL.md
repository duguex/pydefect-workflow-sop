---
name: vasp-sop-new-tree-input-parity-check
description: vasp-sop 新树 bootstrap 后输入一致性体检：cpd_excluded_phases.yaml 是否存在（lattice25Å 相）、defect/perfect 与 cpd 主相 ENCUT 是否一致（API 裸 ENMAX vs CLI 1.3×ENMAX）、ISMEAR=-5 相 KPOINTS 不可约 k 点是否 ≥4、JobStore 是否残留 submitted 卡重提。用于新体系/重建树建好后、或相位推进卡住时。
---

# vasp-sop 新树输入一致性体检

新树 bootstrap 完成后、或相位推进卡住时，先跑四项体检再排障（2026-08-14 三棵重建树全部踩过）。

## 1. cpd_excluded_phases.yaml（系统根，非 cpd/ 内）

lattice >25 Å 的相提交时被跳过（"Lattice too large ... skipped"），但相位推断仍把无 OUTCAR 的相当未收敛，卡 COMPETING→CHEM_POT_DIAGRAM。旧树有此文件，bootstrap 不会自动生成——**必须手动补**。

```bash
# 找出所有 >25 Å 的相
/home/duguex/vasp_sop/.venv/bin/python -c "
import numpy as np
from pathlib import Path
for pd in Path('SYSTEM/cpd').iterdir():
    if not pd.is_dir(): continue
    lines=(pd/'POSCAR').read_text(errors='ignore').splitlines()
    lat=np.array([[float(x) for x in l.split()] for l in lines[2:5]])
    mx=max(np.linalg.norm(v) for v in lat)
    if mx>25: print(pd.name, round(mx,1))
"
# 写入 SYSTEM/cpd_excluded_phases.yaml（YAML 列表，可子串）
# - Ba4Fe4O11_mp-757712
```

验证：`System(SYSTEM).derive_phase(js)` 应推进出 COMPETING。

## 2. ENCUT 双路径不一致（已知问题，2026-08-14 确认）

- cpd/主相走 CLI 路径：`1.3×ENMAX`（如 O ENMAX=400 → 520）
- defect/perfect 走 vise API 路径（cutoff_energy=None）：**裸 ENMAX（400）**——缺 1.3 因子

后果：验收门「E_perfect 每 f.u. vs cpd 主相 ±0.05 eV」全挂（实测 −135~−504 meV/f.u.）。体检时对比：

```bash
grep -E "^ENCUT" SYSTEM/cpd/MAIN/INCAR SYSTEM/defect/perfect/INCAR SYSTEM/defect/*_*/INCAR | head
```

不一致即记录待修（用户裁决：先算完再修，不静默合并）。

## 3. ISMEAR=-5 相的 k 点充足性

BZINTS "number of k-points < 4" 的两种根因：
- KPAR>1 把不可约 k 点分多组（每组 <4）→ 改 KPAR=1
- 网格本身太稀 + VASP ISYM 高对称约化（spglib symprec=1e-5 说 8 个，VASP 约化到 3）→ **加密 KPOINTS**（如 3×2×2 → 4×3×3，保持 ISMEAR=-5 与 shift）

判定 VASP 实际 k 点数：看失败 slurm log 的 BZINTS 行尾数字（"number of k-points < 4) 3" 的 3 就是实际数）。修复后验证：log 出现 DAV 行即过。

## 4. JobStore 残留 submitted 卡重提

删目录重建后，job_history 最新记录仍是 submitted（seeded_from_*/restart）→ wave2 `if latest == "submitted": continue` 跳过 → 目录永远不重提（crisp 0 任务 + JobStore submitted = 死锁特征）。重置：

```python
from vasp_sop.core.job_store import JobStore
js=JobStore(Path.home()/'.vasp_sop'/'jobs.db')
js.record(str(dir.resolve()), 'failed', source='stale_submitted_reset', reason='...')
```

loop 下 cycle 自动重提（failed 在重提分支）。⚠️ 若 loop 长期运行，verdict 进程内缓存可能仍判旧状态——重启 loop 清缓存（systemctl --user restart vasp-sop-loop.service）。

## 5. 收敛判据纪律（避免误判）

- **权威判据 = `convergence_verdict(path).converged`（force gate: max_f < EDIFFG）**，不是 grep "reached required accuracy"——TIME LIMIT 截断但力已达标的运行没有该字符串却是真收敛
- JobStore backfill 的 converged 也可能误标——排查时直接调 convergence_verdict，别信字符串/记录
