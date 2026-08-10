# ADR 0015 — CPD 相集自动刷新（plan 元素变更检测）

- 状态：已接受（2026-08-10）
- 关联：ADR 0014（两阶段 SOC）、ADR 0013（缺陷目录门）

## 背景

competing-phase 集合必须覆盖缺陷化学的所有元素（宿主 + 掺杂）。CaAl4O7 在 `dopant_elements: [Fe]` 加入 plan.yaml **之后**才生成 cpd（08-08 vs 08-09），cpd 只有 Ca/Al/O 相——`standard_energies.yaml` 缺 Fe 的化学势，pydefect `dei`（形成能）崩溃 `KeyError: 'Fe'`，wave3 卡 partial。

## 决策

`ensure_cpd_phases(cpd_root, config)`（cpd.py）：
- **检测**：`cpd/mp_state.json` 的 `elements`（fetch 时的 intrinsic+dopant）≠ plan 当前元素集（`get_intrinsic_elements(formula) + dopant_elements`）→ mismatch
- **重建（增量）**：fetch 到临时目录 → 只移动新相目录（现有已收敛相不动）→ 提交新相 → 重写 mp_state.json
- **触发**：`advance_one_system` 每轮（本地检测便宜，mismatch 才网络 fetch+提交）
- fetch/提交失败非致命（警告继续）

## 手动执行路径（存量体系）

`fetch_candidate_phases` 全量生成会撞已存在目录（pydefect 不能增量）——手动时用 tmp 生成 + 移动新相（CaAl4O7 的 11 个 Fe 相即此路径）。
