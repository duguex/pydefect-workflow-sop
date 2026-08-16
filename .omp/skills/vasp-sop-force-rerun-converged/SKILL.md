---
name: vasp-sop-force-rerun-converged
description: 强制 vasp-sop 重算已收敛的缺陷/单元目录（参数修复如 +U/NSW 使旧结果失效时）：batch retry + 删除 VASP 输出绕过 wave2 磁盘收敛门。
---

# 强制重算已收敛目录（vasp-sop）

**触发**：输入参数修复（INCAR 参数如 +U/NSW/SIGMA）后，旧参数下已收敛（磁盘 OUTCAR 收敛）的目录不会自动重算——wave2 提交逻辑有磁盘收敛门（`convergence_verdict(child).converged` → 跳过提交），仅 reset JobStore 无效。

## 流程

1. **找出已收敛目录**（OUTCAR 尾部 64KB 搜 `reached required accuracy`）：
   ```python
   with open(outcar, 'rb') as f:
       f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz-65536))
       if b'reached required accuracy' in f.read(): converged.append(t)
   ```
2. **重置 JobStore**：`vasp-sop batch retry <root>`（对每个目录输出 `pending (next batch run will resubmit)`）
3. **删除 VASP 输出**（关键步骤，否则 wave2 仍跳过）：
   `OUTCAR OSZICAR vasprun.xml CHGCAR CHG WAVECAR DOSCAR EIGENVAL PCDAT PROCAR IBZKPT REPORT LOCPOT ELFCAR`
   保留输入（INCAR/POSCAR/POTCAR/KPOINTS）和 CONTCAR。
4. **等 loop 自动重提**（单 loop 每轮轮转 51 体系，Gd 案例 ~200s 内开始提交）。
5. **验证**：crisp agent.db `jobs` 表按 `local_dir` 取最新一条，status ∈ (submit/submitted/running) 计数。

## 注意

- 只删目标目录输出，**不要碰**正在 running 的作业目录。
- 如果 defect_energy_summary.json 存在，wave2 会整体跳过 defect 提交（gate 在根级）——那是另一个门。
- 新参数作业跑完（约 1-1.5h/作业）后用同一 OUTCAR 判定重测收敛率。
}
