# 阶段机 (`_phase()`)

## 阶段定义

```
STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE
```

| 阶段 | 含义 | 判定条件 |
|---|---|---|
| `NO_TARGET` | 没有 MPID，无法运行 | `_target_dir()` 返回 None |
| `STRUCTURE_OPT` | 目标相 VASP 没算完 | JobStore 说 target 没 `converged` |
| `COMPETING` | 竞争相还有没提交的 | `_competing_dirs()` 返回非空 |
| `CHEM_POT_DIAGRAM` | 竞争相算完了，CPD 待生成 | 无 competing dirs，`target_vertices.yaml` 不存在 |
| `UNITCELL_DEFECT` | CPD 完成，UC/缺陷阶段 | `target_vertices.yaml` 存在 |
| `COMPLETE` | 全线完成 | 全部中间文件齐全 |

## COMPLETE 的具体判断

```python
if target_vertices.yaml 存在:
    if UC 输入未生成:              return UNITCELL_DEFECT
    if UC 有任务没做完:            return UNITCELL_DEFECT
    if unitcell/unitcell.yaml 不存在: return UNITCELL_DEFECT
    if CPD 中间文件缺:             return UNITCELL_DEFECT
    if defect 目录不存在:          return UNITCELL_DEFECT
    if defect_energy_summary.json 不存在: return UNITCELL_DEFECT
    for 每个缺陷子目录:
        if failed 状态: continue         # 跳过已放弃的
        if 缺 calc_results.json:         return UNITCELL_DEFECT
        if 缺 correction.json:          return UNITCELL_DEFECT
        if 缺 defect_structure_info.json: return UNITCELL_DEFECT
    if 缺 perfect_band_edge_state.json: return UNITCELL_DEFECT
    return COMPLETE
```
