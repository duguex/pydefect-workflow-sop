# 收敛判定 (`check_converged`)

实现：`vasp_sop/vasp/io.py::check_converged`。

## 规则

```text
1. 无 OUTCAR / 无 "General timing and accounting" → False（未正常结束）

2. 运行参数优先读 **OUTCAR**（本次 job），INCAR 仅作缺失时的回退。
   禁止用“已为续跑改过的 INCAR NSW”判断旧 OUTCAR。

3. 单点 / DFPT / MD：NSW≤1 或 IBRION∉{1,2,3}
   → timing 即可 True

4. 离子弛豫：IBRION∈{1,2,3} 且 NSW>1
   a. EDIFFG < 0（力判据，缺陷默认）：
        max|F|（最后一块 TOTAL-FORCE）≤ |EDIFFG|  → True
        无 FORCE 块 / 力超阈                     → False
   b. EDIFFG ≥ 0 或无法用力：
        n_ionic < NSW_run（OUTCAR 中的 NSW）     → True（提前退出启发式）
```

## 设计动机

| 问题 | 处理 |
|------|------|
| CONTCAR 续跑抬高 INCAR NSW → 假 converged | NSW / 力一律以 **OUTCAR** 为准 |
| 假阳性（力未到却标完成）污染 JobStore / COMPLETE | **力硬门**（EDIFFG&lt;0） |
| 假阴性（跑满 NSW 但力已到） | 力硬门下 `n==NSW` 且力达标 → True |

与 pymatgen `Vasprun.converged_ionic` 的 NSW 提前退出启发式兼容，但在力判据任务上更严、更不易被续跑参数污染。

## dielectric / band / dos

- `check_task_complete("dielectric")`：只要求 OUTCAR + timing（DFPT 无离子力判据）。
- `band` / `dos`：`check_converged`（通常 NSW≤1）+ `vasprun.xml`。

## 测试

`tests/test_defects.py::TestVaspJobDone`：含 NSW bump 假阳性、满 NSW 力达标、提前退出但力失败等回归。
