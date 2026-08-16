---
name: vasp-sop-two-phase-soc-position
description: "盘点 2026 批次两阶段 SOC（ADR 0014）当前位置：对 5 个 soc+stage2_soc 体系按 cpd/defect 腿分类 stage1 未开始/在跑/完成待 st2/st2 完成，并识别残留 SOC 标签。用户问\"两阶段现在到哪了/SOC 进度\"时使用。"
---

# 两阶段 SOC 位置盘点（ADR 0014）

回答"现在两阶段到哪了"——按腿分类 stage1/stage2 位置，识别需要人工处理的目录。

## 数据源（两个，缺一不可）

- **JobStore**：`~/.vasp_sop/jobs.db`（表 `job_history`：dir_path/status/timestamp/source/attempt）。**source=`soc_stage2` 是 stage2 已布防的唯一标记**。
- **磁盘 INCAR**：`LSORBIT = .TRUE.` 正则（大小写/点号变体：`LSORBIT\s*=\s*\.?TRUE`）。
- **verdict**：`vasp_sop.vasp.convergence.convergence_verdict`。
- ⚠️ **agent.db（~/.crisp/data/agent.db）没有 source 列**——stage2 判定必须查 JobStore，别查错库。

## 分类逻辑（每目录）

```
if LSORBIT and converged and 'soc_stage2' in job_history.sources → st2完成
elif LSORBIT and latest in (running/submit/submitted) → st2在跑
elif LSORBIT → 其他（见下）
elif converged → st1完成待st2
elif latest live → st1在跑
else → st1未开始
```

**"其他"再细分**（两子类性质完全不同）：
- **旧全SOC遗留**：LSORBIT + converged + 无 st2 记录——切换两阶段前按全 SOC 跑完的目录，**等效 st2 结果，无需处理**（2026-08-12 实测：Gd defect 2、La2Sr cpd 11、Y2Ti defect 8 等）。
- **残留 SOC 标签**：LSORBIT + **未收敛** + 无 st2 记录——旧协议 INCAR 残留，stage1 提交前必须 strip（issue #115 类，否则 stage1 带 SOC 跑 ZBRENT 风险）。2026-08-12 实测：La2Zr2O7 cpd ZrBi_mp-30933、ZrBi2_mp-29642。

## 关键机制事实

- stage2 腿在 `orchestrator.py` `wave2_submit` 的 **COMPETING 相位分支**（~530-537 行）：`_stage2_soc_pending`（JobStore latest=converged 且无 soc_stage2 记录）+ `_submit_stage2_soc`（Bi_* 相从 CONTCAR 续算弛豫，其余 NSW=0 单点补能量修正）。**cpd 和 defect 都覆盖**——cpd 两阶段闭环已存在，只差 stage1 提交。
- stage2 补 SOC 的提交只发生在 COMPETING 相位——相位锁定（target_vertices.yaml 存在）时 cpd 的 stage1 和 stage2 都不会跑。
- `_stage2_soc_pending` 要求 JobStore latest=converged——旧全SOC遗留目录（无 st2 记录但已带 SOC 收敛）会**被当成 stage1 完成待 st2**，若再提交会重复跑 SOC 单点——盘点时注意区分。

## 5 个 SOC 体系

Gd2GaSbO7:Bi / La2SrSc2O7 / La2Zr2O7 / Y2Sn2O7 / Y2Ti2O7（plan `soc: true` + `stage2_soc: true`）。defect 腿用 `is_valid_defect_dir` 过滤（ADR 0013 排除反位），cpd 跳过 combos/perfect。

## 输出

每体系每腿一行分类计数（st1未开始/st1在跑/st1完成待st2/st2完成/st2在跑/其他），残留标签目录列名——用户据此判断推进卡点。
