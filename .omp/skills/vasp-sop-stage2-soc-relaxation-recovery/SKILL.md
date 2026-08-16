---
name: vasp-sop-stage2-soc-relaxation-recovery
description: vasp-sop SOC 两阶段 stage2 统一弛豫（ADR 0022）的批次恢复与重算流程：git 快照恢复 stage1 收敛 CONTCAR、平衡性验证、批量子集提交、纯验证对比。
---

# vasp-sop stage2 SOC 统一弛豫：批次恢复与重算（ADR 0022）

## 何时用
- SOC 体系（Gd2GaSbO7:Bi/La2SrSc2O7/La2Zr2O7/Y2Sn2O7/Y2Ti2O7）stage2 需按 ADR 0022 重跑（SOC 弛豫，非 NSW=0 单点）
- 需要从 git 快照恢复 stage1 收敛几何时
- 用户要求"恢复几何+重算"或"处理所有重算体系"时

## 背景（2026-08-12 事故链）
- 旧 `_submit_stage2_soc` 对非 Bi 目录不复制 CONTCAR→POSCAR → SOC 单点跑在遗留旧 POSCAR 上；Y2Ti2O7 perfect 的 SP 跑在废弃废 run 几何（E_perfect 偏高 1.98 eV）；171/306 SP 目录几何偏移 0.05~7.7 Å
- 用户裁决 ADR 0022：stage2 统一 SOC 结构弛豫（NSW=100 + LSORBIT），废除 NSW=0 单点；代码已修（orchestrator.py `_submit_stage2_soc` 对所有目录复制 CONTCAR→POSCAR + patch LSORBIT/ISYM，NSW 保留）；tests/test_stage2_soc.py 已更新

## 恢复几何（git 快照）
- 全部体系目录是 git 仓库（ADR 0019 自动快照）。**用 SP 批量提交前的 manual snapshot**（2026-08-12 03:16；commit：Gd2GaSbO7:Bi=74575fc, La2SrSc2O7=8b6787d, La2Zr2O7=f36193e, Y2Sn2O7=5204cb6, Y2Ti2O7=c7ae2d9）——勿用 SP 后的 cycle snapshot（内容是 SP 输入几何）
- 恢复：`git -C <体系> show <commit>:defect/<目录>/CONTCAR > POSCAR`（跨 FS 用 copy 勿 os.replace）
- 快照可能错过"stage1 收敛在快照后"的目录（如 Va_O4_2）：该类目录的收敛几何不在 git，需从最近可用几何（SP 后快照 CONTCAR = SP 输入）重跑 stage1 再接 stage2

## 提交前平衡性验证（用户强制要求，勿跳过）
- 对恢复的快照几何：检查该目录 st1_log（stage1 最后一段 run）含 "reached required accuracy" 且 mtime < 快照时刻 → 快照 CONTCAR = 收敛终点
- 或实测：恢复几何后提交 NSW=1 无 SOC 试跑，第 1 步力 < EDIFFG 即平衡（验证子集 perfect/Ti_Y6_1 第 1 步即停）
- 未平衡目录（快照错过类）先重跑 stage1（NSW=100 无 SOC INCAR 从 git baseline 取，ISTART=0），收敛后再接 stage2

## 批量提交清单（重要教训）
- **清单必须用"曾计算目录"**（audit_sp_hit 集合 + perfect），不能用"有 POSCAR 的目录"——否则误提交 ADR 0013 排除的反位目录（X→O/O→X，曾误提交 211 个，需 crisp cancel --name 逐个取消）
- 排除：Bi_* 目录（旧协议已是 SOC 弛豫）、pilot 已完成目录、Batch B 待 stage1 目录
- INCAR 逐个 ensure：NSW=100 + LSORBIT=True + ISYM=-1 + IBRION=2（曾发生 INCAR 被 stage1 版覆盖丢失 LSORBIT 而跑成无 SOC 的案例——perfect）
- 提交：`crisp submit --skip-prefill`（在目录内执行）；取消：`crisp cancel --name <task>`

## SOC 弱体系特性（成本预判）
- d0 宿主缺陷（Y2Ti2O7/Y2Sn2O7/La2SrSc2O7 等）SOC 弛豫第 1 步即收敛（能量 = 单点值），统一协议零成本；Bi 缺陷/Gd 体系才真正多跑步数（1-3 小时/目录）

## 验证对比口径（纯验证模式）
- 对比：旧 SP 能量（audit_sp_geom.jsonl sp_energy）vs 新 SP（Batch A）vs SOC 弛豫能量，Δ 分布汇总
- 修正后 E_diff = 旧 E_diff + (新 E_def − 旧 E_def) − (新 E_perfect − 旧 E_perfect)；Y2Ti2O7 perfect 修正 −1.98 eV
- 结果文件（calc_results/summary）在用户确认前不动（纯验证）；analyze 重建有 #133 竞态风险
