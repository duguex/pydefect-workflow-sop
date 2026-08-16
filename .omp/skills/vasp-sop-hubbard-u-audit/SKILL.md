---
name: vasp-sop-hubbard-u-audit
description: 审计 vasp-sop 实际执行的 DFT+U 配置，并设计成套、几何身份可审计的 U 开关对照实验
---

# vasp-sop DFT+U 审计与重算

## 权威源

- **vise U 表**：`libs/vise/vise/input_set/datasets/u_parameter_set.yaml`（LDAUU/LDAUL）
- **实际执行协议以目录中的 `INCAR.tuned` / OUTCAR 回显为准**，不要仅由 vise 表或 plan 推断。2026 batch 实证：Y2Ti2O7 production 实际有 `LDAU=True, LDAUU=0 4.0 0`，而 La2SrSc2O7 production perfect/defect 无 LDAU 标签。
- **vasp-sop U 元素列表**：`vasp_sop/materials/mp.py::_DTFU_FALLBACK`
- **ADR 0012**：plan 的 `hubbard_u` 字段已废弃；生成路径可能按元素自动加 U，但历史/生产目录必须查执行输入。

## 审计流程

1. 遍历目标体系的 `plan.yaml`、当前 `INCAR`、`INCAR.tuned`，必要时核对 OUTCAR 头部回显。
2. 对比 formula/dopant 元素、生成器 U 策略和实际 LDAU/LDAUL/LDAUU。
3. 分类：
   - 应有 U 但执行输入无 U：协议遗漏。
   - 生成策略与实际执行不一致：历史输入或特殊补丁，按执行输入报告。
   - perfect 与 defect/cpd U 配置不同：能量不可比，必须成套重算。

## U 开关对照实验

用于判断 +U 是否造成异常形成能时：

1. **先确认两侧是否真的有 U 差异。** 若 production 本身无 LDAU，重复提交所谓 U=0 作业没有信息量。
2. 成套选择同体系 `perfect` 与代表缺陷；比较裸能差 `E_diff = E_def - E_perfect`，不要只看含化学势的形成能。
3. 从每个 production 目录复制：
   - 当前收敛 `CONTCAR → POSCAR`
   - 同一 `POTCAR`、`KPOINTS`
   - `INCAR.tuned` 为基准，删除 `LDAU/LDAUL/LDAUU/LDAUTYPE/LDAUPRINT/LMAXMIX`
   - 保留 SOC 等其他协议，设置 `NSW=0, IBRION=-1, ISTART=0`
4. 用独立 `_experiments/` 目录，写 manifest 记录源目录以及 CONTCAR→POSCAR、POTCAR SHA256。
5. `crisp submit ... --skip-prefill`，防止结构预填覆盖实验 POSCAR。
6. 完成后从最新收敛 slurm log 的 `F=` 提取能量，计算每个缺陷相对其同协议 perfect 的 E_diff。

## +U 重算流程

1. 重生成 INCAR，并核验 LDAU/LDAUU/LDAUL、ISPIN、NELECT。
2. 清除会让磁盘收敛门误判的旧输出；保留收敛 CONTCAR，提交前复制为 POSCAR。
3. 重置 JobStore 后提交。
4. 验证闭环：OUTCAR 回显确认实际 U、电子/离子收敛、局域磁矩及新旧 E_diff。

## 陷阱

- 不要把“元素在/不在 vise 表”当成生产执行事实；历史生成器、补丁和 `INCAR.tuned` 可不同。
- U 对照必须 perfect+defect 成套；只改一侧无法解释 E_diff。
- 对本来已是 U=0 的体系再跑 U=0，不构成对照。
- SOC 单点需显式 `--skip-prefill` 并记录几何哈希，避免缓存或提交流程替换 POSCAR。
