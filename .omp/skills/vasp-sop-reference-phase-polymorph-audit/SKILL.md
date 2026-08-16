---
name: vasp-sop-reference-phase-polymorph-audit
description: "Verify a defect-pipeline plan's reference phase is the lowest-e_above_hull MP polymorph and the on-disk host matches it via StructureMatcher before trusting formation energies or debugging deep-negative values. Diagnose/correct rebuilt trees, audit poscar_src across a batch. Use for vasp-sop/2026 batch plans, new poscar_src selection, or any host-identity doubt."
---

# vasp-sop 参考相（polymorph）选取审计 / 宿主参考正确性

## 何时用（gate：信任任何形成能之前先跑）
- 缺陷形成能系统性深负 / 集体重构 / perfect-vs-defect 结构严重偏移，且机械执行条件（POTCAR/INCAR/NELECT/KPOINTS）全过仍物理错误。
- **在信任任一缺陷形成能、或开 debug 深负之前**，先做宿主身份检查——这一项检查已作废整个批次（ADR 0023，issue #149）。
- 全批审计 plan.yaml 的 poscar_src 是否都选了正确多形体；新参考相/poscar_src 选择前。
- 触发：深负形成能、结构参考不对、宿主身份可疑、polymorph/参考相/多形体选取。

## 根因（代码 bug，已修复）
`vasp_sop/core/config.py::generate_config` fallback **曾**直接取搜索第一条：
```python
mpid = docs[0].material_id   # 无稳定性排序
```
1. 主路径 `pydefect_vasp mp -e <els> --e_above_hull 0.0005` 下载候选相；目标公式所有多形体 e_above_hull>0.0005 时（带 +U 元素/烧绿石常见），主循环 is_target 永不成立 → fallback（plan.yaml 注释"Available phases"无目标相 = 触发证据）。
2. MP `materials.summary.search(formula=...)` 默认按 material_id 升序 → `_docs[0]` = 最小 ID 多形体，往往是最不稳定相。
3. **已修复（commit 56781b4）**：fallback 按 `energy_above_hull` 升序取最低相，>0.05 eV/atom 时 logger.warning；回归测试 `tests/test_config.py::TestReferencePhaseSelection`。ADR `docs/adr/0023-reference-phase-host-identity.md` / issue #149。
4. **旧树不受代码修复影响**：已生成的树仍建在错误宿主上，必须重建。

## 影响实证（2026-08-14 全批 10 体系 MP 复核）
| 体系 | 实际使用 | e_above_hull | 应使用（最低相） | e_above_hull |
|---|---|---|---|---|
| Y2Ti2O7 | mp-1173093（P2 单斜） | 0.162 | mp-5373（Fd-3m 烧绿石） | 0.011 |
| BaAl4O7 | mp-1019532（Pnma 排列A） | 0.0239 | mp-1019534（Pnma 排列B） | 0.0008 |
- 其余 8 体系 e_above_hull=0.000 或 MP 唯一条目（SrLa2Sc2O7=mp-1218245, 0.0421, 唯一 entry，非 bug），**未命中**。
- **勿再假设"烧绿石族高概率中招"**：La2Zr2O7=mp-4974、Y2Sn2O7=mp-3370、CaAl4O7=mp-4867、BaAl2B2O7=mp-9844 等均实测为公式最低相。
- 两个命中案例都是**宿主身份错误**（拓扑不同）：Y2Ti2O7 是 P2 vs Fd-3m；BaAl4O7 同 Pnma 但 Wyckoff/O 亚晶格排列不同（StructureMatcher fit=False）。**不是应变/应力差异**（0.75% 体积差应变能 ~meV，解释不了 1.1 eV/胞能差）。

## 审计流程
1. 读 plan.yaml 的 `poscar_src`（记录 MPID）。
2. 取宿主结构：`unitcell/structure_opt/CONTCAR`（或 `cpd/<main>/POSCAR`），pymatgen SG（symprec=0.1）。
3. MP 查该 ID 与公式全集（默认序 + e_above_hull）：
```python
from mp_api.client import MPRester
import os
with MPRester(os.environ['MP_API_KEY']) as m:
    docs = m.materials.summary.search(formula=FORMULA, fields=["material_id","formula_pretty","energy_above_hull","symmetry","volume","nsites"])
```
   - 目标 ID eah>0 且公式存在更低 eah 多形体 → 命中。
   - 公式只有一条目 → 不是 bug，只是 meta-stable 化合物。
4. **宿主身份拓扑核验**（名/度量会骗人）：
```python
from pymatgen.analysis.structure_matcher import StructureMatcher
sm = StructureMatcher(ltol=0.3, stol=0.5, angle_tol=5)
sm.fit(local, mp_struct); sm.get_rms_dist(local, mp_struct)
```
   - Y2Ti2O7 实证：本地与 mp-1173093 fit=True（rms 0.15），与 mp-5373 fit=False。
   - **relax 后 rms 0.15 量级仍算同拓扑**（stol 放宽 0.6 / angle_tol 8 复核）。
5. **判定**：命中的 MPID 是否最低 e_above_hull？**fit=True 到最低相 = 宿主身份正确；fit=False = 错误，从正确参考相全量重建，旧结果归档作废、绝不合并混用。** 不是 → 换错多形体，必须重建。
6. 全批影响范围：逐一查全部 poscar_src（2026 批实证命中 2/10）。

## 陷阱
- 88 原子伪立方度量（10.08×10.08×10.63 Å、角度≈90）会掩盖 P2/低对称内部序——度量相像 ≠ 拓扑相同（P2 4 ops vs Fd-3m 192 ops），必须 StructureMatcher 定论。
- 只信 OUTCAR 回显一致不够：只证执行参数，不证母相身份。
- MP 默认序无稳定性含义；选相必须显式按 energy_above_hull（代码已如此，审计时复核旧树）。
- 参考相本身高于凸包（>~0.05 eV/atom）时也必须记录，不得静默接受 `_docs[0]` 式选取。

## 物理后果（宿主错 = 全部结果废）
- eah 差 → Y2Ti2O7 88 原子整体 ~13 eV/胞；BaAl4O7 ~1.1 eV/胞。
- 缺陷在低对称 meta-stable 宿主上弛豫进更深 basin → 深负 E_diff、5–10 eV 弛豫能、>1 Å 集体重构。**这些是宿主错误，不是物理。**
- 错误宿主上做的超胞/ISIF 诊断（12 Å、ISIF=3、应力测试）结论**不迁移**到正确宿主——作废重做。

## 修复方向（ADR 0023 采纳）
- plan.yaml `poscar_src: MP <正确ID>`（Y2Ti2O7→mp-5373、BaAl4O7→mp-1019534）→ 全量重建：unitcell（ISIF=3 定平衡晶格）→ supercell（doped 常规胞 88 原子起步，门不过再更大）→ cpd 主相 → defect（枚举/NELECT/链条全重来；cpd 竞争相集不动，但要干净重算，勿复用旧 OUTCAR）。
- 新协议：perfect ISIF=3 平衡晶格；缺陷一律 ISIF=2 固定；**ENCUT 按体系默认（plan encut: null），不固定跨体系值（用户裁定 2026-08-14：ENCUT 跟随体系）**。
- 验收门（任一不过即回查）：配对反位反应 > −0.5 eV；E_diff 中位 ∈ +3…+9 eV；E_perfect 每 f.u. 对照 cpd 主相 ≤ ±0.05 eV；CSV 带 protocol 列（宿主 mp-id / ENCUT / 胞协议）。
