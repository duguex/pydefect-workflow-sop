# 收敛判定 (`check_converged`)

和 pymatgen `Vasprun.converged_ionic` 逻辑一致，基于 NSW：

```python
def check_converged(path):
    if not OUTCAR 存在: return False
    if "General timing" not in tail: return False

    nsw = read INCAR → NSW
    ibrion = read INCAR → IBRION

    # 单点 / DFPT / NSW≤1 → 不需要弛豫检查
    if nsw <= 1 or ibrion not in (1, 2, 3):
        return True

    # 弛豫 (IBRION=1/2/3, NSW>1):
    n_ionic = OUTCAR 中 TOTAL-FORCE 块数
    return n_ionic >= 1 and n_ionic < nsw   # 提前退出 = 收敛
```

## dielectric 特殊处理

DFPT 介电计算（`IBRION=8`、`LEPSILON=True`）不做离子弛豫，`check_converged` 中的受力判断不适用。`check_task_complete("dielectric")` 直接跳过 `check_converged`，只检查 OUTCAR 存在 + VASP 正常结束。
