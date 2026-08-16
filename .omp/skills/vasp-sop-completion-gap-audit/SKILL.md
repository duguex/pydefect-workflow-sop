---
name: vasp-sop-completion-gap-audit
description: 盘点 vasp-sop 2026 批次与「全算完」(COMPLETE) 的差距：每体系每腿 converged/never/unconverged 计数 + analyze full/partial/failed + 未跑目录的人工排除检查 + 两阶段 SOC stage2 欠账。用户问「和算完的 gap / 还差多少 / 什么时候完」时使用。
---

# vasp-sop 完成度差距盘点（和"算完"的 gap）

回答"和全算完还差多少/还差什么"时，一次 eval 给出答案。**算完** = 每体系内所有有效目录收敛 + defect 分析 full + formation_energy 报告存在（CONTEXT.md 定义）。

## 数据源（全部本地，不 ssh）

- 每目录收敛判定：`vasp_sop.vasp.convergence.convergence_verdict`（权威，含 NELM 门/截断语义）
- defect 目录有效性：`vasp_sop.defect.is_valid_defect_dir`（ADR 0013 反位排除——never/unconverged 计数前先过滤，否则把被筛目录当欠账）
- analyze 状态：`vasp_sop.defect.analysis.classify_analyze_status(defect_dir)` → full/partial/failed
- 报告存在性：`<system>/formation_energy_interactive.html`
- 范围排除：`vasp_sop.defect.cpd.excluded_phases(system)`，patterns 是子串匹配（`pat in dname`）

## 扫描骨架

```python
from vasp_sop.defect import is_valid_defect_dir
from vasp_sop.defect.analysis import classify_analyze_status
from vasp_sop.defect.cpd import excluded_phases
for s in systems:  # 有 plan.yaml 的体系目录
    for leg in ('cpd','defect','unitcell'):
        for d in leg 目录（跳过 combos/perfect）:
            if leg=='defect' and not is_valid_defect_dir(d): continue
            v = convergence_verdict(d)
            converged / never(missing_outcar) / unconverged(按 reason 分组) 计数
    ast = classify_analyze_status(s/'defect')
```

## 输出口径（四类欠账，缺一不可）

- **A 有效从未提交**：never 目录逐个列表 + 检查是否已人工排除（未排除=真欠账；排除=范围决策、无碍）
- **B 真未收敛**：reason=force_gate_fail 等，注明触顶（ionic restart cap）需人工参数决策 vs 在跑
- **C 范围阻塞**：max_abc>25Å 等物理跑不了但**未写入 cpd_excluded_phases.yaml** 的（如 Ba4Al2O7_mp-560978，loop 每轮跳但永远 never）
- **D stage1 已收敛等 stage2 SOC**：配合 skill vasp-sop-two-phase-soc-position 判（cpd/defect 均适用；只有含 Bi 的才续算，其余 NSW=0 单点）

## 关键解释规则

- analyze=failed 不代表系统坏——多半是前面欠账（cpd 未全收敛/chem pot 未出）导致 wave3 走不出来；先清 A/B/C/D 再看 analyze
- **结构性假卡**：phase 已越过 COMPETING 的体系（如 UNITCELL_DEFECT），cpd/defect 提交腿只活在 COMPETING 分支 → never 目录**即使 loop 在跑也永远不会被提**（ADR 0020/#122）——这类要归因于结构缺陷而不是"没轮到"
- 有 6 个常驻 running 的 force_gate_fail 重试目录，量级对总差距影响小，别当主要卡点

## 经验值（2026-08-12）

10 体系扫描：3 COMPLETE（CaAl4O7/SrAl4O7/SrGa4O7:Fe），7 个欠账：有效 never≈39、真未收敛 8（2 触顶）、范围阻塞 2（Ba4Al2O7）、stage2 欠账≈20、7 系统 analyze=failed。
