# 阶段机 (`System.phase()`)

> 2026-08-16 重构（ADR 0026）：阶段是**分析门**，不再是提交门。提交每轮无条件进行（输入就绪即提交）；阶段只回答「下游分析被什么挡住」。

## 阶段定义

```
RUNNING → CPD_READY → ANALYZE_READY → COMPLETE
```

| 阶段 | 含义 | 判定条件（全部磁盘派生） |
|---|---|---|
| `NO_TARGET` | 没有宿主相，无法运行 | `System.target_dir` 返回 None |
| `RUNNING` | 有计算未收敛（提交保持活跃） | target 未收敛，或有竞争相未收敛，或（凸包未算时）有 `.failed` 标记；或 unitcell/perfect/defect 腿未收敛 |
| `CPD_READY` | 全部 cpd 相收敛，凸包待算 | cpd 全收敛 + `target_vertices.yaml` 缺失/为空，或 `standard_energies`/`composition_energies`/`chem_pot_diag.json` 缺 |
| `ANALYZE_READY` | 凸包 + 全腿收敛，缺陷分析待跑 | CPD 产物全 + 腿全收敛 + `unitcell.yaml` 或缺陷 analyze 产物（`calc_results`/`correction`/`defect_structure_info`/`perfect_band_edge_state`/`defect_energy_summary`）缺 |
| `COMPLETE` | 分析完成 | 全部中间文件齐全 |

> 阶段纯磁盘派生（ADR 0011）；`state.json` 已不读不写（ADR 0001 遗留）。

## 提交与分析的解耦（ADR 0026）

`advance_one_system` 每轮：

1. **提交**（无条件）：`wave1_optimize`（宿主 target，幂等）+ `wave2_submit`（竞争相 + stage2 补充 + unitcell 单点腿 + perfect + 缺陷链播种）——任何输入就绪的目录即提交。
2. **分析**（按门）：`p == CPD_READY` → `wave3_cpd`（凸包 + 从 target 派生 structure_opt）；`p == ANALYZE_READY` → `wave3_analyze`（unitcell.yaml + pydefect 形成能）。

单点腿（band/dos/dielectric）的物理依赖：需要 `unitcell/structure_opt/CONTCAR`（收敛宿主，由 CPD 门派生）——输入依赖，不是相位门。

## COMPLETE 的具体判断

```python
if cpd 未全收敛 (target + 竞争相, ADR 0013 排除相除外):  return RUNNING
if 凸包未算 (.failed 标记阻塞仅当 target_vertices 未生成): return CPD_READY
if CPD 产物缺 (target_vertices/standard_energies/composition_energies/chem_pot_diag):
                                                          return CPD_READY
if 化学环境 (ADR 0005):                                   return COMPLETE
if unitcell 单点腿或 perfect 或 defect 链有未收敛 (反位排除): return RUNNING
if unitcell.yaml 或缺 analyze 产物:                       return ANALYZE_READY
return COMPLETE
```
