---
name: pydefect-workflow
description: >
  Point-defect VASP calculation SOP based on pydefect/vise. Covers perfect cell, competing phases, 
  supercell generation, defect entries, cluster submission, and full post-processing (eFNV correction, 
  formation energy plots, defect level analysis). Built from official docs + 3C-SiC full test.
version: 2.4.1
author: duguex
---

# PyDefect 点缺陷计算标准操作流程 (SOP)

> 版本: 2.4（defect_entry.json monty 序列化坑点 + 修复脚本 + diamond 2Va_C1 端到端验证通过）
> ⚠️ **MANDATORY**: 任何涉及 pydefect 缺陷计算的任务（生成、提交、后处理），**必须先加载本 skill**。结构生成用 `pydefect-complex` skill，VASP 设置必须用本 SOP 阶段 3.4 的 `vise vasp_set`，严禁手工构造 INCAR/POTCAR/KPOINTS。  
> 参考: [官方教程](https://kumagai-group.github.io/pydefect/) + 3C-SiC 全流程测试
> 参考文件: [DOS 命令官方对比](references/official-dos-command.md), [间隙位流程](references/interstitial-workflow.md), [CPD 扩展](references/cpd-extension.md), [doped 对比指南](references/doped-comparison.md), [defect_entry.json 修复](references/defect-entry-json-fix.md), [diamond 2Va_C1 验证](references/diamond-2va-c1-verification.md)  \n> 环境: pydefect 0.9.12, vise 0.9.1, pymatgen 2025.6.14
> 局限: 仅支持非磁性非金属 VASP 计算；替代工具链 **doped** (v3.2.1, Python API, 含 ShakeNBreak + 热力学) 见 [对比参考](references/doped-comparison.md)  
> 测试结果: [3C-SiC 测试报告](references/3c-sic-test.md)  
> 旧版自动化脚本（仅供参考）: `~/materials-research/skills/pydefect-workflow/scripts/`
> 环境验证: `scripts/verify-installation.sh` — 快速检查三大 CLI + 所有 Python 导入

---

## 目录结构

```
<project>/
├── pydefect.yaml           # 可选
├── vise.yaml               # 可选
├── unitcell/
│   ├── structure_opt/      # 结构优化
│   ├── band/               # 能带
│   ├── dos/                # DOS (+ AECCAR 用于间隙位)
│   └── dielectric/         # 介电 (DFPT)
├── cpd/                    # 竞争相
│   ├── <phase1>/
│   ├── <phase2>/
│   ├── composition_energies.yaml
│   ├── standard_energies.yaml
│   ├── relative_energies.yaml
│   ├── target_vertices.yaml
│   └── cpd.pdf
└── defect/                 # 缺陷
    ├── supercell_info.json
    ├── defect_in.yaml
    ├── perfect/
    ├── Va_X_0/ ...         # 各缺陷各电荷态
    └── results/
```

---

## 阶段 1: 完美晶胞 (unitcell/)

### 1.1 结构优化

```bash
cd unitcell/structure_opt/
# POSCAR → vise → VASP
vise vasp_set -x pbesol          # PBEsol 泛函
# 或指定泛函: -x {pbe,pbesol,lda,scan,pbe0,hse}
# ⚠️ ENCUT 需 1.3× max(ENMAX of all POTCARs)
# 如 SiC: Si=245, C=400 → max 400 × 1.3 = 520
```

### 1.2 能带 / DOS / 介电

```bash
cd unitcell/band/
vise vasp_set -x pbesol -t band -pd ../structure_opt

cd unitcell/dos/
# ⚠️ 间隙位分析需要 AECCAR0/2 (LAECHG) + LOCPOT (LVTOT) — 官方必有参数
vise vasp_set -x pbesol -t dos \
  -pd ../structure_opt \
  -uis LVTOT True LAECHG True KPAR 1

cd unitcell/dielectric/
vise vasp_set -x pbesol -t dielectric_dfpt -pd ../structure_opt
```

### 1.3 收集 unitcell.yaml

```bash
pydefect_vasp unitcell \
  -vb unitcell/band/vasprun.xml \
  -ob unitcell/band/OUTCAR \
  -odc unitcell/dielectric/OUTCAR \
  -odi unitcell/dielectric/OUTCAR \
  -n "<project>"
```

---

## 阶段 2: 竞争相 (cpd/)

### 2.1 下载结构

```bash
cd cpd/
pydefect_vasp make_poscars -e <元素> --e_above_hull 0.0005
# 若使用 MPD 数据直接生成 CPD（跳过 VASP）: 详见 FAQ
```

### 2.2 VASP 结构优化

```bash
# 统一 ENCUT
for d in *_*/; do
    (cd "$d" && vise vasp_set -x pbesol -uis ENCUT <统一值>)
done
# 已有目标化合物结果 → 链接/复制到 cpd/
```

### 2.3 生成 CPD

```bash
pydefect_vasp make_composition_energies -d *_*/
pydefect standard_and_relative_energies
pydefect cpd_and_vertices -t <target> -e <元素>
pydefect plot_cpd                                # 可选 → cpd.pdf
```

---

## 阶段 3: 缺陷生成 (defect/)

### 3.1 超胞

```bash
cd defect/
# POSCAR = conventional cell
# -p = primitive cell (标准化原胞)
pydefect supercell -p <primitive> --max_atoms <N>
# ⚠️ primitive 必须与 supercell_info.json 中的 unitcell_structure 一致!
```

### 3.2 缺陷集

```bash
pydefect defect_set                       # 所有可能缺陷
pydefect defect_set -k "Va_O"             # 正则筛选
pydefect defect_set -d Ca                 # 添加掺杂
# 输出: defect_in.yaml → 可手动裁剪电荷态
```

### 3.5 阶段性提速

如果超胞各方向 > 10 Å，**建议使用 Γ-only k-point (1×1×1) 代替收敛的 k-mesh**——此时能带折叠使 Γ 点已包含足够信息，能量误差通常 <1 meV/atom，计算量降至 1/4。

用 `vise vasp_set` 时，不指定 KPOINTS 则可能根据原胞收敛值自动折算（如 7×7×7 → 2×2×2）。如需 Γ-only，需手动设置 KPOINTS 或用模板。

3C-SiC 实测: 64 原子 9.5 Å 超胞用 2×2×2 → 如改 Γ-only 可省 4 倍机时。

需 AECCAR0+AECCAR2（来自 1.2 节的 DOS 计算，需 `LAECHG=True`，且官方还要求 `LVTOT=True` 生成 LOCPOT）。

**关键前提:** 生成 AECCAR 所用的原胞结构必须与 `supercell_info.json` 中的 `unitcell_structure` 完全一致（晶格常数匹配）。先用优化后结构重新生成 supercell，再做间隙位分析。

```bash
# 1. 从 AECCAR 找间隙位（官方文档: le -v AECCAR0 AECCAR2）
#    默认找电荷密度极小值（= 原子间空隙）
pydefect_vasp local_extrema \
  -v AECCAR0 AECCAR2 \
  -i "all_electron_charge"
# 输出: volumetric_data_local_extrema.json

# 2. 按官方文档用 pydefect_util ai 加入 supercell_info.json
#    ✅ 自动读 extrema JSON，无需手动传坐标/原胞
pydefect_util add_interstitials_from_local_extrema \
  --local_extrema volumetric_data_local_extrema.json \
  -i <索引1> <索引2> ...
# -i: 从 extrema 文件中选第 N 号间隙位加入

# 3. 移除（pop）间隙位
pydefect pop -i <idx> -s supercell_info.json       # 按索引移除
pydefect pop --pop_all -s supercell_info.json       # 移除全部

# 4. 重新生成 defect_in.yaml 以包含间隙位
pydefect defect_set
```

### 3.4 缺陷目录 + VASP 输入

```bash
pydefect_vasp defect_entries
# 每个缺陷+电荷态一个目录

vise vasp_set -t defect -x pbesol -d defect/*/ \
  -uis ENCUT <统一值> KPAR 4 NSW 80 EDIFFG -0.02
```

---

## 阶段 4: VASP 计算

提交到集群。建议先算中性电荷态，收敛后复制 CONTCAR 给其他电荷态加速收敛。

### 4.1 批量提交 (crisp)

⚠️ **不要用 `crisp submit` 命令行直接批量提交** — CLI 的 `cmd_submit` 使用 `Path.cwd()` 作为 `local_dir`，如果从根目录批量提交会导致所有 job 的 `local_dir` 都是同一个根目录，VASP 找不到 POSCAR/INCAR 秒退 EXIT_CODE:1。

**正确做法**：用 Python 脚本直接调 `register_job()`，每个目录传完整的绝对路径：

```python
# ~/.conda/envs/paramiko/bin/python 运行
import sys, uuid
sys.path.insert(0, "~/crisp")
sys.path.insert(0, "~/crisp/scripts")
from utils.db import get_job_db
manager = get_job_db()
for d in Path("defect_new").glob("*_0/"):
    manager.register_job(
        task_name=uuid.uuid4().hex[:8],
        local_dir=str(d.resolve()),
        status="submit",
    )
```

**原理**: `register_job` 只是往 SQLite 写记录，daemon 后续会自动分配集群、上传文件、提交 SLURM。不依赖 `cwd()`。

**原则**: 有 `defect_new/` 直接从中提交即可，**不需要把新目录复制/替换到旧 `defect/`**。旧目录保留做参考。

---

## 阶段 5: 后处理

### 5.1 同步结果

⚠️ **关键坑点**: crisp 把结果放 `output/` 子目录，但 `pydefect_vasp` 命令从根目录读取。

```bash
for d in defect/*/; do
    if [ -d "${d}output" ]; then
        cp "${d}output/vasprun.xml" "$d/"
        cp "${d}output/OUTCAR" "$d/"
        cp "${d}output/CONTCAR" "$d/"
        cp "${d}output/PROCAR" "$d/"
        cp "${d}output/EIGENVAL" "$d/"
    fi
done
```

### 5.2 依次执行

```bash
# (a) 解析 VASP 结果
pydefect_vasp calc_results -d defect/*/
```

如果 `pydefect_vasp calc_results` 不可用或报错，用 Python API 从 vasprun.xml 生成：

```python
from pymatgen.io.vasp import Vasprun, Outcar
from pydefect.cli.vasp.make_calc_results import make_calc_results_from_vasp
from pathlib import Path

for d in Path("defect/").glob("*/"):
    v = d / "vasprun.xml"; o = d / "OUTCAR"
    if v.exists() and o.exists():
        cr = make_calc_results_from_vasp(Vasprun(str(v)), Outcar(str(o)))
        cr.to_json_file(str(d / "calc_results.json"))
```

```bash
# (b) eFNV 电荷修正 (务必检查 correction.pdf 是否合理)
pydefect efnv -d defect/*/ -pcr defect/perfect/calc_results.json -u unitcell.yaml

# (c) 缺陷结构分析
pydefect defect_structure_info -d defect/*/

# (d) 能级分析（可选）
pydefect_vasp perfect_band_edge_state -d defect/perfect
pydefect_vasp band_edge_orbital_infos \
  -d defect/*/ -pbes defect/perfect/perfect_band_edge_state.json
pydefect band_edge_states \
  -d defect/*/ -pbes defect/perfect/perfect_band_edge_state.json

# (e) 形成能
pydefect defect_energy_infos \
  -d defect/*/ -pcr defect/perfect/calc_results.json \
  -u unitcell.yaml -s cpd/standard_energies.yaml

# (f) 汇总 + 绘图
pydefect defect_energy_summary \
  -d defect/*/ -u unitcell.yaml \
  -pbes defect/perfect/perfect_band_edge_state.json \
  -t cpd/target_vertices.yaml

pydefect plot_defect_formation_energy -d defect_energy_summary.json -l A
pydefect plot_defect_formation_energy -d defect_energy_summary.json -l B
```

---

## 阶段 6: 向已有项目添加掺杂（增量流程）

当项目已完成原生缺陷计算后，需要添加新的掺杂元素时，**不要从头开始** — 以下增量流程复用已有结果。

### 6.1 添加掺杂缺陷类型

```bash
cd defect/

# 1. 备份当前 defect_in.yaml（defect_set 会覆写！）
cp defect_in.yaml defect_in.yaml.bak

# 2. 添加掺杂元素
pydefect defect_set -d O                  # e.g. O、N、Al、Ca
# 新 defect_in.yaml = 原生缺陷 + O_C1, O_Si1, ...
```

- 电荷态由 pydefect 根据能带边自动选取
- 可多次执行 `defect_set -d` 添加多个掺杂元素

### 6.2 创建计算目录

```bash
# 只创建新缺陷的目录（已有目录自动跳过）
pydefect_vasp defect_entries
```

- 已有原生缺陷（C_Si、Si_C、Va_C、Va_Si）的目录**不动**
- 仅新增 O_C1_0/、O_Si1_-3/ 等目录

### 6.3 生成 VASP 输入

```bash
# 仅对新目录生成 INCAR/POTCAR/KPOINTS
vise vasp_set -t defect -x pbesol -d O_*/ \
  -uis ENCUT <统一值> KPAR 4 NSW 80 EDIFFG -0.02
```

- **ENCUT 必须与原生缺陷一致**，不然形成能系统偏移
- 新 POTCAR 自动包含掺杂元素（如 Si + C + O）
- 🆚 `defect/*/` 会对所有目录重新生成，耗时且没必要

### 6.4 提交脚本

```bash
# 从已有缺陷目录复制 submit.slurm
for d in O_*/; do cp C_Si1_0/submit.slurm "$d/"; done
# 检查 ENCUT 等参数是否一致
```

### 6.5 后处理 (VASP 计算完成后)

#### 更新 CPD（必需！）

新掺杂元素的化学势需要加到 CPD 中，否则 `defect_energy_infos` 会缺参考能量。

**⚠️ `make_poscars` 不幂等** — 如果 CPD 目录中有之前下载但未计算的空相目录（仅有 POSCAR + prior_info.yaml），第二次运行 `-e` 扩展元素时会 `FileExistsError`。**必须先清理：**

```bash
cd cpd/
# 删除未计算的多余相目录（只有结构下载了但没跑 VASP）
for d in */; do
  if [ ! -f "$d/CONTCAR" ] && [ ! -f "$d/OUTCAR" ] && [ ! -f "$d/.completed" ]; then
    rm -rf "$d"
  fi
done
```

然后添加掺杂竞争相：

```bash
# 下载新竞争相
pydefect_vasp make_poscars -e O --e_above_hull 0.0005

# VASP 计算掺杂竞争相（如 SiO₂, CO₂, O₂ 等），可复用已有项目 ENCUT
# O₂ 由 pydefect 自动生成 (mol_O2/)，含 ISPIN=2, NUPDOWN=2, ISIF=2

# 全部算完后重建 CPD
pydefect_vasp make_composition_energies -d *_*/
pydefect standard_and_relative_energies
pydefect cpd_and_vertices -t <target> -e C,Si,O
```

详细操作和坑点参见 [CPD 扩展参考](references/cpd-extension.md)。

##### `defect_energy_infos` 无 `correction.json` 也能跑

`defect_energy_infos` 会给出 WARNING "correction.json does not exist" 但照常计算形成能。\
校正项会在 `defect_energy_info.yaml` 中留空（`energy_corrections: {}`），最终形成能用 `--no_corrections` 绘图。

##### 同步 `output/` 到根目录

⚠️ crisp 把 VASP 结果 fetch 到 `output/` 子目录，但 pydefect 命令从根目录读文件。\
**不先同步会报 `calc_results.json not found` 之类错误。**

```bash
for d in defect/*/; do
    if [ -d "${d}output" ]; then
        for f in vasprun.xml OUTCAR CONTCAR PROCAR EIGENVAL; do
            [ -f "${d}output/$f" ] && [ ! -f "$d/$f" ] && cp "${d}output/$f" "$d/"
        done
    fi
done
```

注意：`cp` 不会覆盖已有文件。如果重新提交后想覆盖老结果，加 `-f` 参数。

##### 生成 `calc_results.json`

优先用 `pydefect_vasp calc_results`（但该命令可能不可用——它只读已有的 calc_results.json，不从 vasprun 解析）。\
如果不可用，用 Python API：

```python
from pymatgen.io.vasp import Vasprun, Outcar
from pydefect.cli.vasp.make_calc_results import make_calc_results_from_vasp

for d in Path("defect/").glob("*/"):
    v = d / "vasprun.xml"; o = d / "OUTCAR"
    if v.exists() and o.exists():
        cr = make_calc_results_from_vasp(Vasprun(str(v)), Outcar(str(o)))
        cr.to_json_file(str(d / "calc_results.json"))
```

已验证可以在 pydefect 0.9.12 环境中直接 `python` 执行（需 pymatgen）。

##### 全部后处理命令（与阶段 5 相同）

```bash
pydefect_vasp calc_results -d defect/*/                    # 解析结果
pydefect efnv -d defect/*/ -pcr perfect/calc_results.json -u ../unitcell.yaml
pydefect defect_structure_info -d defect/*/
pydefect defect_energy_infos -d defect/*/ \
  -pcr perfect/calc_results.json -u ../unitcell.yaml \
  -s cpd/standard_energies.yaml
pydefect defect_energy_summary -d defect/*/ \
  -u ../unitcell.yaml \
  -pbes perfect/perfect_band_edge_state.json \
  -t cpd/target_vertices.yaml
```

- 新老缺陷的结果在 `calc_results` / `efnv` / `defect_energy_infos` 等命令中**自动合并**
- `defect_energy_summary.json` 会包含所有缺陷的形成能

---

## 阶段 7: 复合缺陷生成 (pydefect-complex)

pydefect 原生只支持单点缺陷。对于复合缺陷（空位对、空位+掺杂、共掺杂等），使用独立库 `pydefect-complex`（位于 `~/pydefect-complex/`）。

### 7.1 安装

```bash
pip install -e ~/pydefect-complex
```

### 7.2 生成 + VASP 设置（三步验证，严禁跳过单条测试）

**⚠️ 核心纪律 — 只用 Maker API，严禁绕过**: 复合缺陷生成有且仅有一条正确路径：

```python
maker = ComplexDefectMaker.from_supercell_info(...)
maker.make_all_n_body(n)                        # 几何枚举
entries = maker.generate_entries(n_or_geometries=n)  # 组分分配 + 结构生成 + dedup
entries = ComplexDefectMaker.filter_entries(entries)  # C1 + max-dopant 过滤
maker.write(entries, '/absolute/path/defect', merge=True)
```

**⚠️ 注意事项（pydefect-complex >= 已硬化版本）**：

旧版 pydefect-complex（0.x）需要避免以下坑。新版（commit `8f9349c` 之后）已经把危险 API 私有化，留 deprecation shim：

- ⚠️ 旧版 `from pydefect_complex.enumerate import generate_all_entries` 不做 dedup → 新版**默认 dedup=True**；旧版裸调仍可走 shim
- ⚠️ 旧版 `from pydefect_complex.io import write_all` 接受任意 entries 互相覆盖 → 新版**改名为 `_write_all`**，shim 警告
- ⚠️ 旧版 `from pydefect_complex.io import write_entry` → 同上
- ⚠️ 旧版 `from pydefect_complex.enumerate import assign_compositions` → 新版**改名为 `_assign_compositions`**
- ⚠️ 旧版 `from pydefect_complex.enumerate import generate_structure` 公开 wrapper → 新版**删除**，只能走内部 `_generate_structure`（不推荐）或 Maker API
- ⚠️ 旧版 `maker.write(entries, 'defect')` 用相对路径 → 新版**自动 warn + resolve 到绝对路径**（不再静默踩坑）
- ⚠️ 旧版手写 `if e.point_group != 'C1'` 过滤 → 新版**统一封装为 `ComplexDefectMaker.filter_entries(entries)`**（必须 main 进程调用，不能放在 parallel worker 里——spglib 在 pickle 跨进程时非确定性，会让 parallel/serial 结果不一致）

**▶️ 正确流程（分阶，先小后大）**：

先交 N=2，确认无误后再 N=3，最后 N=4。**禁止一次生成全部阶再统一提交**——N 越大耗时爆炸（N=4 几何枚举 ~190s + entry 生成 ~100s），中间出错全部白费。

```python
# N=2 first (fast: ~1s)
maker.make_all_n_body(2)
entries = maker.generate_entries(n_or_geometries=2)
entries = ComplexDefectMaker.filter_entries(entries)  # 默认 C1 + max 2 dopants
maker.write(entries, '/absolute/path/to/defect', merge=True)

# After N=2 submitted and confirmed, continue N=3, then N=4 similarly
```

**Step 1: N=2 → N=3 → N=4 分阶生成**（每阶确认后再下一阶）

**电荷态**: 默认 `charges=[0]`（中性）。空位团簇建议 `charges=[-2,-1,0,1,2]`。传参方式：
```python
maker = ComplexDefectMaker(supercell_info, charges=[-2,-1,0,1,2])
# 或在 generate_entries 时覆盖
entries = maker.generate_entries(n=2, charges=[-1,0,1])
```

**物理过滤**（生成后筛选）:
```python
entries = maker.generate_entries(n=2)
# 去除非对称缺陷 (C1 = 平凡群)
entries = [e for e in entries if e.point_group != 'C1']
# 限制杂质原子数 ≤ 2
entries = [e for e in entries if sum(1 for a in e.complex_defect.in_elements if a) <= 2]
```

**N≥4 高阶枚举**: `maker.generate_entries(n=4)` 默认生成 2..4 所有阶数条目——这是必需的，Maker API 的 dedup 需要所有阶数上下文。生成后用 `e.complex_defect.n_defects == 4` 筛选目标阶数。**必须复用同一个 maker**（几何枚举有 Apriori 缓存）:

```python\nmaker = ComplexDefectMaker.from_supercell_info('supercell_info.json',\n    dopants=['O'], max_distance=4.0, charges=[0])\nmaker.enumerate_geometries(N_max=3)        # 缓存 N=2,3\nmaker.enumerate_geometries(N_max=4)        # Apriori 扩展 N=3→4（~192s）\n# ⚠️ 必须用 maker.generate_entries()，不是 generate_all_entries()!\nentries = maker.generate_entries(n_or_geometries=4)\n# 只取 N=4\nentries = [e for e in entries if e.complex_defect.n_defects == 4]\n# N=5 基本不可行——216 原子超胞 + 5 体枚举组合爆炸\n```\n\n⚠️ **严禁使用 `generate_all_entries()` / `write_all()`** — 绕过 Maker API 的 dedup，同名 entry 全写进同一个目录互相覆盖。

**Step 1: 确认命名 + 成分（不写入）**

```bash
cd defect/
python -c "
from pydefect_complex import ComplexDefectMaker
m = ComplexDefectMaker.from_supercell_info('supercell_info.json',
    dopants=['B','Si','O'], max_distance=4.0, charges=[0])
m.make_all_pairs()  # enumerate geometries
entries = m.generate_entries(n_or_geometries=2)
from collections import Counter
for name, cnt in sorted(Counter(e.complex_defect.name for e in entries).items()):
    print(f'{name}: {cnt}')
# 确认混合成分的实际目录名（因为按 out_atom 排序，Si_C1+B_C1 → B_C1+Si_C1）
"
```

**Step 2: 单目录 vise 验证**

⚠️ **VASP 参数必须与已有单缺陷完全一致**（ENCUT、SIGMA、NSW、KPOINTS、LORBIT 等），否则形成能无法直接比较。从已有单缺陷的 `vise_log.yaml` 获取原始生成参数，复制其 `KPOINTS`（手动调整过的）：

```bash
# 读取已有单缺陷的 vise 参数
cat Va_C1_0/vise_log.yaml
# 用相同参数生成 INCAR/POTCAR
vise vasp_set -x pbesol -t defect -k 0.1 \
  --options set_hubbard_u True \
  -uis NSW 50 SIGMA 0.02 LORBIT 11 \
  -d <entry>_0/
# 复制已有单缺陷的 KPOINTS（可能被手动改成 Γ-only）
cp Va_C1_0/KPOINTS <entry>_0/
# 验证
diff <(grep -E "^(ENCUT|SIGMA|NSW|LORBIT|ISPIN)" <entry>_0/INCAR) \
     <(grep -E "^(ENCUT|SIGMA|NSW|LORBIT|ISPIN)" Va_C1_0/INCAR)
grep ENCUT <entry>_0/INCAR
grep TITEL <entry>_0/POTCAR       # 确认所有元素
cat <entry>_0/KPOINTS              # 确认 k-point 密度
```

**Step 3: 确认无误后全量生成 + 批量**

```bash
python -c "
from pydefect_complex import ComplexDefectMaker
m = ComplexDefectMaker.from_supercell_info('supercell_info.json',
    dopants=['B','Si','O'], max_distance=4.0, charges=[0])
m.make_all_pairs()
entries = m.generate_entries(n_or_geometries=2)
# optional: apply physical filters here
m.write(entries, 'defect', merge=True)
print(f'Written {len(entries)} entries')
"

# 用正确的实际目录名做 glob（先 ls -d *+*001_0/ 确认命名）
vise vasp_set -t defect -x pbesol -d 2B_C1*/ 2O_C1*/ ... -uis ENCUT <值>

- **严禁手工复制 INCAR、手工 cat POTCAR、手工创建 symlink** — `vise vasp_set` 自动检测 POSCAR 元素 → 正确拼接 POTCAR
- `merge=True` 已自动合并 defect_in.yaml → 后处理命令（efnv、defect_energy_summary 等）可直接消费

输出目录结构：`{d1}+{d2}.{site}_{charge}/POSCAR`，含 `defect_entry.json` 和 `complex_defect_in.yaml`。可直接被 pydefect 后处理管线消费。

> ⚠️ **pydefect 命名约定**: `SimpleDefect(None, "C1", charges)` → name="Va_C1"（不是"v_C_1"），out_atom="C1"（元素+数字，无下划线）。复合缺陷名继承此格式，如 "Va_C1+Va_C2"、"Va_C1+N_C1"。pydefect-complex 内部通过 `d.name` 获取名称，确保与 pydefect 一致。

### 7.3 算法 (PLAN-C，图基 Apriori 枚举)

几何优先：先枚举 N 节点晶格构型（纯几何，与缺陷化学无关），再分配组分、生成结构。

| 模块 | 职责 |
|------|------|
| `HostGraph` | 晶体位点注册表 (wyckoff + 元素标签) |
| `ComplexDefectGraph` | 纯几何图 (N 节点 + min-image 边) |
| `ComplexDefectEnumerator` | Apriori 增量枚举：N=2 锚点+邻居对 → N=3 扩展 → Kabsch 在线去重 |
| `assign_compositions` | 几何图 → 缺陷组分匹配 (wyckoff multiset 匹配) |
| `deduplicate` | 跨组分几何去重 → per-composition index |

**验证记录**: diamond 2Va_C1.001 (q=0), 214 atoms, 64 cores, 4 min VASP, E_form = 11.81 eV。全流程端到端通过。diamond N=2 O 掺杂（15 entries: 2Va×5 + 2O×5 + OVa×5），Maker API 分阶流程验证通过。

详细用法和公开缺陷数据集参考见 [复合缺陷参考](references/complex-defects.md)。

### 7.8 再生复合缺陷的正确流程（不要文件替换！）

当需要重新生成复合缺陷时（如 pydefect-complex 版本升级、命名方向改变、旧版有 bug），**不要**在旧 `defect/` 目录里 rm 旧目录 + cp 新目录。正确做法：

1. 在项目下创建新目录（如 `defect_new/`），只放新版复合缺陷
2. 旧 `defect/` 保留不动——里面有已完成单缺陷结果、后处理文件
3. 从 `defect_new/` 直接提交 crisp

```bash
# ✅ 正确：独立目录，不碰旧的
project/
├── defect/              ← 保留不动（单缺陷 + 旧版复合缺陷）
├── defect_new/           ← 新版复合缺陷，从这里提交
```

**为什么不做文件替换**：旧 `defect/` 里有耗时算出的单缺陷结果（35/36 done）、后处理文件（energy_A/B.pdf、defect_energy_summary.json 等）。文件替换操作容易误删这些不可恢复的结果，且新版可以独立提交，后处理时再决定用哪个目录。

### 7.9 crisp 提交复合缺陷的 local_dir 坑点

**⚠️ `local_dir` 必须指向具体缺陷子目录，不能是父级 `defect/` 目录。**

```bash
# ❌ 错误：local_dir 指向 defect/ 根目录 → VASP 找不到 POSCAR → EXIT_CODE:1
# （批量提交时容易犯——脚本 cd 进了子目录但 crisp 记录的是 launch 时的 cwd）

# ✅ 正确：每个目录单独 submit，local_dir 指向具体子目录
PYTHONPATH=~/crisp/scripts python -m crisp.cli submit \
  /path/to/defect_new/2Va_C1.001_0
```

旧版 675 条全部因为这个原因秒失败（`local_dir: .../diamond/defect`，VASP 读取不到子目录里的 POSCAR）。

## 相关命令速查

| 问题 | 原因与解决 |

> **Crisp 批量提交**: 参见 [references/crisp-batch-submit.md](references/crisp-batch-submit.md) — Python 脚本调用 `get_job_db().register_job()` 批量注册，避免 curl/HTTP MCP 方式。
|:--|:--|
| `NotPrimitiveError` | 原胞晶格常数与 `supercell_info.json` 不匹配。生成 AECCAR 和 supercell 须用同一结构 |
| `calc_results` 失败 | `output/` 文件未同步到根目录 |
| `pydefect_util ai` 报 `NotPrimitiveError` | AECCAR 来自不同原胞，重做：统一用优化后结构生成 supercell + AECCAR |
| `pop` 不生效 | 间隙位非通过 `ai` 加入（格式不标准），用 `ai` 重新加入后再 pop |
| ENCUT 不一致 | `vise vs -t defect` 默认用 POTCAR ENMAX，需 `-uis ENCUT <bulk值>` |
| 提交后全部秒失败 (EXIT_CODE:1) | crisp 的 `local_dir` 指向了 `defect/` 根目录而非具体子目录（如 `defect/2Va_C1.001_0/`），VASP 找不到 POSCAR。每个缺陷目录必须单独 submit，local_dir 指向具体子目录。 |
| `bes` 找不到命令 | `bes` 是 `pydefect` 的子命令，不是 `pydefect_vasp` |
| 复合缺陷目录名被排序 | `ComplexDefect.name` 按 `out_atom` 倒序排列。`Si_C1+B_C1` 实际目录名是 `B_C1+Si_C1`。**必须先 `ls -d *+*001_0/` 确认实际命名再做 glob** |
| 批量前未单条验证 | 生成 50+ 条目后不应直接对全体跑 `vise vasp_set`。正确流程：生成 1 个条目 → 检查命名/目录结构 → `vise vasp_set` 单目录验证 → 确认 INCAR/POTCAR/KPOINTS 正确 → 再批量处理剩余。跳过验证直接批量会导致 glob 匹配失败 + 大量无效操作 |
| 多阶复合缺陷未分阶筛选 | 生成 N=2,3,4,5 时不应一次批量全部生成。正确流程：每阶单独生成 → 筛选（去 C1、≤2 杂质等）→ **用户确认该阶结果后再继续下一阶**。跳过确认直接批量会浪费大量计算资源在不满足物理条件的构型上。 |
| VASP 参数与已有单缺陷不一致 | 复合缺陷的 `vise vasp_set` 默认参数（SIGMA、KPOINTS、NSW）可能与已有单缺陷不同 → 形成能不能直接比较。必须从已有单缺陷的 `vise_log.yaml` 提取原始参数并复制其 KPOINTS。详见阶段 7.2 Step 2。 |
| `defect_entry.json` 导致 efnv 报 `'dict' object has no attribute 'charge'` | `defect_entry.json` 必须是 monty 序列化的 `DefectEntry` 对象（含 `@module`/`@class` 字段），不能是 plain dict。pydefect-complex v0.1.0 之前有此 bug。修复脚本见 [defect_entry.json 修复参考](references/defect-entry-json-fix.md)。 |
| N≥4 枚举爆慢 | `generate_entries(n=4)` 默认生成 2..4 所有阶数条目。用 `e.complex_defect.n_defects == 4` 筛选目标阶数；几何枚举有 Apriori 缓存可逐步扩展。**必须复用同一个 maker 实例**，新建 maker 会丢失缓存。⚠️ 严禁用 `generate_all_entries(orders={4})` — 绕过 Maker API dedup。 |
| 复合缺陷生成极慢且无输出 | (1) **必须用 Maker API** (`make_all_n_body → generate_entries → write`)，严禁用底层 enumerate.py/io.py 函数；(2) 后台 Python 无输出是管道缓冲问题，`-u` flag 不能完全解决——用 `ps --ppid <PID>` 检查子进程 CPU 确认在运行，别因为没输出就杀进程重跑；(3) N≥4 几何枚举需 ~190s，勿杀 |
| 复合缺陷 VIS 参数与单缺陷不一致 | `vise vasp_set` 对复合缺陷可能生成不同默认参数（SIGMA=0.1 vs 0.02, KPOINTS=2×2×2 vs Γ-only）。**必须从已有单缺陷的 `vise_log.yaml` 读取原始参数 + 复制其 `KPOINTS`**，用 `diff` 验证 INCAR 关键参数一致。 |

## 命令速查

```bash
# Structure
vise vasp_set -x pbesol -t structure_opt
vise vasp_set -x pbesol -t band
vise vasp_set -x pbesol -t dos -uis LVTOT True LAECHG True KPAR 1
vise vasp_set -x pbesol -t dielectric_dfpt
vise vasp_set -x pbesol -t defect

# pydefect_vasp
pydefect_vasp unitcell -vb -ob -odc -odi -n <name>
pydefect_vasp make_poscars -e <elements>
pydefect_vasp make_composition_energies -d <dirs>
pydefect_vasp local_extrema -v AECCAR0 AECCAR2
pydefect_vasp defect_entries
pydefect_vasp calc_results -d <dirs>
pydefect_vasp perfect_band_edge_state -d perfect
pydefect_vasp band_edge_orbital_infos -d <dirs> -pbes <state>

# pydefect
pydefect supercell -p <prim> --max_atoms <N>
pydefect defect_set [-k <regex>] [-d <dopant>]
pydefect pop [-i <idx> | --pop_all] -s <json>
pydefect plot_cpd
pydefect efnv -d <dirs> -pcr <json> -u <yaml>
pydefect defect_structure_info -d <dirs>
pydefect band_edge_states -d <dirs> -pbes <state>
pydefect defect_energy_infos -d <dirs> -pcr <json> -u <yaml> -s <yaml>
pydefect defect_energy_summary -d <dirs> -u <yaml> -pbes <state> -t <yaml>
pydefect plot_defect_formation_energy -d <json> -l <vertex>

# pydefect_util (间隙位)
pydefect_util add_interstitials_from_local_extrema \
  --local_extrema <json> -i <indices>
