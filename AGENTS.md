# Agent 操作规范

## 作业管理

涉及 HPC 集群作业操作（提交、取消、查状态）前，**必须先加载 skill**：

```
读取 skill://crisp
```

crisp CLI 用法见该 skill。

**禁止：** 直接使用 `scancel`、`sbatch`、`squeue` 提交/取消作业，或手动启停 `crisp-agent`。

`squeue` 仅可用于快速查看 Slurm 队列状态，不得用于 cancel 或 submit。
