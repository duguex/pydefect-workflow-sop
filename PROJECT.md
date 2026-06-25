# PROJECT.md — VASP SOP

---

## 0. 设计哲学 / 操作规范

1. **最小工作量, Be wise**
2. **避免重复造轮子**
3. **过程透明,可审查的中间结果/log/缓存**
4. **规模化测试前进行最小验证**
5. **确定性程序优于 LLM agent 自由发挥**
6. **慎防 plan 没有细节**

## 1. 项目目标

1. 解构、模块化现有成熟第一性原理计算项目
2. 构建不同种类的vasp计算的sop
3. 通过编排实现更复杂的计算项目:
   - 点缺陷性质计算工作流:给定化学式与可选掺杂元素,自动完成从完美晶胞优化到形成能/转变能图谱的端到端计算
   - 声子性质、电声耦合、激发态、线形计算
4. 一套将新的计算或程序转化为sop的方法论

---

## 2. 相关组件 libs/

1. pydefect / pydefect.complex — 缺陷框架、eFNV 校正、形成能、转变能
2. doped — 缺陷框架、超胞选择、缺陷枚举
3. vise — VASP 输入生成 (INCAR/POTCAR/KPOINTS)
4. pymatgen — 通用材料计算
5. spglib — 空间群
6. ASE — 通用材料计算
7. Materials Project — 材料数据库
8. phonopy — 声子性质
9. **crisp** — 计算资源管理。所有作业操作必须通过 `crisp` CLI（`crisp submit / cancel -n TASK_NAME / jobs`），不得直接 `scancel`/`sbatch`。Agent 操作前需加载 skill://crisp。
10. VASP — DFT 程序
11. SQLite — 计算结果缓存后端。`~/.vasp_sop/cache.db` + `calc_cache/`。只缓存 CONTCAR + calc_results.json（~10KB），不缓存完整 OUTCAR（~100MB）。

## 3. 点缺陷计算业务逻辑

### 3.1 物理目标
### 3.2 形成能公式

### 3.3 三个阶段

```
CPD  →  获取化学势 → target_vertices.yaml
Unitcell → 获取晶胞参考 → unitcell.yaml（VBM/CBM/介电常数/CONTCAR）
Defect → 缺陷计算 → defect_energy_summary.json
```

---

### 3.4 CPD 阶段（化学势图）

**输入**：化学式、掺杂元素、MP API

**步骤**：
1. `pydefect_vasp mp` 下载目标相 + 所有竞争相的 POSCAR/POTCAR
2. 各竞争相 VASP 计算（完全并行）
3. `pydefect_vasp mce` 汇总 composition_energies.yaml
4. 施加分子修正（O₂/Cl₂/F₂）
5. `pydefect sre` → standard_energies.yaml
6. `pydefect cv` → chem_pot_diag.json（求解化学势凸包）
7. `pydefect pc` → CPD 图

**输出**：`target_vertices.yaml`（每个化学势顶点一组 μ_i）

---

### 3.5 Unitcell 阶段（完美晶胞）

**输入**：目标相 CONTCAR（CPD 产物，**不重复跑 VASP**）

**步骤**：
1. 结构优化 → CONTCAR（实际上用 CPD 目标相的 VASP 结果，不重复）
2. Band 计算（并行）
3. DOS 计算（并行）
4. Dielectric 计算（并行）
5. 后处理: `pydefect_vasp u` → `unitcell.yaml`，`vise pb/pd/pdf` 出图

**输出**：`unitcell.yaml`（VBM/CBM/带隙/介电常数）

---

### 3.6 Defect 阶段（缺陷计算）

**输入**：CONTCAR（结构优化后）、unitcell.yaml、target_vertices.yaml、standard_energies.yaml

**本地步骤（秒级）**：
1. 构建超胞：`plan.yaml` 中 `supercell.tool` 控制超胞工具
   - `doped`（默认）：`get_ideal_supercell_matrix(min_image_distance=10Å)` → 50-150 原子
   - `pydefect`：`pydefect s --min_atoms 200 --max_atoms 600` → 200-600 原子
2. `pydefect ds` 枚举缺陷 → defect_in.yaml
3. `pydefect_vasp de` 生成缺陷结构目录（含 perfect 和所有电荷态）
4. （若 interstitials）`pydefect_util ai` 从 DOS extrema 确定填隙位（需 DOS 结果）

**后处理**：
8. `pydefect_vasp cr` → calc_results.json
9. `pydefect efnv` → eFNV 修正
10. `pydefect dei` → 代入形成能公式
11. `pydefect des` → defect_energy_summary.json
12. `pydefect pe` → 各化学势下形成能图

