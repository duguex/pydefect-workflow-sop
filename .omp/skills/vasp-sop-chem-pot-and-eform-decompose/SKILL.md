---
name: vasp-sop-chem-pot-and-eform-decompose
description: "Audit vasp-sop cpd chemical-potential freshness (composition_energies vs newest logs, OUTCAR pollution by unconverged SOC SP) and decompose formation-energy terms to localize deep-negative E_f. Use when 化学势检查/形成能深负定位/能量可比性审计."
---

# cpd 化学势时效审计与形成能逐项分解

## 何时用
检查 vasp-sop 化学势（standard_energies / composition_energies / target_vertices）是否过时，或需要把形成能逐项展开定位深负来源。

## 化学势污染机制（2026-08-13 实证）

1. cpd 相能量提取链：`pydefect mce` 读 **OUTCAR** → composition_energies.yaml → standard_energies.yaml / relative_energies.yaml → chem_pot_diag.json → target_vertices.yaml（全链一次性生成）。
2. **cpd 相 OUTCAR 会被"未收敛/错几何的 SOC 单点"覆盖**（实证批次：08-12 06:42-07:01 一批 cpd SOC 单点，INCAR.tuned: NSW=0, EDIFF=1e-07, NELM=100, LSORBIT=True，未收敛（DAV 耗尽无 reached required accuracy）且几何错（SP 能量高于已收敛弛豫终点，stage2 不复制 CONTCAR→POSCAR 的同款 bug 落在 cpd 上））→ mce 读到坏值 → standard_energies 元素参考系统性偏移。
3. 实证元素参考偏差：Y2Ti2O7 Ti +0.13 eV/atom（−4.875 应为 −5.006）、La2SrSc2O7 La +0.29（−5.58 应为 −5.29）、Sr +0.09。O/Bi/Sc/Y 小；个别 Bi 相偏差 −10.4 eV（Ti8Bi9）影响顶点选择。
4. 影响量级：形成能 Σstd·v 项偏移 0.1–0.3 eV —— **不足以解释深负（−6~−7 eV）**——污染是数据卫生问题，不是深负主因（深负在 E_diff）。

## 审计步骤（只读）

```bash
# 1. 元素参考对比：composition_energies.yaml（per-atom）vs 各相最新收敛 log F=（权威）
#    注意 mol_O2 的 compE 含 +1.374 校正（raw log 值 + 校正 = compE，不算过时）
# 2. 检查 cpd 相 OUTCAR mtime vs 最新收敛 log mtime（OUTCAR 被未收敛作业覆盖 = 中招）
# 3. 确认标准能量影响：对目标缺陷手算 ΔE_f = −Δ(Σstd·v)
```

- **逐相对比**：对 cpd/<phase_dir>，取该相最新**收敛** log（`reached required accuracy` + `F=` 帧），对比 composition_energies.yaml 中该相的 energy（按化学式归一到每 f.u.；O2 加 O2_CORR 1.374 再除原子数）。偏差 >0.02 eV = 污染。
- **元素参考量化**：Bi2/O2/Ti3/Y2（或 La4/Sc2/Sr2）等元素相，对比旧值 vs 最新收敛 log，得 eV/atom 偏差 → 估算对 Σ std·v 的影响。
- **识别污染批次**：OUTCAR mtime 晚于收敛 log 的未收敛作业（NSW=0/EDIFF=1e-07 特征），其 OUTCAR 尾部 DAV 耗尽无 reached accuracy。
- **修复路径**：恢复各相收敛几何（git snapshot CONTCAR→POSCAR）→ 放宽 EDIFF（1e-6 或保守 1e-7）→ NSW=0 重跑 SOC 单点 → 收敛后删 composition_energies/standard_energies/relative_energies/chem_pot_diag/target_vertices + 重跑后处理（`vasp-sop defect analyze` 或 cpd 刷新路径）。

## 形成能逐项分解（定位深负来源）
$$E_f = E_{def} - E_{perfect} + q\cdot vbm - \sum_k std_k v_k$$
- E_def/E_perfect：calc_results.json（= 最新收敛 log F=，OUTCAR 被 fetch 截断不可信）
- q·vbm：unitcell/unitcell.yaml
- Σstd·v：cpd/standard_energies.yaml × atom_io（如 Ti_Y6_1: std_Ti·(+1)+std_Y·(−1) = +1.952）
- **深负裁决顺序**：q=0 缺陷（如 Y_Ti6_0）无 vbm 项仍深负 → 与电荷补偿无关；化学势项量级 2-5 eV → 不是主因；E_diff 是唯一深负载体。
- 中性反位（NELECT=perfect 相同、几何同 basin、条件逐字段同）E_diff 仍 −8 eV → 剩余嫌疑 = U 施加在 d0 宿主的电子结构不对称；验证 = U=0 对照实验（NSW=0 SP，几何已收敛）。

## 陷阱

- cpd 相目录**没有 calc_results.json**——能量权威源是 `composition_energies.yaml`（mce 产物），mtime 晚不代表值新（读的是被覆盖的 OUTCAR）。
- OUTCAR 尾部 `F= grid` 不是能量帧；log 的 `F=` 帧才是。未收敛 SP 也打 `CRISP_COMPLETED`（fetch 不判电子收敛）。
- 全树扫描用"最新**收敛** log"而非最新 log（未收敛作业会冒充）。

## 执行条件可比性检查清单（OUTCAR 头部回显，非 INCAR.tuned）
NSW/ISIF/ISMEAR/SIGMA/ENCUT/EDIFF/EDIFFG/IBRION/ISPIN/LSORBIT/ISYM/LORBIT/PREC/ALGO/LREAL/NELM/NELECT/LDAU/LDAUU/LDAUL/LMAXMIX/ISTART/ICHARG/NCORE/KPAR/GGA/LASPH/LCHARG/LWAVE。
陷阱：ISPIN=1 + LSORBIT=T 是 SOC 非共线的正常回显（非异常）；"ISPIN = 1 spin polarized calculation?" 是描述行，取最后出现。

## 结构可比性
- 宿主原子：defect CONTCAR vs perfect CONTCAR 物种感知最近邻位移（rms=0.000 = 同 basin；反位原子本身 >2 Å 是"无对应位点"假象，正常）
- 反位原子须坐在目标格点（与 perfect 对应位点距离 0.0 Å）
- NELECT 数学：ZVAL 用 POTCAR 实际值（Ti=4 非 12，Ti_sv=12；Y_sv=11；O=6），原子数 × ZVAL ∓ q

## 相关
- defect 侧同源过时：vasp-sop-stale-analyze-chain-rebuild（calc_results.json 复用 + efnv/dei exists-skip）
- 深负 E_diff 判定链：vasp-sop-formation-energy-sanity-audit