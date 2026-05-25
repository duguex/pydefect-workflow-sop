# doped ⇄ pydefect: 工作流对比与集成指南

> **更新日期:** 2026-05-17
> **doped 版本:** 3.2.1 (conda env: `doped`)
> **pydefect 版本:** 0.9.12 (conda env: `pydefect`)
> **shakenbreak:** 3.4.4 (与 doped 同 env)
> **pydefect 在 doped 环境: 未安装** — `pip install pydefect` 可获得 eFNV 校正支持

---

## 核心范式差异

| 方面 | pydefect | doped |
|------|----------|-------|
| **接口** | CLI 命令驱动 (`pydefect`, `pydefect_vasp`, `pydefect_util`) | **Python API** 驱动（无 CLI） |
| **执行方式** | shell 脚本 + 手工命令序列 | Python 脚本 / Jupyter notebook |
| **缺陷识别** | 从目录结构 + `calc_results.json` 逐目录解析 | **从弛豫后结构自动推断**（即使非 doped 生成） |
| **超胞生成** | `pydefect supercell` 手动执行 | `DefectsGenerator` 内部自动完成 |
| **电荷态猜测** | 能带边自动选取，可在 `defect_in.yaml` 手动裁剪 | 化学直觉自动猜测 + 可手动 `add_charge_states()` / `remove_charge_states()` |
| **基态搜索** | ❌ 无内置 | ✅ **ShakeNBreak 强制集成** |
| **VASP 输入** | `vise vasp_set` (CLI) | `DefectsSet` (API) — 多阶段 (gam→nkred→std) |
| **CPD/化学势** | `make_poscars` → `make_composition_energies` → `standard_and_relative_energies` → `cpd_and_vertices` (手工流程) | `CompetingPhases` (MP 半自动) → `CompetingPhasesAnalyzer` |
| **结果解析** | 分步: `calc_results` → `efnv` → `defect_energy_infos` → `defect_energy_summary` | 一步: `DefectsParser(output_path)`, 自动含校正 |
| **形成能图** | `plot_defect_formation_energy` (有限定制) | `thermo.plot()` (内置出版质量) |
| **热力学分析** | ❌ 无 | ✅ `FermiSolver`: 载流子浓度 vs 温度、退火模拟、淬火 |
| **对称性分析** | ❌ 无（需手动检查 correction.pdf） | ✅ 自动 `get_symmetries_and_degeneracies()` |
| **中间态** | ❌ 不支持 | ✅ 内置标记和处理 |
| **可重复性** | 无内置 | 内置 `to_json()`, 完整的计算元数据追踪 |

---

## 工作流阶段逐项对比

| 阶段 | pydefect (CLI) | doped (Python API) | 选哪个？ |
|------|---------------|-------------------|---------|
| 结构准备 | 手动 → `vise vasp_set` | 手动 → 收敛测试 | **一样** — 两者都不自动 |
| 超胞生成 | `pydefect supercell -p <prim> --max_atoms <N>` | `DefectsGenerator` 自动（可 `supercell_gen_kwargs` 控制） | **doped** 更省事，pydefect 更可控 |
| 缺陷集定义 | `pydefect defect_set [-d dopant]` → `defect_in.yaml` | `DefectsGenerator(structure=prim, extrinsic=...)` | **doped** 自动化程度高，pydefect 的 YAML 可读性好 |
| 间隙位 | `local_extrema` → `add_interstitials_from_local_extrema` | `interstitial_coords` 参数或 `InterstitialGenerator` | **pydefect** 更成熟（有可视化），doped 的直接传坐标更直接 |
| VASP 输入 | `vise vasp_set -t defect` | `DefectsSet().write_files()` | **doped** 多阶段输入优秀，pydefect 的 vise 更灵活 |
| 基态搜索 | 手动（需外挂 ShakeNBreak） | **内置** ShakeNBreak 集成 | **doped** 完胜 |
| CPD/化学势 | `make_poscars` → VASP → `make_composition_energies` → `standard_and_relative_energies` → `cpd_and_vertices` (全手动，不幂等) | `CompetingPhases` (MP 自动查询) → VASP → `CompetingPhasesAnalyzer` | **doped** 更系统，但 pydefect 的 CPD 图（`cpd.pdf`）更直观 |
| 结果解析 | 分5+步 CLI 命令 | `DefectsParser` 一步 | **doped** 完胜 |
| 电荷校正 | `pydefect efnv` (需 `-pcr -u`) | 自动集成在 `DefectsParser` 中 | **doped** 更省事 |
| 形成能 | `defect_energy_infos` → `defect_energy_summary` → `plot_defect_formation_energy` | `DefectThermodynamics(defect_entries, chempots).plot()` | **doped** 一体化，pydefect 的分步更适合脚本化 |
| 能级分析 | `perfect_band_edge_state` → `band_edge_orbital_infos` → `band_edge_states` | `thermo.get_transition_levels()` | **doped** 简洁 |
| 热力学 | ❌ 无 | `FermiSolver`, `get_equilibrium_fermi_level()`, 浓度 vs T | **只能用 doped** |

---

## 何时用哪个？

### 首选 doped 的场景
- ✅ 项目从零开始
- ✅ 需要 ShakeNBreak 基态搜索（新项目强烈建议）
- ✅ 需要热力学分析（载流子浓度、退火温度等）
- ✅ 需要自动化缺陷识别（尤其是非 doped 生成的 VASP 结果）
- ✅ 后处理需要一步到位 + 出版质量作图
- ✅ 需要对称性/简并度自动分析

