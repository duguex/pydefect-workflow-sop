---
name: vasp-sop-regenerated-cpd-not-submitted
description: "诊断 vasp-sop 体系 cpd 输入重生成后从未重提：三步验证相位锁定（target_vertices.yaml）、SOC 协议（stage2_soc 剥 LSORBIT 且 cpd 无补 SOC 腿）、续算几何保留（CONTCAR==POSCAR 语义）。用于\"cpd 重生成后没跑/输入新了为什么没提交/37 个 cpd 不动\"类问题。"
---

# vasp-sop cpd 输入重生成后未重提诊断

触发：cpd 目录 INCAR/POSCAR 已重生（mtime 新）但 24h+ 无作业记录、无 OUTCAR、loop 每轮也不提交。

## 症状确认

```bash
R=/mnt/.../2026_undergo_spin_defect/<sys>/cpd
ls $R/<phase>/INCAR $R/<phase>/OUTCAR   # INCAR 新、OUTCAR 无
sqlite3 ~/.crisp/data/agent.db "SELECT status, COUNT(*) FROM jobs WHERE local_dir LIKE '%<sys>%cpd%' GROUP BY status;"
```

## 三步验证

### 1. 相位锁定（最常见根因）
```bash
ls $R/target_vertices.yaml   # 存在 = 相位 ≥ CHEM_POT_DIAGRAM，永不回 COMPETING
```
- `_infer_phase_locked`（core/system.py）显式 gate：target_vertices.yaml 存在 → 永不返回 COMPETING（ADR 0011 磁盘权威）
- cpd 提交只在 COMPETING 分支的 `competing_dirs` 执行 → **相位过了 = cpd 结构性永不重提**
- 947d258 stale-converged 修复同样只在 competing_dirs（COMPETING 腿）——救不了
- 解锁（用户决策）：删 target_vertices.yaml + standard_energies.yaml 回相位（CPD 图需重算）/ 补程序补算腿 / 保持

### 2. SOC 协议缺口
```bash
grep -E "soc|stage2" <sys>/plan.yaml
grep -c LSORBIT $R/<phase>/INCAR    # stage2_soc:true 时 = 0
```
- `_apply_soc_tags`（vasp/io.py）：stage2_soc=True → 所有任务跳过 LSORBIT（stage1 语义）
- stage2 补 SOC 腿（orchestrator ~772 行）**只遍历 df_root（defect）**，cpd 无补 SOC 机制
- 直接提交无 SOC cpd → 化学势图无 SOC，与 defect 不一致，形成能全错
- 解锁（用户决策）：补 cpd stage2 腿 / cpd 恢复全 SOC 单跑

### 3. 续算几何是否保留
```bash
cmp -s $R/<phase>/CONTCAR $R/<phase>/POSCAR && echo "same"
stat -c %y $R/<phase>/CONTCAR $R/<phase>/*.log
tail -2 $R/<phase>/*.log   # CRISP_COMPLETED = 旧作业收敛过
```
- **CONTCAR==POSCAR 内容相同 = 重生成从旧 CONTCAR 读结构写新 POSCAR**（续算几何保留，方向是 CONTCAR→POSCAR，不是反过来）
- 清 OUTCAR 是重跑触发的必要机制（verdict 读 OUTCAR；旧 OUTCAR 在 = 磁盘"已收敛" = 不重跑）
- 旧 log/XDATCAR/submit.slurm 保留 = 旧作业历史在

## 报告格式

逐目录状态表：POSCAR/INCAR/KPOINTS/POTCAR/CONTCAR/OUTCAR/SOC/作业记录 + U/NELM 核对（cpd 协议 NELM=50、Gd 相 LDAUU=5）。区分"续算就绪"与"全新输入"（看 CONTCAR mtime 与内容对比）。
