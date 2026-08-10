# vise / pydefect 命令清单（vasp-sop 实际使用）

> 来源：代码构造语句（`vasp_sop/vasp/io.py`、`vasp_sop/defect/pydefect_adapter.py`、`vasp_sop/defect/unitcell.py`、`vasp_sop/materials/mp.py`），参数代入真实默认值。
> 全部经 `vasp_sop/core/jobs.py::run_local` 执行（conda PATH 优先，超时 300-600s）。
> 共 26 条：vise 4 + pydefect 22。

## vise（4 条）

### 输入生成（io.py CLI 路径）

`-t` 按任务变化：`defect` / `band` / `dos` / `dielectric_dfpt` / `structure_opt`。
`-uis` 中的 `extra_uis` 仅 defect 传 `SIGMA 0.02 LORBIT 11`；`ENCUT 400` 仅 plan `encut` 非空时出现。
生成后经 `_apply_soc_tags` patch（dielectric: NSW=1/LREAL=.FALSE./去 SOC；其他 SOC 体系: LSORBIT=.TRUE./ISYM=-1）。

```bash
# 1. 输入生成（defect 示例）
vise vs -x pbesol -k 2.0 -t defect --options set_hubbard_u True -uis NSW 50 NELM 50 SIGMA 0.02 LORBIT 11
```

### unitcell 模板（pydefect_adapter.py VISE_TASKS）

```bash
# 2. band
vise vs -x pbesol -t band
# 3. dos
vise vs -x pbesol -t dos -k 2 -uis LVTOT True LAECHG True KPAR 1
# 4. dielectric
vise vs -x pbesol -t dielectric_dfpt -k 2
```

> defect 目录的输入生成走 vise Python API（`CategorizedInputOptions(charge=q)`，NELECT 由 vise 计算），无 CLI 命令。

## pydefect（22 条）

### CPD 阶段

```bash
# 5. 竞争相下载（元素按 plan 组合）
pydefect_vasp mp -e Al Fe O Sr --e_above_hull 0.0005
# 6. 组成能量 composition_energies.yaml
pydefect_vasp mce -d <cpd/phase_dirs...>
# 7. 标准/相对能量 standard_energies.yaml
pydefect sre
# 8. 化学势顶点 target_vertices.yaml（失败时进入能量调整循环）
pydefect cv -t "SrAl4O7"
# 9. 化学势图 chem_pot_diag.json（>3 元素体系跳过绘图，仅计算）
pydefect pc
```

### Unitcell 阶段

```bash
# 10. unitcell.yaml（带隙/价带顶）
pydefect_vasp u -vb <unitcell/band/vasprun.xml> -ob <unitcell/band/OUTCAR> -odc <unitcell/dielectric/OUTCAR> -odi <unitcell/dielectric/OUTCAR> -n SrAl4O7
# 11. AECCAR 局域极值
cd dos && pydefect_vasp le -v AECCAR0 AECCAR2 -i all_electron_charge
```

### Defect 构建

```bash
# 12. 超胞（fallback；默认走 doped Python API）
pydefect s -p <unitcell/CONTCAR> --max_atoms 600 --min_atoms 200
# 13. 缺陷列表 defect_in.yaml（有 dopant 时）
pydefect ds -d Fe
# 14. 缺陷结构
pydefect_vasp de
```

### Defect 后处理（<dirs...> = 缺陷目录批量，`-d` 名称经 shlex.quote）

```bash
# 15. calc_results.json（逐目录并行）
pydefect_vasp cr -d <defect_dir>
# 16. perfect calc_results
pydefect_vasp cr -d perfect
# 17. 有限尺寸校正 correction.json（逐目录并行）
pydefect efnv -d <defect_dir> -pcr <perfect/calc_results.json> -u <unitcell/unitcell.yaml>
# 18. perfect 带边态
pydefect_vasp pbes -d perfect
# 19. 带边占据（批量）
pydefect_vasp beoi -d <dirs...> -pbes <perfect/perfect_band_edge_state.json>
# 20. 带边态（批量）
pydefect bes -d <dirs...> -pbes <perfect/perfect_band_edge_state.json>
# 21. 缺陷结构信息 defect_structure_info.json（批量）
pydefect dsi -d <dirs...>
# 22. 缺陷体积分数（批量）
pydefect_util dvf -d <dirs...>
# 23. defect_energy_info（批量）
pydefect dei -d <dirs...> -pcr <perfect/calc_results.json> -u <unitcell/unitcell.yaml> -s <cpd/standard_energies.yaml>
# 24. correction_summary（批量）
pydefect cs -d <dirs...> -pcr <perfect/calc_results.json>
# 25. defect_energy_summary.json（批量）
pydefect des -d <dirs...> -u <unitcell/unitcell.yaml> -pbes <perfect/perfect_band_edge_state.json> -t <cpd/target_vertices.yaml>
# 26. 顶点能量图（每顶点）
pydefect pe -d defect_energy_summary.json -l <vertex>
```

## 未计入 26 条

```bash
# 绘图（非致命，失败仅 warning）
cd band && vise pb
cd dos && vise pd
cd dielectric && vise pdf
# 填隙位点（plan interstitials: false 未启用）
pydefect_util ai --local_extrema <dos> -i <sites>
```
