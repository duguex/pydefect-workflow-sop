# 向已有 CPD 添加新元素（实测于 SiC + O）

> 源自 3C-SiC 项目扩展 O 杂质时的 CPD 操作记录  
> 原始项目: `~/materials-research/pydefect-test/3C-SiC/`

## 场景

已有二元 Si-C 系统的 CPD（Si、C 竞争相已算完），需要添加 O 竞争相以便计算 O 杂质的形成能。

## 步骤

### 1. 备份

```bash
cp -r cpd cpd.bak
```

### 2. 清理无残留的多余相目录

`make_poscars` 首次运行时下载了所有 SiC 多晶型，但大部分没有实际计算（只有 POSCAR + prior_info.yaml）。如果不清理，第二次 `make_poscars -e C Si O` 会报 `FileExistsError`。

**清理规则（仅保留有 VASP 结果的目录）：**

```bash
cd cpd/
for d in */; do
  if [ ! -f "$d/CONTCAR" ] && [ ! -f "$d/OUTCAR" ] && [ ! -f "$d/.completed" ]; then
    rm -rf "$d"   # 只有 POSCAR + prior_info.yaml，无 VASP 结果
  fi
done
```

### 3. 下载新竞争相

```bash
pydefect_vasp make_poscars -e O --e_above_hull 0.0005
# 或全量: pydefect_vasp make_poscars -e C Si O --e_above_hull 0.0005
# （如果已有目录已被清理，全量也不会报错）
```

- O₂ 分子由 pydefect **自动生成**为 `mol_O2/`，包含正确的 spin 设置（ISPIN=2, NUPDOWN=2, ISIF=2）
- 最稳定的 SiO₂ 多晶型需手动下载（MP id: mp-546794）

### 4. 下载特定相（手动，当 make_poscars 无法覆盖时）

```python
# 从 Materials Project 下载特定结构
from pymatgen.ext.matproj import MPRester
with MPRester() as mpr:
    structure = mpr.get_structure_by_material_id('mp-546794')  # α-石英
    structure.to(filename='SiO2_mp-546794/POSCAR', fmt='poscar')
```

另需创建 `prior_info.yaml`：
```yaml
charge: 0
```

### 5. VASP 输入设置

**固体相**（SiO₂）：
```bash
cd SiO2_mp-546794/
vise vasp_set -x pbesol -uis KPAR 4 LWAVE False LCHARG False
# ENCUT 自动计算（O=400 → 520），ISIF=3，KS密度5.0
```

**气体分子**（O₂）：
```bash
cd mol_O2/
# 注意：prior_info.yaml 已包含 ISPIN=2, NUPDOWN=2
# pydefect 自动识别的 prior_info.yaml 已有 is_cluster=True
vise vasp_set -x pbesol -uis KPAR 1 LWAVE False LCHARG False
# 生成结果: ISIF=2, Gamma-only KS, ENCUT=400
```

### 6. 提交脚本

复制已算完的竞争相提交脚本：
```bash
cd cpd/
for d in mol_O2 SiO2_mp-546794; do
  cp Si_mp-149/submit.slurm "$d/"
done
```

检查 CPD 各相之间参数一致性：
- **ENCUT**: 必须统一（SiC=520, C=520, Si=319, SiO₂=520, O₂=400）。 注意 Si 只有 245 ENMAX → 1.3×=319，比 SiC/C/O 的 400→520 低。这是正确的，因为 Si 的 POTCAR 本质不需要更高 ENCUT。
- **ISIF**: 固体=3，气体/分子=2
- **ISMEAR**: 半导体/绝缘体用 -5（Si: -5, C: -5, SiO₂: 0 由 vise 自动选择）
- **KPOINTS**: 固体用 Gamma-centered 均匀网格，分子用 Gamma-only

### 7. 算完后重建 CPD

> ⚠️ 这些步骤需在 VASP 计算完成之后执行

```bash
cd cpd/

# 收集所有竞争相能量
pydefect_vasp make_composition_energies -d *_*/

# 生成标准参考能量（eV/atom）
pydefect standard_and_relative_energies

# 计算 CPD 顶点（三元需指定所有元素）
pydefect cpd_and_vertices -t SiC -e C Si O

# 绘图
pydefect plot_cpd
```

### 8. 注意事项

| 坑点 | 说明 |
|:--|:--|
| `make_poscars` 不幂等 | 第二次对已有元素运行会 `FileExistsError`，需先清理空目录 |
| MP vs VASP 能量对齐 | MP 使用 PBE，项目可能用 PBEsol → 不能用 `composition_energies_from_mp` 直接混合。要么全部用 VASP 算，要么用原子能量对齐 |
| O₂ 是特殊情况 | 自旋极化（ISPIN=2）、三线态（NUPDOWN=2）、固定盒子（ISIF=2） |
| 空位/替位不需要 O POTCAR | 只有含 O 的缺陷目录需要 O POTCAR，原生缺陷不受影响 |
| CPD 更新影响所有缺陷 | 新 `standard_energies.yaml` 中 O 的参考能会影响所有 O 缺陷的形成能。非 O 缺陷的形成能只依赖于 μ_Si 和 μ_C，不变 |