**输出**：`defect_energy_summary.json`、`calc_summary.json`

---

### 3.7 全部计算及依赖关系

以 GaN + Mg 掺杂为例：

| # | 计算 | 阶段 | 属于 | 依赖 | 并行组 |
|---|---|---|---|---|---|
| 1 | GaN_mp-804 (CPD目标相 = structure_opt) | CPD | VASP | 无 | Wave 1 |
| 2 | Ga_mp-142 | CPD | VASP | 无 | Wave 1 |
| 3 | N₂ | CPD | VASP | 无 | Wave 1 |
| … | 其他竞争相 (MgGa, Mg, Mg₃N₂, Mg₅Ga₂, Mg₂Ga₅, Mg₂Ga) | CPD | VASP | 无 | Wave 1 |
| | **超胞构建** `pydefect s` | Defect | 本地 | CONTCAR | Wave 1b |
| | **缺陷结构生成** `pydefect_vasp de` | Defect | 本地 | 超胞构建 | Wave 1b |
| 4 | band | Unitcell | VASP | CONTCAR | Wave 2 |
| 5 | dos | Unitcell | VASP | CONTCAR | Wave 2 |
| 6 | dielectric | Unitcell | VASP | CONTCAR | Wave 2 |
| 7 | perfect | Defect | VASP | 超胞构建 | Wave 2 |
| 8 | Va_Ga_0, Va_Ga_-1, …, Va_Ga_-3 | Defect | VASP | 缺陷结构 | Wave 2 |
| 9 | Va_N_0, Va_N_+1, Va_N_+2, Va_N_+3 | Defect | VASP | 缺陷结构 | Wave 2 |
| 10 | Mg_Ga_0, Mg_Ga_+1, Mg_Ga_-1 | Defect | VASP | 缺陷结构 | Wave 2 |
| 11 | Mg_N_0, Mg_N_+1 | Defect | VASP | 缺陷结构 | Wave 2 |
| | **填隙位确定** `pydefect_util ai` | Defect | 本地 | DOS | Wave 2b |
| 12 | i_Ga_0, i_Ga_+1（若有） | Defect | VASP | 填隙位确定 | Wave 2c |
| | **后处理** | Defect | 本地 | 全部 VASP | Wave 3 |

### 3.8 Wave 化调度

```
Wave 1: ── 目标相(=structure_opt) ──────────────────────┐  (并行)
          ── 其他所有竞争相 ─────────────────────────────┘
                                    │ CONTCAR 就绪
Wave 1b:   超胞构建 → 缺陷结构生成  (本地，秒级)
                                    │
Wave 2: ── band ── dos ── dielectric ────────────────────┐
          ── perfect ─────────────────────────────────────┤  (全部并行)
          ── 所有非填隙缺陷电荷态 ────────────────────────┘
                                    │
Wave 2b:   填隙位确定 (本地，秒级，需 DOS)
                                    │
Wave 2c: ── 填隙缺陷 VASP ─────────────────────────────── (尾部提交)
                                    │ 全部 VASP 完成
Wave 3:    后处理 (本地)
```

### 3.9 墙钟估算

```
无填隙：structure_opt + max(band, dos, diel, perfect, 单缺陷)
有填隙：structure_opt + max(dos + 填隙处理, band, diel, perfect, 单缺陷)
```

顺序执行 32 个 VASP → 两波并发后 ≈ 2 个串行 VASP 时长。

### 3.10 几个容易搞错的点

- **目标相 = structure_opt**，CPD 阶段已经跑过目标相的 VASP，其 CONTCAR 就是结构优化的结果。Unitcell 不应再跑一次。
- **各缺陷不需要等 perfect**。perfect 只在后处理（efnv 修正）中用到，VASP 阶段可以同时跑。
- **填隙需要 DOS**。不是 DOS 的 VASP 结果本身，而是 `volumetric_data_local_extrema.json`（AECCAR 的后处理产物），所以填隙必在 dos 之后。
- **竞争相 VASP 和后面所有计算无关**，它们只用来求化学势凸包。跑多快都无所谓，不影响主线。

---

## 4. CLI 参考

### 4.1 batch run

```bash
cd /path/to/project_root

# Dry-run: build defect structures and generate VASP inputs, no submission
vasp-sop batch run . --dry-run

# Real run: build + submit VASP (CPD → unitcell → defect)
vasp-sop batch run .

# Dry-run with custom poll interval (default 60s)
vasp-sop batch run . --poll 30 --dry-run
```

`--dry-run` 会并行（14 进程）处理所有体系，输出本应提交的 VASP 作业列表后退出。

## 5. TODO
