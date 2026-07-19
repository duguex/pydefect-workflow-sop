# Batch Run Logging & Snapshot System

**Date:** 2026-07-16  
**Status:** draft

---

## 目标

`vasp-sop batch run --loop` 后台运行后，操作者可事后回溯并进行外部查询。

1. **运维日志文件**：每轮 poll/提交/失败全量记录  
2. **快照**：JSON（覆盖最新状态）+ JSONL（时序追加）  
3. **终端**：默认静默，仅异常/错误摘要  
4. `--verbose` 行为不变

---

## 组件

### 1. `LogConfig` — 日志文件与终端分流（`vasp_sop/core/logging.py`）

| 项目 | 配置 |
|------|------|
| 日志文件路径 | `{root}/batch_run.log`（默认，可通过 `--log` 覆盖） |
| 日志文件级别 | INFO |
| 终端级别 | **WARNING**（loop 模式）；非 loop 保持现状 INFO |
| 格式 | `%(asctime)s %(levelname)s %(message)s`（与现有一致） |

- 在 `_batch_run` 启动时调用 `LogConfig.setup_file_logging()`  
- 非 loop 不建文件日志（保持现有行为，不破坏单次交互习惯）

### 2. `SnapshotWriter` — 体系快照（`vasp_sop/core/snapshot.py`）

类 `SnapshotWriter(root: Path)`：

- `write(state: dict)`  
  - 覆盖写入 `{root}/batch_snapshot.json`（最新一条）  
  - 追加一行 JSON 至 `{root}/batch_timeline.jsonl`（带 `timestamp`）

`state` 字典字段：

```json
{
  "timestamp": "2026-07-16T08:30:12Z",
  "phases": {"COMPLETE": 18, "UNITCELL_DEFECT": 22},
  "analyze": {"full": 9, "partial": 25, "failed": 6},
  "crisp_active": 47,
  "crisp_running": 12,
  "defects_ready": 1506,
  "defects_unconverged": 564,
  "jobstore_submitted": 290,
  "errors": [{"system": "SeO2", "reason": "unitcell.yaml zero_gap"}]
}
```

- 每轮 advance 结束后写入  
- `SnapshotWriter.last()` 读回上一轮 JSON（供 `_batch_run` 做进度 diff）

### 3. 终端输出调整（`vasp_sop/cli/main.py`）

| 原来 | 改为 |
|------|------|
| `print("→ … submitted")` | `logging.info("…")`（进文件） |
| `print("  Cached N completed")` | `logging.info("Cached N completed")` |
| `print("  ~ … post-process partial")` | `logging.info("… post-process partial")` |
| `print("  ✗ … post-process failed")` | `logging.error("… post-process failed")` |
| `print("  [{N}/{M}] {name} … done")` | `logging.info("[{N}/{M}] {name} … done")` |
| 阶段/计数摘要 | 保持 `print()`（终端可见，不进日志——不影响判断） |
| ERROR/WARNING 摘要 | 已是 `logging.error/warning`，终端会显示（level=WARNING） |

**具体交互控制：** loop 模式内所有信息性输出走 `logging.info`，只到文件；`logging.warning` 及以上到终端。非 loop 模式一切照旧。

---

## 调用时机

```
_batch_run(loop=True):
  1. LogConfig.setup_file_logging(root)
  2. SnapshotWriter(root) 初始化
  3. while True:
       a. poll + advance（日志现有 logging 不变）
       b. 构建 state dict
       c. snapshot.write(state)
       d. sleep
```

非 loop 不建文件日志/快照。

---

## 文件产物（loop 模式）

| 文件 | 位置 | 格式 |
|------|------|------|
| 运维日志 | `{project_root}/batch_run.log` | 纯文本，与现有日志格式一致 |
| 最新快照 | `{project_root}/batch_snapshot.json` | JSON（覆盖） |
| 时序快照 | `{project_root}/batch_timeline.jsonl` | JSONL（追加，带 timestamp） |

---

## 非目标

- 不引入新依赖（纯 stdlib `logging` + `json`）  
- 不改 subagent/worker 架构  
- 不实现日志轮转（文件会一直增长；loop 模式下建议外部 logrotate）  
- 不替换 `print()` 的所有调用——只改信息性输出，终端交互保留

---

## 测试

- `test_logging.py`：文件 handler 写入、终端 level 分流  
- `test_snapshot.py`：覆盖 + 追加、read-back、字段完整性  
- `test_cli.py`：loop 模式输出不包含旧 print 噪声