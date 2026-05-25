# CLAUDE.md — PyDefect Workflow SOP

本项目是点缺陷 VASP 计算的标准化操作流程，基于 pydefect + vise 工具链。以下是执行本工作流时必须遵守的规则和操作指南。

## 核心规则（必须遵守）

1. **VASP 输入文件必须由 `vise vasp_set` 生成** — 严禁手工写 INCAR/POTCAR/KPOINTS。手工构造会导致：(a) POTCAR 顺序与 POSCAR 不匹配，(b) ENCUT 不一致导致形成能系统偏移，(c) 缺少 pydefect 后处理所需的关键标签（LVTOT、LAECHG 等）。
2. **复合缺陷只走 Maker API** — `ComplexDefectMaker` → `make_all_n_body(N)` → `generate_entries()` → `write()`。`generate_all_entries()` 和 `write_all()` 不做去重，同名 entry 会互相覆盖。
3. **先单条，后批量** — 生成缺陷条目后，先对 1 条执行 `vise vasp_set` 并用 `diff` 与已有单缺陷 INCAR 对比，确认一致后再批量。
4. **crisp 提交 `local_dir` 必须指向具体子目录** — 指向父级目录会导致 VASP 秒退（找不到 POSCAR）。
5. **后处理前先同步 `output/`** — crisp fetch 结果到 `output/` 子目录，运行 pydefect 命令前先 `cp output/* .`。

## 环境

- pydefect 0.9.12, vise 0.9.1, pymatgen 2025.6.14
- VASP 通过 crisp 提交到 SLURM 集群
- 复合缺陷扩展: `~/pydefect-complex/`（`pip install -e .` 安装）
- Python 环境须包含 pymatgen、monty、spglib

## 项目目录结构

```
<project>/
├── unitcell/
│   ├── structure_opt/    # 结构优化 (POSCAR → CONTCAR)
│   ├── band/             # 能带计算
│   ├── dos/              # DOS (LAECHG=True → AECCAR0/2)
│   └── dielectric/       # 介电常数 (DFPT)
├── cpd/                  # 竞争相化学势图
│   └── <phase>/
└── defect/               # 缺陷计算
    ├── supercell_info.json
    ├── defect_in.yaml
    ├── perfect/           # 完美超胞参考
    ├── Va_X_0/           # 各缺陷目录
    └── results/           # 后处理输出
```

## 关键命令

### 结构优化
```bash
vise vasp_set -x pbesol
# ENCUT须 1.3× max(ENMAX of POTCARs)
# 例: Si=245, C=400 → ENCUT ≥ 520
```

### DOS（含间隙位所需 AECCAR）
```bash
vise vasp_set -x pbesol -t dos -pd ../structure_opt \
  -uis LVTOT True LAECHG True KPAR 1
```

### 缺陷输入生成
```bash
pydefect supercell -p <primitive> --max_atoms <N>
pydefect defect_set           # 所有缺陷
pydefect defect_set -d <elem> # 加掺杂
pydefect_vasp defect_entries
vise vasp_set -t defect -x pbesol -d defect/*/ \
  -uis ENCUT <值> KPAR 4 NSW 80 EDIFFG -0.02
```

### crisp 批量提交（Python API）
```python
from utils.db import get_job_db
db = get_job_db()
for d in Path("defect_new").glob("*_0/"):
    db.register_job(task_name=uuid.uuid4().hex[:8],
                    local_dir=str(d.resolve()),
                    status="submit")
```
**不要用 `crisp submit` CLI 批量提交** — CLI 的 `local_dir` 依赖 `cwd()`，批量时全部指向同一目录。

