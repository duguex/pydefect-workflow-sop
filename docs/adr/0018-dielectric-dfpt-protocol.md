# ADR 0018 — Dielectric（DFPT）INCAR 协议：NSW=1 · LREAL=.FALSE. · 无 SOC · 长 QOS

- 状态：已接受（2026-08-11）
- 关联：ADR 0014（两阶段 SOC）、ADR 0017（批次自动恢复）

## 背景

La2Zr2O7/unitcell/dielectric 反复失败（内存 748MB/rank 分配失败、LRF_COMMUTATOR internal error、TIME LIMIT），208376 挂起 17.5h。根因链：

1. **NSW=50 协议回滚**：`prepare_inputs` CLI 路径对所有任务统一传 `NSW 50`（`uis_flags`，2026-06-22 起）。DFPT（IBRION=8/LEPSILON）是**单步微扰评估**——NSW>1 时每个离子步重跑线性响应（50 倍计算量）。vise 模板默认 NSW=1；08-08 前生成的体系（CaAl4O7/SrAl4O7）因 input_ready 早退未重生成而幸存；La2Zr2O7 08-10 17:56 触发重生成（vise_log 实证 `user_incar_settings: NSW: 50`）→ 回滚。
2. **DFPT+SOC 不兼容**：SOC patch 对 unitcell 所有任务无差别加 `LSORBIT=.TRUE.`。VASP DFPT 不支持自旋轨道耦合——旋量波函数内存翻倍（rank-0 分配失败）+ 线性响应核内部错误（LRF_COMMUTATOR）。5 个 SOC 体系 dielectric 全受影响。
3. **LREAL=Auto**：VASP 明确建议 DFPT 用 `LREAL=.FALSE.`（实空间投影精度不足）。
4. **无长 QOS 通道**：dielectric ~2h，短 QOS（qos_test 20min）必死；`--tag long` 此前只给 >150 原子 defect。

## 决策

`_apply_soc_tags`（io.py）对 `task_type == "dielectric"` **无条件**（不受 plan soc 门控）执行：

- `NSW = 1`（DFPT 单步；防重复线性响应）
- `LREAL = .FALSE.`（VASP 推荐）
- 移除 `LSORBIT`/`ISYM`（DFPT 无 SOC 支持；幂等，可修复已误伤目录）

其余 SOC 任务（band/dos/structure_opt/defect）维持 `LSORBIT=.TRUE./ISYM=-1`。

`_crisp_submit`（jobs.py）对 `/unitcell/dielectric` 路径**自动加 `--tag long`**（长 QOS 集群），与 >150 原子 defect 同策略。

## 影响

- 生成逻辑修复 + 存量修复：La2Zr2O7（NSW 50→1、删 LSORBIT、LREAL False）、Gd2GaSbO7:Bi（删 LSORBIT）、Y2Ti2O7（LREAL False）；其余 5 体系 dielectric 未生成，由生成逻辑直接保护。
- 208376（17.5h 挂起）cancel；87a8b50f 用修复协议重提（205.5 长 QOS）。
- 2025 唯一 SOC 体系 CsPbBr3 的 dielectric 干净（NSW=1、无 LSORBIT），无同病。
- 测试：dielectric 协议 3 项（soc/非 soc 都 NSW=1、band 仍 SOC）。486 passed。
- 已知现象（不深究）：crisp agent.db 历史记录有清理/丢失机制（08-08 成功的 CaAl4O7 dielectric 记录缺失；08-10 12:08-12:39 提交无 DB 记录）——vasp-sop JobStore 是权威，不影响计算正确性。

## 未决

- dielectric 完成后信任 VASP 输出（用户决策 2026-08-11），不做额外 sanity check。
