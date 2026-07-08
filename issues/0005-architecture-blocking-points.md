# 架构文档 & 当前阻塞点

**Date:** 2026-07-08

## Architecture 文档

`docs/architecture.md` 完整描述了当前 vasp-sop 的架构与业务流程，包括 `_batch_run` 流程、`_phase()` 阶段机、`_advance_one_system` 各阶段操作、JobStore 状态模型、CONTCAR 重启逻辑。

## 三个阻塞点

### 阻塞 1：CONTCAR 重启未集成到 crisp 提交路径

`vasp_sop/defect/compute.py:run_vasp()` 有完整的 CONTCAR 重启循环（最多 20 次，stall 检测，POTIM 增加），但它走的是本地 `submit_vasp`（subprocess + mpirun）路径，没有被 `_batch_run` 的 crisp 提交流程使用。

`_batch_run` 的轮询循环中，crisp 作业完成后如果 `check_converged()` 返回 False，什么都不做——提交标记不清除，系统永远卡住。

**影响**：30 个 UC_DF 系统中有部分缺陷属于此类。

### 阻塞 2：少数未收敛缺陷阻止全系统分析

`df_vasp_ondisk` 要求所有缺陷目录都有收敛的 OUTCAR 才触发后处理。少数不收敛的缺陷（如 Te_Ba1_-1，受力 0.0747 > EDIFFG 0.03）阻塞了其他所有已收敛缺陷的分析。

### 阻塞 3：JobStore 三态不够用

JobStore 状态：`waiting` → `running` → `done`

`running` 状态同时包含了"正在集群上跑""跑完了但没收敛""跑崩溃了"三种情况，无法区分。

需要加 `unconverged` 和 `failed` 状态。

## 待决策

1. CONTCAR 重启逻辑如何集成到 crisp 轮询路径
2. 少数未收敛缺陷是否应阻塞全系统分析（放宽条件）
3. JobStore 状态扩展
