# 数据存储

单一数据库 `~/.vasp_sop/jobs.db`，两张表：

## job_history — VASP 计算最终状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `dir_path` | TEXT | 计算目录完整路径 |
| `status` | TEXT | `converged` / `failed` |
| `reason` | TEXT | 失败原因: `unconverged` / `vasp_crash` / `orphaned` / `stalled` |
| `timestamp` | REAL | 记录时间 |
| `attempt` | INTEGER | CONTCAR 重启次数（0-5） |
| `task_name` | TEXT | crisp 任务 ID |

## tracked — 待检查列表

| 字段 | 类型 | 说明 |
|---|---|---|
| `dir_path` | TEXT | 已提交到 crisp 但尚未出结果的目录 |
| `submitted_at` | REAL | 提交时间 |

`submissions.db` 已删除。所有状态统一在 `jobs.db` 中。
