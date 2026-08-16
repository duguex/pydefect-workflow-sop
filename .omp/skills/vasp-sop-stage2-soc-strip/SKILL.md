---
name: vasp-sop-stage2-soc-strip
description: 两阶段 SOC（ADR 0014）切换时 strip 所有未收敛目录的 LSORBIT/ISYM——漂移扫描只覆盖有 OUTCAR 的目录，未跑目录的 INCAR 会残留旧 SOC 标签导致 ZBRENT 崩溃。用户要求实施两阶段 SOC/去 SOC、或含 Bi 体系全并行推进时使用。
---

# 两阶段 SOC 切换：strip 未收敛目录（ADR 0014）

## 触发

- plan 加 `stage2_soc: true` 后全并行推进（含 Bi 体系）
- 任何"INCAR 去 SOC"的批量操作

## 核心坑（实测 2026-08-11）

**漂移扫描（INCAR mtime > OUTCAR mtime）只覆盖有 OUTCAR 的目录**——未跑过/无收敛结果的目录不在漂移列表，其 INCAR 残留旧批次（如 +U/SOC 13:30 批次）的 `LSORBIT`/`ISYM`。带 SOC 提交 → **ZBRENT 线搜索崩溃**（EXIT_CODE 1，209 次失败/40 目录，全部是含 Bi cpd）。

## 流程

1. **5 SOC 体系 plan 加 `stage2_soc: true`**（Gd2GaSbO7:Bi/La2SrSc2O7/La2Zr2O7/Y2Sn2O7/Y2Ti2O7）——`_apply_soc_tags` 已尊重该开关（新生成不再加 SOC）。
2. **strip 漂移目录**（有 OUTCAR 且 INCAR>OUTCAR）：`_strip_incar_tags(d, 'LSORBIT', 'ISYM')`。
3. **关键：strip 未收敛目录**（无 OUTCAR 或 verdict 不收敛）：
   ```python
   from vasp_sop.vasp.convergence import convergence_verdict
   for d in dirs:  # defect/*/ + cpd/*/ + unitcell/{band,dos}
       if 'LSORBIT' not in (d/'INCAR').read_text(): continue
       v = convergence_verdict(d) if (d/'OUTCAR').is_file() else None
       if v and v.converged:
           continue  # 已收敛 SOC 结果保留（ADR 0014 共存）
       _strip_incar_tags(d, 'LSORBIT', 'ISYM')
   ```
4. **failed 重提**：`vasp-sop batch retry <root> <dirs...>`（按 crisp agent.db status='failed' + submit_time 过滤 2026 批次目录）。
5. **验证**：新提交目录 INCAR 无 LSORBIT；等 2 轮 poll 后确认无新 ZBRENT 失败。

## 注意

- 已收敛的 SOC 结果**保留**（不与阶段 1 混跑）——verdict 收敛即跳过。
- dielectric 走 `_apply_soc_tags` 的 DFPT 分支（NSW=1/LREAL=.FALSE./去 SOC），不受影响；但存量 dielectric INCAR 可能 LREAL=Auto（协议偏差，结果仍有效）。
- defect 的 EDIFF/NELM 协议（1e-4/30）与 cpd（1e-4/50）不同——strip 不动这些。
- 全并行前先确认根级 `defect_energy_summary.json`（存在会 gate 住 defect 提交）——需要重跑缺陷时先删。
