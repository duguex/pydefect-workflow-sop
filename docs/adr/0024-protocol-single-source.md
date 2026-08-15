# ADR 0024 — 计算协议单一事实源(protocol 模块)

- 状态：已接受（2026-08-15）
- 关联：ADR 0012（+U 永远打开）、ADR 0014 / ADR 0022（两阶段 SOC）、ADR 0007（输入还原）、issue #150（协议漂移）、issue #151（Gd 磁矩塌缩）
- 实现：`vasp_sop/vasp/protocol.py`；调用点 `vasp_sop/vasp/io.py`、`vasp_sop/defect/builder.py`、`vasp_sop/defect/unitcell.py`、`scripts/check_results.py`

## 背景

2026-08-15 批次验收（scripts/check_results.py 两支柱）检出 13/13 体系 ENCUT/EDIFF/EDIFFG/SIGMA 漂移（门禁级，整批不可信）。根因追踪后确认：**协议值没有单一事实源**，散落六处、靠注释互相镜像：

1. `io.py` CLI 路径 `-uis "NSW 50 NELM 50 EDIFF 1e-4 EDIFFG -0.01 ..."`（字符串内联）
2. `io.py` API 路径 `overrides = {"NSW": "100", "NELM": "30", "EDIFF": "1e-4"}`（**与 CLI 路径值不一致**：NSW 50 vs 100、NELM 50 vs 30）
3. `unitcell.py` 直拼 vise 命令 + EncHUT 注入
4. `builder.py` `extra_uis="SIGMA 0.02 LORBIT 11"`（只 defect 有）
5. `patch_incar_u` 的 `_U_TABLE`
6. `patch_incar_magmom` 的 `_MAGMOM_TABLE`（**仅 Fe=5.0**——Gd 不在表内 → SOC 计算无磁矩初始化 → Gd³⁺ 4f 磁矩塌缩 ~0 μB）

叠加事实：13/13 体系 `plan.yaml` 的 `encut: null`——生成器把 ENCUT 完全让给 vise 模板默认，vise 模板又在腿间不一致（unitcell structure_opt=520 vs band/dos/dielectric=400），漂移被固化。代码注释自认历史多次漂移（“dropped NSW 50→20”“overrides can drift with vise releases”）。

## 决策

1. **`vasp_sop/vasp/protocol.py` = 协议单一事实源**：
   - `LEG_PROTOCOL`：每条腿（defect/cpd/structure_opt/band/dos/dielectric）的 NSW/NELM/EDIFF/EDIFFG/SIGMA/LORBIT 声明表，值收敛 2026-08-11 批次决定（defect: NSW=100/NELM=30/EDIFFG=-0.01/SIGMA=0.02/LORBIT=11；cpd+structure_opt: NSW=50/NELM=50/EDIFFG=-0.01；band/dos/dielectric: 只声明电子层）。
   - `U_TABLE`：DFT+U 表（io.py 旧 `_U_TABLE` 迁入，含 Ti/3d/4f 镧系）。
   - `INITIAL_MAGMOM`：初始磁矩高自旋表（Fe=5/Mn=5/Co=3/Ni=2/Gd=7/4f M³⁺ f 电子近似）；Ti(d⁰)/Cu(d⁹)/Zn(d¹⁰) 不写。
   - `encut_for_potcar` / `effective_encut`：ENCUT = plan/config 显式值优先，否则目录 POTCAR 的 **1.3×max ENMAX**（VASP 保守惯例）——生成器永不落入 vise 模板默认。
2. **生成器三路径统一取数**：CLI `-uis`、API `overridden_incar_settings`、UC 直拼命令全部由 `protocol_tags(task_type)` 组装；`extra_uis` 显式覆盖在后（调用点可抬高，协议表是基线）。`builder.py` 不再散落 `extra_uis="SIGMA 0.02 LORBIT 11"`（协议表已有）。
3. **ENCUT 分区语义固化**：defect/unitcell 的 POTCAR 同为宿主组成 → per-目录检测即宿主单值（组内一致）；cpd 竞争相按各自组成（合法分区豁免，与 check 既有判定一致）。
4. **修复 unitcell structure_opt 漏传 task_type**（原来 `prepare_inputs(structure_opt_dir, config)` 无 task_type → 拿不到 EDIFFG=-0.01 与 cpd 不一致）。
5. **check_results 升级为对照协议基线**：记录级“协议不符”维度（OUTCAR 回显 vs LEG_PROTOCOL）+“无 MAGMOM 初始化”输入侧检查（真磁性元素 ISPIN=2 无 MAGMOM）。

## 影响

- 生成器改动只影响**未来生成**的目录；存量 939 目录不动（只被 check 标记差距）。
- defect 腿新增 EDIFFG=-0.01（此前遗漏）——未来 defect 离子收敛标准与 cpd 一致；新增 Gd/Fe/Co/Ni/4f 初始磁矩——修复 #151 塌缩输入侧。
- 存量 defect SIGMA=0.1 目录会被“协议不符”标记（预期——它们是 2026-08-11 前产物）。

## 验证

- `python3 -m pytest tests/`（新增 test_protocol.py + test_io 回归）。
- 全批 check 复跑：协议不符/无 MAGMOM 维度输出与存量差距一致；组内一致性维度不受影响。