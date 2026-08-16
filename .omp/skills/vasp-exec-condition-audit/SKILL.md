---
name: vasp-exec-condition-audit
description: "只读审计 vasp-sop/crisp 批次中 perfect 与缺陷（或批次内任意目录）的执行条件一致性：OUTCAR 回显逐 key 对比（含默认值块/描述行解析陷阱）、LDAU/MAGMOM/KPOINTS/POTCAR 序、slurm log 取磁矩与收敛（crisp 截断 OUTCAR 尾部）、perfect-vs-unitcell 每 f.u. 参照能量检验、SOC 覆盖分层盘点。用于\"我怀疑 perfect 和缺陷计算条件不同\"\"结果都是 SOC 吗\"类问题。"
---

# VASP 批次执行条件一致性审计（只读）

## 何时用
- 用户怀疑 perfect/缺陷（或两批目录）计算条件不同
- 形成能异常（深负、跨体系不可比）需先排除条件差异
- "计算结果都是 SOC 吗"类 SOC 覆盖盘点

## 核心原则
**执行真相 = OUTCAR 头部回显（最后一次出现），不是盘面 INCAR**。crisp fetch 可能改写 INCAR；INCAR.tuned 是提交快照；OUTCAR 回显是 VASP 实际读入值。三者关系：`INCAR.tuned == OUTCAR 回显` 应全 0 差异（执行链路健康）；盘面 INCAR 可陈旧（再生成后未重跑）。

## OUTCAR 回显解析陷阱（重要）
1. 头部前 ~30 行是 VASP **默认值块**（IBRION=-1、ISPIN=1、LSORBIT=T 等假值）→ 真实 INCAR 回显在默认块之后，**取每个 key 最后一次出现**
2. 只接受单 token 值：`IBRION = -1 ionic relax: 0-MD 1-quasi-New 2-CG` 这类带描述的行必须跳过（正则：值内含空格且非纯数字串即跳过）
3. 归一化：去尾部 ';'；科学计数等价（1e-4 == 0.1E-03）；PREC 大小写；ENCUT 回显 1 位小数（348.247 → 348.2）
4. 多 token key（LDAUU/LDAUL/MAGMOM）单独处理：对比时注意物种数差异（Bi 缺陷多一个元素，U 映射应正确）

## 磁矩与收敛：用 slurm %j.log，别信 OUTCAR 尾部
- crisp fetch 截断 OUTCAR 尾部 → "reached required accuracy"、mag 可能丢失
- 最新 log 取最后 `F= ... mag=` 行；**ISPIN=1 的 run 无 mag= 字段**（正常，非错误）
- 每目录多个 log：按 mtime 排序取最新；注意最新 run 可能是废 run（apptainer 启动失败、NSW 耗尽重复提交），需与 OUTCAR 能量交叉（OUTCAR 才是 summary 取数值）

## 对比维度
| 维度 | 内容 |
|---|---|
| INCAR 回显 | NSW/SIGMA/ISPIN/LSORBIT/ENCUT/EDIFF/EDIFFG/IBRION/ISIF/NELM/NELECT/ISYM/LORBIT/PREC 逐 key |
| +U | LDAUU/LDAUL/LDAUTYPE（多 token，注意物种序） |
| MAGMOM | 是否设置（一致缺失 = 一致，但 Gd 等需初始化） |
| KPOINTS | 全文（含模式行），抓 2×2×2 混入 1×1×1 的特例 |
| POTCAR | TITEL 变体 + ZVAL 序 vs POSCAR 物种序（错位致命）；Bi 缺陷追加物种是正常差异 |
| 晶格 | 超胞体积 vs unitcell 弛豫体积 ratio（~1.000 正常）；晶胞参数 |

## 参照能量决定性检验
`E_perfect/每f.u. − E_unitcell参照/每f.u.`：若 ~0（±0.6 eV 内），perfect 参照无系统性偏高 → 深负能量不是参照问题。注意 unitcell 倍率（supercell/unitcell 原子数比）。

## E_diff 跨体系判据
`E_diff = E_def − E_perfect = Ef − q·vbm + Σstd_k·v_k`（从 summary 条目还原）。跨体系分布是第一判据：非 SOC 体系全正（中位 +7~9 eV），SOC 体系深负（反位双向 −9 eV）→ 物理不可信。

## SOC 覆盖分层盘点
- defect 层：summary 每个条目回查执行 LSORBIT（两阶段协议：最终腿应 SOC）
- cpd 层：化学势相应 SOC（含 Bi 相必须，La2Bi 类金属间相尤其）
- unitcell 层：band（VBM 来源）与 dielectric（pc 修正）应 SOC
- 输出所有非 SOC 混入条目清单（每条例出 LSO/NSW）

## 产出
差异清单按"可枚举的正常差异（协议组）" vs "真实异常（需处理）"分类；给出每个异常的影响量级。修复决策留给用户。
