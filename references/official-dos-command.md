# DOS 计算命令：官方文档 vs SOP 对比

> 验证日期: 2026-05-17
> 来源: https://kumagai-group.github.io/pydefect/tutorial.html §2

## 官方命令

```bash
vise vs -x pbesol -t dos -pd ../structure_opt -uis LVTOT True LAECHG True KPAR 1
```

### 各参数作用

| 参数 | 作用 | 说明 |
|:--|:--|:--|
| `LVTOT = True` | 写 LOCPOT（静电势体积数据） | 教程 §6："generate volumetric data such as AECCAR and LOCPOT" |
| `LAECHG = True` | 写 AECCAR0/AECCAR2（全电子电荷密度） | `local_extrema -v AECCAR0 AECCAR2` 依赖此文件 |
| `KPAR = 1` | 核数 = 1（默认值，显式写出） | 不影响功能，安全参数 |

## SOP v2.0 的错误

| 问题 | 详情 |
|:--|:--|
| 缺 `LVTOT True` | ❌ 官方明确要求 LOCPOT，漏了 |
| 多了 `LCHARG True` | 无害但多余（默认即为 True，且与 AECCAR 无关） |
| 注释说"LCHARG=False 必须改成 True" | ❌ 错误！AECCAR 由 `LAECHG` 控制，非 `LCHARG` |

## 修正

SOP v2.1.0 已改为与官方完全一致。
