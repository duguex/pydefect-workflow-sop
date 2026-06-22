# Issue 0002: `pydefect pc` 对 4 元体系抛 ValueError，batch run 永远卡在 CPD_POST

## 时间
2026-06-23 00:14 (CST)

## 现象

`Sr2MgSi2O7`（Sr/Mg/Si/O 四元）的 CPD 后处理失败：

```
00:14:20 INFO Sr2MgSi2O7: CPD post-processing ...
00:14:26 INFO Corrected O2 energy: -10.2611 -> -8.8871
00:14:33 ERROR Sr2MgSi2O7 CPD failed: Command failed in .../Sr2MgSi2O7/cpd (exit 1):
  $ pydefect pc
  stderr: Traceback (most recent call last):
  ...
  File ".../pydefect/cli/main_functions.py", line 105, in plot_chem_pot_diag
    raise ValueError(f"Only 2 or 3 dimensions are supported. Now {cpd.dim} dimensions.")
ValueError: Only 2 or 3 dimensions are supported. Now 4 dimensions.
  ✗ Sr2MgSi2O7         CPD post-processing FAILED
```

## 根因

- `pydefect` 的 `pc` (plot chem-pot diagram) 只支持 2D / 3D
- 4 元体系在 chem-pot 空间是 4D halfspace intersection，超出 matplotlib plotters 能力
- 这是 pydefect 库限制，**不是 vasp-sop 代码 bug**，但 vasp-sop 应该跳过这种情形而不是 fail

## 涉及体系（4 元以上）

| 体系 | 元素数 | 影响 |
|------|:------:|------|
| Sr2MgGe2O7 | 4 (Sr/Mg/Ge/O) | 会复现 |
| Sr2MgSi2O7 | 4 (Sr/Mg/Si/O) | 已复现 |
| Ba2MgGe2O7 | 4 | 会复现 |
| Ba2MgSi2O7 | 4 | 会复现 |
| Ca2Ge7O16 | 3 (Ca/Ge/O) | OK（3D 边界） |
| BaGe4O9 | 3 | OK |
| SrGe4O9 | 3 | OK |
| Sn(SeO3)2 | 3 (Sn/Se/O) | OK |
| CaMg2(SO4)3 | 4 (Ca/Mg/S/O) | 会复现 |
| Mg3TeO6 | 3 (Mg/Te/O) | OK |

## 建议修复

在 `vasp_sop/defect/cpd.py` 的 `compute_chemical_potentials` 中 line 231 之前加维度判断：

```python
if len(target_composition.as_dict()) > 1 and not (cpd_root / "cpd.pdf").is_file():
    n_elements = len(target_composition.elements)
    if n_elements > 3:
        logger.warning(
            "%s: %d-element system, skipping pydefect pc "
            "(pydefect only supports 2D/3D chem-pot diagrams)",
            cpd_root.name, n_elements,
        )
        # Optionally: emit a synthetic cpd.pdf placeholder
    else:
        run_local("pydefect pc", cwd=cpd_root)
```

并修复 batch run 主循环：若 `pydefect pc` 失败但其余产物（`target_vertices.yaml` / `chem_pot_diag.json`）齐全，应**视为 CPD_POST 完成**，推进到 UC_DF，而不是循环 retry。

## 实际状态

- Sr2MgSi2O7 在日志里只失败 1 次就停了 —— batch run 自己好像没死循环；原因待查
- 其他 4 元体系尚未触发本错误
