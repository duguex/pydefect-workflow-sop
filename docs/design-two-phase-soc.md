# 两阶段 SOC 机制设计（ADR 0014 实现）

## 目标

非 SOC 收敛后自动补算 SOC：含 Bi 目录 SOC 续算、其余 SOC 单点（NSW=0）。全自动（loop 驱动），CaAl4O7 收尾后实施于 SOC 体系。

## 术语

- **阶段 1**：非 SOC 结构优化（现状流程 + INCAR 去 LSORBIT——实施时脚本处理已存在 INCAR，新建目录由 prepare_inputs 生成后统一去 SOC）
- **阶段 2**：SOC 补算——`Bi_*` 目录 = SOC 续算（LSORBIT + CONTCAR→POSCAR + 继续优化）；其余 = SOC 单点（LSORBIT + NSW=0）
- **soc_done**：目录完成阶段 2 的标记（js history `source="soc_stage2"`）

## 触发与状态机

```
loop poll:
  对每个 defect/cpd 目录:
    if 阶段1 收敛（converged） and 最新 source != "soc_stage2":
        if config.soc（体系级，plan 的 parameters.soc）:
            if 目录名 startswith "Bi_":  SOC 续算提交（source=soc_stage2）
            else:                        SOC 单点提交（source=soc_stage2）
```

- 阶段 2 提交后 js 记录 `submitted`（source=soc_stage2）；crisp 完成后收敛判定照旧 → `converged`
- **wave3 门**：defect/cpd 全部目录的**最终收敛**（含阶段 2）后才 analyze——实现为 wave3 检查目录最近记录含 soc_stage2 且 converged（SOC 体系）
- 非 SOC 体系（无 config.soc）：不触发阶段 2

## INCAR 修改（阶段 2 提交前）

- SOC 续算：patch LSORBIT=.TRUE. + ISYM=-1（保留 NSW 等其余参数）；CONTCAR→POSCAR
- SOC 单点：patch LSORBIT=.TRUE. + ISYM=-1 + NSW=0（其余保留；KPOINTS 不变）

## 范围

- defect：全部目录（`Bi_*` 续算 / 其余单点）
- cpd：目录名含 Bi（如 GdBi_mp-*）续算，其余单点
- unitcell：band/dos/dielectric 本来就是 NSW=0 光谱任务——SOC 单点等价于直接重跑（加 LSORBIT）；structure_opt 同 defect 规则

## 实施顺序（CaAl4O7 收尾后）

1. 5 个 SOC 体系：INCAR 去 LSORBIT（已存在目录脚本处理；prepare_inputs 对 soc 体系不再自动加——代码改）
2. 阶段 2 机制代码 + 测试（本设计）
3. 单体系串行推进（按完成度）：SrAl4O7 → Gd2GaSbO7:Bi → La2Zr2O7 → Y2Sn2O7 → La2SrSc2O7 → Y2Ti2O7（含 Bi 缺陷重建）

## 风险

- 已算的（SOC 直接收敛的 102 个）保留——阶段 2 判定用 source 标记，已算的补记 soc_done（一次性脚本）避免重算
- Y2Ti2O7 的 Bi 缺陷缺失（defect_in.yaml 早于 plan dopant 变更）——实施时重建
