# ADR 0023 — 参考相必须取凸包最低多形体（宿主身份错误重构）

- 状态：已接受（2026-08-14）
- 关联：ADR 0012（+U 永远打开）、ADR 0014 / ADR 0022（两阶段 SOC 阶段 2 = SOC 弛豫）、ADR 0019（git 输入快照）、CONTEXT.md 词条「参考相」「宿主身份」

## 背景

2026-08-13 对 Y₂Ti₂O₇ 的深负形成能诊断进入死胡同：深负 (−5…−10 eV)、集体重构（RMS 0.5–1.2 Å、>1 Å 原子十余个）、单轮弛豫能 −4.6…−6.2 eV 全部指向“宿主在错误结构 basin”。12 Å 超胞与 ISIF=3 两轮对照实验都无法消除。

2026-08-14 查明的根因：**宿主结构身份错误**。`generate_config`（`vasp_sop/core/config.py`）选取目标相的逻辑为：

```python
_docs = _mpr.materials.summary.search(formula=formula, fields=["material_id", "formula_pretty"])
_mpid = _docs[0].material_id   # 直接取第一条，无稳定性排序
```

MP 公式搜索默认按 material_id 升序返回，`_docs[0]` = id 最小的多形体，**不是凸包上最稳定的多形体**。全批次 10 体系复核（MP API，2026-08-14）命中 2 个体系：

| 体系 | 实际使用 | e_above_hull | 应使用（最低相） | e_above_hull |
|---|---|---|---|---|
| Y₂Ti₂O₇ | mp-1173093（P2 单斜，44 原子原胞） | 0.162 eV/atom | mp-5373（Fd-3m 烧绿石） | 0.011 eV/atom |
| BaAl₄O₇ | mp-1019532（Pnma，畸变排列） | 0.0239 eV/atom | mp-1019534（Pnma，另一排列） | 0.0008 eV/atom |

关键点：两个案例均为**宿主身份错误**（结构拓扑不同，非晶格度量/空间群符号可区分）。Y₂Ti₂O₇ 的 P2 宿主 88 原子胞 10.08×10.08×10.63 Å 度量与烧绿石常规胞几乎一致，掩盖了内部序差异；BaAl₄O₇ 两个相同为 Pnma，但 Wyckoff 排列不同（StructureMatcher fit=False）。

**宿主的能量惩罚**：Y₂Ti₂O₇ 约 0.151 eV/atom（88 原子 ≈ 13 eV/胞）；BaAl₄O₇ 约 0.023 eV/atom（48 原子 ≈ 1.1 eV/胞）——直接压入形成能并驱动缺陷进入错误弛豫 basin。

## 决策

1. **代码修复**（`config.py` fallback）：公式搜索后按 `energy_above_hull` 升序取最低相，不再取 `_docs[0]`；若最低相 e_above_hull > 0.05 eV/atom，打告警日志要求人确认。对“唯一条目”体系不受影响。
2. **重建范围**：Y₂Ti₂O₇ 用 mp-5373（Fd-3m），BaAl₄O₇ 用 mp-1019534；两体系全量重建（unitcell → supercell → cpd 主相 → defect 枚举 → 全部缺陷 → 解析产物）。旧缺陷树/解析产物全部归档作废，不迁移不混用。
3. **重建协议**（仅重建两体系；其余 8 体系维持现状，结果表带 protocol 标签不静默合并）：
   - 宿主晶格：~~perfect 先 ISIF=3 弛豫得到平衡晶格~~ **（2026-08-17 修订：不再进行 perfect ISIF=3 弛豫，也不再做晶格更新——晶格在超胞构建时一次定死（unitcell CONTCAR），perfect 与全部缺陷一律 ISIF=2 固定该晶格；`sync_lattice_from_perfect` 已从代码移除，见 builder.py / orchestrator.py）**；
   - 缺陷：一律 ISIF=2 固定该晶格；
   - 超胞：Y₂Ti₂O₇ 从 88 原子常规胞（min_distance=10）起步，验收门不过再升级；
   - ENCUT：按体系默认（plan `encut: null`），不固定跨体系值（2026-08-14 用户裁定：ENCUT 跟随体系）；其余电子参数沿用（PBEsol、Ti U=4、两阶段 SOC per ADR 0014/0022）。
4. **验收门**（任一不满足即回查，不逐级放宽）：
   - 配对反位反应 E(Ti_Y+q)+E(Y_Ti−q)−2E_perfect > −0.5 eV；
   - E_diff = E_def−E_perfect 全体中位落于 +3…+9 eV；
   - E_perfect 每 f.u. 对照 cpd 主相 ≤ ±0.05 eV。
5. **结果口径**：712 条形成能 CSV 中重建体系带 protocol 列（宿主 mp-id / ENCUT / 胞协议），禁止与旧基面静默混合。

## 范围与影响

- 直接失效：Y₂Ti₂O₇ 全部 163 个已算缺陷 + BaAl₄O₇ 全部 82 个已算缺陷 + 两体系全部解析产物（calc_results/summary/HTML/CSV 行）。
- 之前基于 P2 宿主的 12 Å、ISIF=3 诊断实验**全部作废**，不可作为新宿主设计依据。
- cpd 竞争相集合不受影响（与宿主无关），仅主相需按新参考相重取。

## 代价与风险

- 重建成本：Y₂Ti₂O₇/BaAl₄O₇ 两体系全量（各 ~100–200 目录,两阶段 SOC），数天量级。
- 若 88 原子胞未过验收门 → 升级 12 Å（176 原子）成本加倍。
- 其余 8 体系仍在旧协议基面，跨体系对比必须显式标注基面差异。

## 执行记录

- 诊断与复核：2026-08-14 会话，MP API 复核 10 体系；StructureMatcher 判定两案例宿主身份错误（Y₂Ti₂O₇: local fit mp-1173093=True / mp-5373=False；BaAl₄O₇: local fit mp-1019532=True / mp-1019534=False）。
- 术语：CONTEXT.md 新增「参考相」「宿主身份」。
- 2026-08-17 修订：perfect ISIF=3 弛豫 + 晶格同步导致 Li₂ZnGe₃O₈ analyze 卡死（defect 与 perfect 弛豫晶格 0.36% 分歧，pydefect efnv `SupercellError`，174/174 correction 失败）——策略改为 perfect 不再 ISIF=3、不再做晶格更新；代码移除 `sync_lattice_from_perfect`（builder.py/orchestrator.py）与 perfect ISIF=3 patch，check_results.py 的 perfect 豁免同步删除。