### 后处理管线
```bash
# 1. 同步 output/
for d in defect/*/; do
  if [ -d "${d}output" ]; then
    for f in vasprun.xml OUTCAR CONTCAR PROCAR EIGENVAL; do
      [ -f "${d}output/$f" ] && [ ! -f "$d/$f" ] && cp "${d}output/$f" "$d/"
    done
  fi
done

# 2. 解析 -> 修正 -> 形成能
pydefect_vasp calc_results -d defect/*/
pydefect efnv -d defect/*/ -pcr perfect/calc_results.json -u ../unitcell.yaml
pydefect defect_structure_info -d defect/*/
pydefect defect_energy_infos -d defect/*/ -pcr perfect/calc_results.json -u ../unitcell.yaml -s cpd/standard_energies.yaml
pydefect defect_energy_summary -d defect/*/ -u ../unitcell.yaml -pbes perfect/perfect_band_edge_state.json -t cpd/target_vertices.yaml
pydefect plot_defect_formation_energy -d defect_energy_summary.json -l A
```

## 复合缺陷（pydefect-complex）

### 正确流程（分阶生成，严禁一次性全部生成）
```python
from pydefect_complex import ComplexDefectMaker

maker = ComplexDefectMaker.from_supercell_info(
    'supercell_info.json',
    dopants=['O'],
    max_distance=4.0,
    charges=[0]
)

# N=2 先试（~1s）
maker.make_all_n_body(2)
entries = maker.generate_entries(n_or_geometries=2)
entries = [e for e in entries if e.point_group != 'C1']
entries = [e for e in entries if sum(1 for a in e.complex_defect.in_elements if a) <= 2]
maker.write(entries, '/absolute/path/defect', merge=True)

# 确认后再 N=3 → N=4
```

### 严禁事项
- ❌ `generate_all_entries()` / `write_all()` — 不做 dedup，同名条目互相覆盖
- ❌ 绕过 Maker API 直接调用 enumerate.py/io.py
- ❌ 一次生成 N=2,3,4 再统一提交
- ❌ `maker.write(entries, 'defect')` 用相对路径 — **必须用绝对路径**

## 常见错误与诊断

**crisp 提交后全部 EXIT_CODE:1**: 检查 job 记录的 local_dir 是否为具体子目录。
```bash
python -c "from utils.db import get_job_db; db=get_job_db(); print([(j.task_name, j.local_dir) for j in db.query(status='submitted')])"
```

**efnv 报 `'dict' object has no attribute 'charge'`**: defect_entry.json 是 plain dict 而非 monty 对象。运行修复脚本：
```bash
python references/defect-entry-json-fix.md 中的 Python 代码块
```
（该 reference 文档内含修复代码）

**后处理找不到 calc_results.json**: 忘记同步 output/，或 `pydefect_vasp calc_results` 命令不可用。用以下 Python 替代：
```python
from pymatgen.io.vasp import Vasprun, Outcar
from pydefect.cli.vasp.make_calc_results import make_calc_results_from_vasp
from pathlib import Path
for d in Path("defect/").glob("*/"):
    v, o = d/"vasprun.xml", d/"OUTCAR"
    if v.exists() and o.exists():
        cr = make_calc_results_from_vasp(Vasprun(str(v)), Outcar(str(o)))
        cr.to_json_file(str(d/"calc_results.json"))
```

**复合缺陷生成无输出不退出**: Python 缓冲导致。N≥4 几何枚举正常需要 ~190s。
用 `ps --ppid <PID>` 确认 CPU 是否在跑，勿因无输出就杀进程。

**增量掺杂后 CPD 报 FileExistsError**: `make_poscars` 不幂等。清理未计算目录：
```bash
for d in cpd/*/; do
  if [ ! -f "$d/CONTCAR" ] && [ ! -f "$d/OUTCAR" ]; then
    rm -rf "$d"
  fi
done
```

## 参考文档

- `SKILL.md` — 完整 SOP（最全参考）
- `references/3c-sic-test.md` — 3C-SiC 端到端测试验证
- `references/doped-comparison.md` — 与 doped 工具链对比
- `references/crisp-batch-submit.md` — crisp 批量提交细节
- `references/defect-entry-json-fix.md` — defect_entry.json 修复方法
- `references/complex-defects.md` — 复合缺陷生成详解
- `references/cpd-extension.md` — 增量掺杂 CPD 扩展
- `scripts/verify-installation.sh` — 环境验证