### 首选 pydefect 的场景
- ✅ 已有大量 pydefect 结果（不迁移）
- ✅ 需要间隙位的 AECCAR 分析（pydefect 有完整工具链）
- ✅ 需要 CPD 可视化图版（`cpd.pdf`）
- ✅ 习惯 CLI 工作流，不喜欢写 Python 脚本
- ✅ 需要精细控制每步的手动检查
- ✅ 非磁性非金属体系

### 混合使用的最佳实践

```
doped 的强项 → 用 doped
  ├── 缺陷生成 (DefectsGenerator)
  ├── 基态搜索 (ShakeNBreak)
  ├── VASP 输入 (DefectsSet — 多阶段)
  ├── 结果解析 (DefectsParser — 自动识别)
  ├── 热力学 (FermiSolver)
  └── 出版质量绘图 (thermo.plot())

pydefect 的强项 → 沿用 pydefect
  ├── 间隙位分析 (AECCAR → local_extrema)
  ├── CPD 可视化 (cpd.pdf)
  └── 已有 pydefect 项目增量扩展
```

> ⚠️ **不建议混用两者做同一批 CPD** — 两者的化学势计算方法和输入输出格式完全不同。建议全流程用其中一个，或在 CPD 阶段用 pydefect 生成 `standard_energies.yaml` 后传给 doped（需写转换脚本）。

---

## 关键区别陷阱

### 1. Python API vs CLI

**pydefect** 的命令可以逐条在 shell 中执行，适合交互式调试。  
**doped** 的所有操作在 Python 中，需要写 .py 脚本或 Jupyter notebook。

```python
# doped 典型用法 — 写在 .py 或 notebook 中
from doped.generation import DefectsGenerator
from doped.vasp import DefectsSet
from doped.analysis import DefectsParser

defect_gen = DefectsGenerator(structure="POSCAR")
# ... 后续所有操作 ...
```

### 2. 环境分离

```bash
conda activate pydefect   # pydefect 0.9.12, vise 0.9.1
conda activate doped      # doped 3.2.1, shakenbreak 3.4.4, pymatgen 2026.3.23
```

**doped 环境没有 pydefect** — 如需 eFNV 校正需要额外安装。但 doped 本身有内置校正（FNV + eFNV），pydefect 并非必须。

### 3. 电荷态猜测策略不同

- **pydefect:** 基于能带边自动选择（VBM/CBM 决定可能的电荷态范围）
- **doped:** 基于化学直觉 + 氧化态概率
- **结果可能不同!** 交叉检查两种方法给出的电荷态，确保不遗漏

### 4. 超胞生成算法

- **pydefect:** `supercell --max_atoms <N>`，指定最大原子数，系统自动选超胞矩阵
- **doped:** 默认每个方向 ≥ 10 Å，自动选最小超胞；可通过 `supercell_gen_kwargs` 定制
- 对于高介电常数材料，doped 的可能偏小 → 需要手动增大

### 5. 命名规则

| 缺陷 | pydefect | doped |
|------|----------|-------|
| O 替 C, 荷电 0 | `O_C1_0` | `O_C` |
| O 替 C, 荷电 +1 | `O_C1_+1` | `O_C_+1` |
| 同上但有不等价位点 | `O_C1_0`, `O_C2_0` | `O_C_Td_0` (点群) |
| 间隙 O | — | `O_i_Td_O2.83` (含最近邻信息) |

doped 的命名更智能（自动加不等价位点区分符），但 pydefect 的数字索引更明确。

---

## 环境配置指南

### doped 环境 (已有)

```bash
conda activate doped
# 已安装: doped 3.2.1, shakenbreak 3.4.4, pymatgen 2026.3.23
# 未安装: pydefect

# 如需要 eFNV 校正（可选）
pip install pydefect
```

### 前置条件

```bash
# POTCAR 配置
python -c "from pymatgen.core import SETTINGS; print(SETTINGS.get('PMG_VASP_PSP_DIR'))"

# MP API Key
echo $MP_API_KEY  # 或用环境变量设置
```

---

## 快速参考: 对应命令/API

| 操作 | pydefect CLI | doped Python API |
|------|-------------|-----------------|
| 超胞生成 | `pydefect supercell -p <prim> --max_atoms <N>` | `DefectsGenerator(structure)` 自动 |
| 缺陷集 | `pydefect defect_set [-d dopant]` | `DefectsGenerator(structure, extrinsic=...)` |
| 添加掺杂 | `pydefect defect_set -d O` | `DefectsGenerator(structure, extrinsic=["O"])` |
| VASP 输入 | `vise vasp_set -t defect -d */` | `DefectsSet(defect_gen).write_files()` |
| 解析结果 | `pydefect_vasp calc_results -d */` | `DefectsParser(output_path="./")` |
| eFNV 校正 | `pydefect efnv -d */ -pcr perfect/... -u unitcell.yaml` | 自动（在 `DefectsParser` 中） |
| 形成能 | `defect_energy_infos` → `defect_energy_summary` → `plot_*` | `DefectThermodynamics(defect_entries, chempots)` → `.plot()` |
| 转变能级 | `band_edge_states` | `thermo.get_transition_levels()` |
| 竞争相 | `make_poscars` → VASP → `make_composition_energies` → ... | `CompetingPhases` → VASP → `CompetingPhasesAnalyzer` |
| 介电常数 | `vise vasp_set -t dielectric_dfpt` | 手动（与 pydefect 同方法） |
| 基态搜索 | ❌ 无 | `Distortions(defect_gen)` → SnB → `low_energy_defects` |
| 热力学 | ❌ 无 | `FermiSolver(thermo, bulk_dos)` |
