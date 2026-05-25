# 间隙位分析实测验证（3C-SiC, 2026-05-17）

## 官方正确流程

```bash
# 1. 从 AECCAR 找电荷密度谷（间隙位）
pydefect_vasp local_extrema \
  -v AECCAR0 AECCAR2 \
  -i "all_electron_charge"
# 默认找局部极小值（电荷密度的谷 = 原子间空隙）
# 输出: volumetric_data_local_extrema.json

# 2. 从 extrema JSON 添加间隙位到 supercell_info
pydefect_util add_interstitials_from_local_extrema \
  --local_extrema volumetric_data_local_extrema.json \
  -i 1 2
# -i: 从 extrema 文件中选哪些索引加入（从1开始）

# 3. 移除间隙位
pydefect pop -i 1 -s supercell_info.json       # 按索引移除
pydefect pop --pop_all -s supercell_info.json  # 全部移除
```

## 关键条件

### AECCAR / LOCPOT
- DOS 计算必须加 `LVTOT True LAECHG True KPAR 1`（官方必填参数）
- `LAECHG=True` → AECCAR0/AECCAR2（`local_extrema` 用）
- `LVTOT=True` → LOCPOT（教程 §6 要求 "volumetric data such as AECCAR and LOCPOT"）

### 原胞必须一致！
以下三者的晶格常数必须完全相同：
1. `supercell_info.json` 中的 `unitcell_structure`
2. AECCAR 文件对应的结构（DOS 计算用的 POSCAR）
3. `pydefect supercell -p` 传入的原胞

若结构优化后晶格有变化，需从优化后的 CONTCAR **重新生成** supercell_info，然后用新 AECCAR（也来自优化结构）。

## 3C-SiC 实测结果

- AECCAR0 + AECCAR2 → 找到 2 个间隙位
- Site 1: (0.375, 0.375, 0.375) in supercell coords — T_Si（四面体 Si 侧）
- Site 2: (0.250, 0.250, 0.250) in supercell coords — T_C（四面体 C 侧）
- `pydefect pop -i 1` → 正确删除 #1，#2 重编号为 #1
- `pydefect pop --pop_all` → 正确清空

## 常见错误

| 错误做法 | 结果 |
|:--|:--|
| 用 `pydefect append_interstitial -p -c` 手动添加 | 原胞不匹配 → `NotPrimitiveError` |
| AECCAR 来自结构 A，supercell_info 来自结构 B | `NotPrimitiveError` |
| 手动写 JSON 填充 interstitials | `pop` 读不了 |
