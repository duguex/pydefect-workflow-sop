# ADR 0022 — 阶段 2 SOC 统一为结构弛豫（废除 NSW=0 单点）

- 状态：已接受（2026-08-12）
- 关联：ADR 0014（两阶段 SOC，本 ADR 修订其阶段 2 执行方式）、ADR 0019（git 输入快照）

## 背景

ADR 0014 阶段 2 区分两类目录：含 Bi 的 `Bi_*` 目录做 SOC 续算（结构弛豫），其余目录做 SOC 单点（NSW=0，几何固定，仅一次 SOC 能量修正）。2026-08-12 审计发现该区分造成系统性错误：

1. **单点腿几何错误**：`_submit_stage2_soc` 对非 Bi 目录只 patch `NSW=0`，不复制 `CONTCAR→POSCAR`，单点跑在目录遗留的旧 POSCAR 上（而非阶段 1 收敛几何）。Y2Ti2O7 perfect 的单点甚至跑在已废弃的错 NELECT 废 run 几何上，E_perfect 偏高 1.98 eV（重算验证精确命中）。全批 306 个单点目录中 171 个存在 0.05–7.7 Å 的几何偏移，46 个能量偏差 >1 eV。
2. **协议不一致**：同一体系内 Bi 缺陷（SOC 弛豫）与宿主缺陷（SOC 单点）的能量基面不同——SOC 弛豫会进一步优化结构，单点不会。

## 决策

**阶段 2 统一为 SOC 结构弛豫，不再区分体系/目录类型**：

1. 所有目录（含不含 Bi）的阶段 2 均为：从阶段 1 收敛 CONTCAR 续算，`LSORBIT=True` + `ISYM=-1`，**NSW 保持阶段 1 的值（100）**，结构在 SOC 下继续优化到收敛（`reached required accuracy`）。
2. `_submit_stage2_soc` 删除 `is_bi` 分支与 `NSW=0` patch；`CONTCAR→POSCAR` 复制对所有目录生效（零字节 CONTCAR 保护保留）。
3. 阶段 1（无 SOC 弛豫）保留——两阶段框架不变，仅阶段 2 内容变更。
4. 既有 NSW=0 单点结果作参考数据，不作为最终形成能来源（重算批次已按新协议进行）。

## 重提续算纪律（2026-08-14 增补）

**阶段 2 的任何重提必须以最近 CONTCAR 为续算真相源**，不得回退到旧 POSCAR 起点：

1. 未收敛目录的每轮 ionic 重提（`wave2_submit` CPD restart 段）执行 `restart_from_contcar`（CONTCAR→POSCAR + ISTART=1），触发条件为 verdict reason ∈ `_IONIC_RETRY_REASONS`（`force_gate_fail` / `nsw_exhausted` / `nsw_early_exit` / `missing_forces` / `truncated`）。
2. **续算失效必须可观测**：`restart_from_contcar` 失败不得静默（logger.warning）；重提后若 POSCAR mtime 仍旧于 CONTCAR，告警"continuation not in effect"——断链即暴露，禁止静默打转。
3. 阶段 2 首轮由 `_submit_stage2_soc` 布防（LSORBIT + CONTCAR→POSCAR），其后每轮重提走 cpd ionic restart 路径（同样续算）——两条路径都受本纪律约束。
4. 磁初值纪律：SOC 体系 INCAR 应显式 MAGMOM（无 MAGMOM 时每轮磁态初值漂移，轨迹噪声掩盖续算效果）。

### 事件记录：Ti8Bi9_mp-640045 原地打转（2026-08-14）

- 现象：Y2Ti2O7_mp5373 cpd 的 Ti8Bi9 阶段 2（SOC 弛豫，NSW=50）重提 5 轮，每轮首 F 回落到 −174.8 eV 区间（stage1 CONTCAR 起点能量），末 F 卡在 −175.86 不再下降，5 轮全部未收敛；TIME LIMIT 轮（duguex_113，20 分钟时限）与 NSW 用尽轮交替。
- 证据：211675 stage1 收敛（−164.70，无 SOC）；stage2 各轮首 F −174.781/−174.841/−174.948/−174.785/−174.976，末 F −175.739/−174.958/−175.845/−174.986/−175.858。TIME LIMIT 轮间首 F 与上轮末 F 吻合到 0.01 eV（续算生效），NSW 用尽轮后回退到旧起点（续算失效）——轮间续算并非始终生效。
- 处置：该相排除（移出 cpd/，2026-08-14）；修复方向为本纪律第 2 条（续算失效可观测）+ 定位 NSW 用尽轮续算失效的具体断点（竞速窗口：作业结束 CONTCAR 落地与 loop 下一 cycle 读取之间的同步）。
- 教训：结果不可用时的重提循环必须检查"每轮起点是否推进"，否则是打转而非收敛。

## 代价与风险

- **成本上升**：SOC 弛豫（NSW=100）每目录从 ~5 分钟（单点）变为 ~1–3 小时，全批 ~300 目录数周量级（并行）。
- 阶段 2 能量与旧单点能量不可直接混用；已完成的验证性单点重算（E_perfect 修正 −1.98 eV 等）作为协议诊断证据保留。
- 单点腿曾有的几何错误机制（不复制 CONTCAR）已同时修复，未来即使恢复单点也不应复现。

## 执行记录（2026-08-12）

- 代码：`orchestrator.py::_submit_stage2_soc` 统一；`tests/test_stage2_soc.py` 更新为新协议断言。
- 批次：git 03:16 快照恢复阶段 1 收敛几何；Batch A（140 目录 SP）按用户决定跑完作参考；小批 SOC 弛豫验证后全量（另见批次交接文档）。
