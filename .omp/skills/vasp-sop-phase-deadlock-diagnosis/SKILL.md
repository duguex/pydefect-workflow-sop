---
name: vasp-sop-phase-deadlock-diagnosis
description: "诊断 vasp-sop 批次\"相位卡死/loop 空转/假 full\"三类症状：loop 每 cycle 空转但 0 提交、dopant 缺陷形成能静默缺失、cpd 相失败永不重提。用于用户问\"为什么没在跑/相位怎么不动/Fe 缺陷怎么不见了\"。"
---

# vasp-sop 相位卡死 / loop 空转 / 假 full 诊断（ADR 0017 era）

症状与根因一一对应，按序排查：

## 1. Loop 每 cycle 显示 `UNITCELL_DEFECT ... done` 但 0 提交 = 相位门短路

- 机制：`defect_energy_summary.json` 存在 → wave2/wave3 短路；`System._infer_phase_locked` 的 COMPLETE 门要求 defect/ 每个目录 verdict 收敛 + 后处理 artifacts。
- 若 COMPLETE 门**未过滤反位**（ADR 0013，2026-08-11 前）→ ~110 个 `Al_O*/Fe_O*/O_Al*` 目录无 OUTCAR → 相位永不 COMPLETE → 空转。
- 修复：`system.py` COMPLETE 门跳过 `is_anion_cation_antisite(name)`（junk 目录仍阻塞，ADR 0004）。测试锁定在 test_system.py。

## 2. dopant 缺陷形成能静默缺失 + analyze 假 full = 化学势图 stale

- 机制：`compute_chemical_potentials` 幂等一次性（target_vertices 存在即 no-op）；plan 加 dopant（ADR 0015 只刷新相不重建图）→ 旧图缺 dopant 化学势 → pydefect 算不出 dopant 缺陷 → summary 类型缺失但 analyze 报 full。
- 检测：`cpd_diagram_stale(cpd_root, config)`（plan 元素 ⊄ standard_energies.yaml 元素）或对比 defect_in.yaml valid 类型 vs summary `defect_energies` 类型。
- 修复链：stale → preflight 安全重建（删 5 工件重跑）→ 删旧 summary → 重 analyze；analyze full 门要求类型全覆盖（`missing_types` 字段）。
- **注意**：defect_in.yaml key 无电荷后缀（`Al_O1`），反位正则已兼容。

## 3. cpd 相失败永不重提（相位死锁）

- 机制：相位过 COMPETING 后 wave2 不再提交 cpd 相；cpd_only() 在 UNITCELL_DEFECT 短路。
- 修复：wave2 任何相位对 force 类失败（force_gate_fail/nsw_exhausted/nsw_early_exit/missing_forces）自动 `restart_from_contcar` 续算，上限 3 次（`_CPD_MAX_IONIC_RESTARTS`）。electronic_not_conv 不自动（同参数重算无意义）。

## 4. NELM 门误判

- 警告在**早期离子步**（后续收敛）是误判——verdict 只认最后一个 `LOOP+` 之后的警告（sidecar schema v3）。无 LOOP+ 保守拒。
- 2025 恢复前先重判 11 个 NELM 警告目录，可能部分自动转 converged 无需重算。

## 关键命令

```bash
# 相位
.venv/bin/python -c "from pathlib import Path; from vasp_sop.core.config import PipelineConfig; from vasp_sop.core.system import System; r=Path('PROJECT_ROOT'); cfg=PipelineConfig.from_yaml(r/'SYSTEM'/'plan.yaml', root=r/'SYSTEM'); print(System(r/'SYSTEM', cfg).phase())"
# 图 stale
.venv/bin/python -c "from vasp_sop.defect.cpd import cpd_diagram_stale; ..."
# analyze 类型缺口
python3 -c "import json; json.load(open('defect/analyze_status.json'))['missing_types']"
```

改 orchestrator/system/convergence 代码后必须 `systemctl --user restart vasp-sop-loop` 才生效（editable 安装直吃源码，但进程常驻）。
