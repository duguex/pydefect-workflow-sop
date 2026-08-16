# ADR 0025 — 两阶段 DFT+U（stage1 无 U 弛豫 → stage2 +U 补充）

- 状态：已接受（2026-08-16）
- 关联：ADR 0012（+U 永远打开）、ADR 0014 / ADR 0022（两阶段 SOC）、ADR 0024（协议单一事实源）
- 实现：`vasp_sop/vasp/protocol.py`（needs_u / is_singlepoint / needs_final_soc）、`vasp_sop/vasp/io.py`（patch_incar_u apply_u 拆分、apply_final_protocol）、`vasp_sop/core/orchestrator.py`（stage2 泛化、wave3 硬门）、`scripts/check_results.py`

## 背景

2026-08-16 设计评审（grill 会话）确定 DFT+U 与 SOC 同构的两阶段协议。此前 +U 在所有生成路径无条件开启（CLI `set_hubbard_u True`、API `set_hubbard_u=True`、UC 直拼、cpd patch）——一遍算完。

## 决策

1. **元素集派生触发**（Q1）：目录含 U 表元素 → stage1 生成**无 LDAU**（自旋段保留），收敛后 stage2 补充 +U——无新增 plan 配置。
2. **stage2 = 弛豫**（Q2）：从 stage1 收敛的 CONTCAR 续算弛豫（ADR 0022 模式），非单点。
3. **能量口径**（Q3）：wave3 形成能/化学势凸包统一用 stage2（+U/+SOC）能量——cpd 相与 defect 同阶段可比。
4. **自旋保留**（Q4）：stage1 磁性元素仍 ISPIN=2 + MAGMOM（自旋独立于 U）；`patch_incar_u` 拆分自旋段（恒执行）与 U 段（apply_u 控制）。
5. **只影响新树**（Q5）：存量 13 体系保持现状（check 标差距），处置另行授权。
6. **合并一次**（Q6）：stage2 一次 patch 最终协议（体系需 SOC 则 +LSORBIT、含 U 元素则 +LDAU）→ 一次弛豫。既有 `_submit_stage2_soc` 泛化为 `_submit_stage2`。
7. **单点腿带 U 无 SOC**（Q7）：band/dos/dielectric 生成即带 U（`set_hubbard_u True`），**不再加 LSORBIT**（band/dos 现状有——去除）；不参与两阶段。
8. **物理判定**（Q8）：stage2 pending = converged 且 INCAR 缺最终协议标志（读 INCAR，不依赖 JobStore source）；既有 `soc_stage2` 记录兼容（INCAR 已含标志 → 不再补）。
9. **wave3 硬门**（Q13）：analyze 前全部弛豫腿（cpd 相除 mol_* + defect 链）必须达最终协议——否则 stage1 能量混入凸包（口径不一致），体系状态 `stage2_pending`，不 analyze/COMPLETE。

## 影响

- 新树缺陷链计算量增加（stage2 补充弛豫）；物理上 +U 结构响应被保留。
- 单点腿行为变化：soc 体系的 band/dos 不再带 LSORBIT（能带图无 SOC 分裂——诊断用途，能量不进形成能）。
- check_results 新增「待 stage2」记录级维度 + LSORBIT 组内一致豁免单点腿。

## 验证

- `python3 -m pytest tests/`（test_stage2_soc 重写为物理判定语义、test_io 更新 apply_u 拆分）。
- 存量 13 体系 check 复跑：perfect/cpd 的 ISIF、单点腿 LSORBIT 豁免、待 stage2 维度输出符合预期。
