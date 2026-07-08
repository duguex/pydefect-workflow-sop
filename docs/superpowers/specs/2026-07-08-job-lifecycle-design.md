# Job Lifecycle Management — 作业生命周期统一管理

> Date: 2026-07-08
> Project: vasp-sop (`/home/duguex/vasp_sop/`)
> Status: Design

## 背景

当前作业状态分散在两个数据库：`submissions.db`（crisp 提交记录）和 `jobs.db`（VASP 状态）。两个库职责重叠但互不感知，导致 VASP 不收敛时系统卡死、CONTCAR 重启需要两处同步、无法追溯完整生命周期。

## 设计原则

**crisp 的作业生命周期就是 vasp-sop 的作业生命周期。JobStore 只记录 crisp 不关心的东西——VASP 收敛状态。**

## 状态模型

### crisp 负责的（Slurm 调度）

```
submit → submitted → running → ready_fetch → completed
                                  └──→ failed
                                  └──→ cancelled
```

vasp-sop 不重复这个状态机。轮询时查 `crisp jobs` 获取活跃作业列表。

### JobStore 负责的（VASP 收敛）

每完成一个 VASP 计算，检查 OUTCAR → 写入最终状态。`status` 只有两个值，一旦写入不再变更：

| 状态 | 含义 | reason 举例 |
|---|---|---|
| `converged` | OUTCAR 收敛 | — |
| `failed` | 放弃了 | `"unconverged"` / `"vasp_crash"` / `"orphaned"` / `"restart"` |

JobStore 表结构（`~/.vasp_sop/jobs.db`）：

```sql
CREATE TABLE IF NOT EXISTS job_history (
    dir_path    TEXT NOT NULL,
    status      TEXT NOT NULL,       -- converged | failed
    reason      TEXT,                -- unconverged / vasp_crash / orphaned / restart
    timestamp   REAL NOT NULL,
    attempt     INTEGER DEFAULT 0,   -- CONTCAR 重启次数
    task_name   TEXT                 -- crisp 任务 ID（记录用）
);

CREATE TABLE IF NOT EXISTS tracked (
    dir_path TEXT PRIMARY KEY,
    submitted_at REAL NOT NULL
);
```

### 为什么没有 waiting / running

crisp 已经提供了。作业在不在跑，查 `crisp jobs` 就知道。不需要在 JobStore 里再存一份。

`tracked` 表不是状态机，只是一个"待检查列表"——弥补 crisp 不持久化历史。添加时机：`_submit_or_skip` 成功提交 crisp 后。删除时机：收敛后、放弃后。

## 轮询逻辑

```python
crisp_active = _crisp_active_dirs(skip=False)

for row in JobStore().tracked_dirs():
    wd = Path(row["dir_path"])

    if str(wd.resolve()) in crisp_active:
        continue                       # 还在跑，跳过

    if check_converged(wd):
        move_crisp_outputs(wd)
        _cache_phase_results(wd)
        JobStore().record(str(wd.resolve()), "converged")
        JobStore().untrack(str(wd.resolve()))
        continue

    outcar = wd / "OUTCAR"
    if not outcar.is_file():
        outcar = wd / "output" / "OUTCAR"
    if not outcar.is_file():
        if time.time() - row["submitted_at"] > 7 * 86400:
            JobStore().record(str(wd.resolve()), "failed", reason="orphaned")
            JobStore().untrack(str(wd.resolve()))
        continue

    tail = _tail_text(outcar, 4096)
    if "General timing and accounting" not in tail:
        JobStore().record(str(wd.resolve()), "failed", reason="vasp_crash")
        JobStore().untrack(str(wd.resolve()))
        continue

    # VASP 正常结束但未收敛 → CONTCAR 重启或放弃
    _handle_unconverged(wd)
```

## CONTCAR 重启

```python
MAX_RESTART = 5

def _handle_unconverged(wd: Path) -> None:
    attempt = JobStore().latest_attempt(wd) or 0

    if attempt >= MAX_RESTART:
        JobStore().record(str(wd.resolve()), "failed",
                          reason="unconverged", attempt=attempt)
        JobStore().untrack(str(wd.resolve()))
        return

    restart_from_contcar(wd)
    _increase_nsw(wd)
    job = submit_vasp(wd.resolve())
    JobStore().record(str(wd.resolve()), "failed", reason="restart",
                      attempt=attempt + 1, task_name=job.task_name)
    # tracked 不变 — 仍在待检查列表中，等待下次完成
```

## 清理

| 文件 / 代码 | 操作 |
|---|---|
| `vasp_sop/core/cache.py` 中 `_submission_db()`、`mark_submitted`、`is_submitted`、`clear_submission`、`_get_submitted_dirs` | 删除 |
| `~/.vasp_sop/submissions.db` | 删除 |
| `_advance_one_system()` 中的 `mark_submitted` 调用 | 替换为 `JobStore().track()` |
| `_batch_run()` 轮询循环 | 重构 |

## 验证

| 场景 | 验证方法 |
|---|---|
| 正常收敛 | crisp 完成 → OUTCAR 收敛 → JobStore("converged") → untrack |
| 不收敛 + 重启 | OUTCAR 不收敛 → CONTCAR 重启 → crisp 再提交 → 最终收敛 |
| 多次重启后放弃 | 不收敛 × 5 → JobStore("failed", "unconverged") → untrack |
| VASP 崩溃 | 无 General timing → JobStore("failed", "vasp_crash") → untrack |
| crisp 删除记录 | tracked 还在 → 轮询仍会检查 OUTCAR |
| 超时孤儿 | 7 天无 OUTCAR → JobStore("failed", "orphaned") → untrack |
| _phase() 不受影响 | failed 目录被跳过，不阻塞其他缺陷的 DONE 判断 |
