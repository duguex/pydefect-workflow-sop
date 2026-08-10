# ADR 0017 — 批次自动恢复：cpd 相续算 / 化学势图重建 / NELM 门精化 / 相位门一致性

- 状态：已接受（2026-08-11）
- 关联：ADR 0013（反位排除）、ADR 0015（cpd 相刷新）、ADR 0016（电子收敛门）、ADR 0004（COMPLETE 门）

## 背景

SrAl4O7 推进中暴露四个相互纠缠的缺口，导致 loop 每 2 分钟空转、Fe 缺陷形成能静默缺失：

1. **相位门与 ADR 0013 不一致**：`System._infer_phase_locked` 的 COMPLETE 门遍历 `defect/` 所有目录要求 verdict 收敛 + 后处理 artifacts，但未排除反位（`Al_O*`/`Fe_O*`/`O_Al*`）。反位目录（~110 个/体系）磁盘上无 OUTCAR → 相位永远 UNITCELL_DEFECT；而 wave2/wave3 因 summary 存在短路 → 空转。
2. **cpd 相失败后无重提路径**：相位一旦过 COMPETING（target_vertices 存在），wave2 不再提交 cpd 相；`cpd_only()` 在 UNITCELL_DEFECT 相位短路。相失败 → 永不收敛 → 相位永不 COMPLETE。
3. **化学势图过期**：`compute_chemical_potentials` 是幂等一次性（target_vertices 存在即 no-op）。plan 加 dopant（ADR 0015 只刷新相、不重建图）后，旧图缺 dopant 化学势 → pydefect 算不出 dopant 缺陷形成能 → 从 summary 静默消失；analyze 却报 full（72/72 但 22 类型只出 17）。
4. **NELM 门过严**：ADR 0016 判"文件中任意位置含 `increasing NELM` 警告 → 不收敛"。Al13Fe4 的警告出现在**早期离子步**（第 7 步电子耗尽），后续离子步全部电子收敛、离子整体收敛——被误判 electronic_not_conv，且 NELM=100 重算也复现（金属相 EDIFF=1e-7 过严）。

## 决策

### 1. 相位门过滤反位（ADR 0013 一致性）

`_infer_phase_locked` 的 verdict 循环与 post-processing 循环跳过 `is_anion_cation_antisite(name)` 目录（公开化自 `_is_anion_cation_antisite`）。junk 目录（无 `Name_Charge` 模式）**仍阻塞**（ADR 0004 严格门，测试锁定）。

### 2. cpd 相 ionic 自动续算

wave2（任何相位）对 cpd 相：verdict 未收敛 且 reason ∈ {force_gate_fail, nsw_exhausted, nsw_early_exit, missing_forces, **truncated**} 且 JobStore 非 submitted → `restart_from_contcar` + 提交（source=`ionic_restart`）。**上限 3 次**（`_CPD_MAX_IONIC_RESTARTS`）只对 force 停滞类生效——力停滞（EDIFFG 过严）每轮白烧 NSW 步，需要人工参数决策。`electronic_not_conv`/`missing_outcar` 不自动重提（同参数重算无意义）。

**truncated 例外（2026-08-11 补充）**：TIME-LIMIT 截断是 transient——CONTCAR 每轮前进（不是白烧），**豁免 3 次上限**，且续算提交自动带 `--tag long`（长 QOS 集群，经 `submit_vasp` 的 tags 参数；此前 long tag 只给 >150 原子 defect，cpd 相会被短 QOS 反复杀）。实证：Sr[FeO2]2_mp-21926（56 原子、~1.3 分/离子步）被 qos_test 21 分钟杀两次后，自动转 duguex_5 长 QOS 续算。

补充（2026-08-11）：COMPETING 段的 `--retry-failed` auto_retry（ADR 0007，原 POSCAR 一次重试）同样排除 `electronic_not_conv`——SCF 确定性复现，重跑必败；transient 类（vasp_crash/TIME-LIMIT 截断）保留一次重试。

### 3. 化学势图 stale 检测 + 自动重建

- `cpd_diagram_stale(cpd_root, config)`：plan 元素（formula + dopant_elements）⊄ standard_energies.yaml 元素 → stale。
- `refresh_cpd_diagram`：mce preflight 通过后删 5 个旧工件（target_vertices/chem_pot_diag/standard_energies/composition_energies/relative_energies）→ 重跑 `compute_chemical_potentials`。preflight 失败 → 保留旧图（不删）。
- 挂接点：wave3 的 UNITCELL_DEFECT 段（analyze/already_complete 前）+ `advance_one_system` 的 COMPLETE 分支（图 stale 时删除旧 summary → 相位回落 UNITCELL_DEFECT → 下一 cycle 重 analyze）。

### 4. NELM 门精化（sidecar schema v3）

警告只在**最后离子步**（最后一个 `LOOP+` 标记之后）才算耗尽；早期离子步警告 + 后续收敛 → 通过。无 `LOOP+` 标记的 OUTCAR（单点/截断）保守按原语义（警告存在即拒）。Al13Fe4 型目录自动转 converged，无需重算。

### 5. analyze 完整性门（防假 full）

`classify_analyze_status` 的 full 额外要求：summary 的 `defect_energies` 类型覆盖 defect_in.yaml 的 valid 类型（反位类型不算，正则接受无电荷后缀的 key）。缺失 → partial（`analyze_status.json` 增加 `missing_types` 字段）。附带：`with_dei` 接受 `defect_energy_info.yaml`（pydefect 实际输出）。

## 影响

- CaAl4O7：相位从 UNITCELL_DEFECT（数据已完整但相位卡死）→ **COMPLETE**。
- SrAl4O7：analyze full 72/72、22 类型（含 5 个 Fe）——Fe 形成能补齐；仅剩 Sr[FeO2]2（ionic 续算中）卡 COMPLETE。
- 2025 恢复注意：NELM 门精化后 11 个"耗尽目录"需**先重判**再决定重算（部分可能自动转 converged）。
- 测试：480 passed（+21：相位门 3、NELM 位置 2、cpd 续算 9、图 stale/刷新 4、类型门 3）。

## 未决

- **Sr[FeO2]2 参数决策（2026-08-11，用户）**：EDIFFG 从 -0.005 放宽至 **-0.01**（仅此目录，未改全局协议）。旧作业 cancel，新作业 96629351（-0.01 + CONTCAR 续算）运行中。若 3 次 ionic restart 内仍不收敛（力停在 ~0.02+），再议（-0.03 与 defect 一致，或接受不收敛若不在 Fe 凸包顶点）。
