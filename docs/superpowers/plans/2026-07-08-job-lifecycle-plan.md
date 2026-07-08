# Job Lifecycle Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** 统一作业生命周期管理，合并 submissions.db 进 JobStore，扩展状态，CONTCAR 重启集成。

**State names:**

JobStore per-calculation:
- `pending` — 还没提交
- `submitted` — 已提交到 crisp
- `converged` — OUTCAR 收敛
- `failed` — 放弃了（reason: unconverged / vasp_crash / orphaned / restart）

_phase() 系统级:
- `NO_TARGET` / `STRUCTURE_OPT` / `COMPETING` / `CHEM_POT_DIAGRAM` / `UNITCELL_DEFECT` / `COMPLETE`

## Global Constraints

- 所有状态名这次改完
- `submissions.db` 删除，数据并入 JobStore
- JobStore 加 `track()` / `untrack()` / `tracked_dirs()` 方法
- 不再创建新的数据库文件

---

### Task 1: JobStore 扩展 — track 方法 + failed 状态

**Files:**
- Modify: `vasp_sop/core/job_store.py`

**Changes:**

1. `_init_db()` 加 `tracked` 表：

```sql
CREATE TABLE IF NOT EXISTS tracked (
    dir_path TEXT PRIMARY KEY,
    submitted_at REAL NOT NULL
);
```

2. 加三个方法：

```python
def track(self, dir_path: str) -> None:
    """加入待检查列表（提交时调用）。"""
    db = self._connection()
    try:
        db.execute(
            "INSERT OR REPLACE INTO tracked (dir_path, submitted_at) VALUES (?, ?)",
            (dir_path, time.time()),
        )
        db.commit()
    finally:
        db.close()

def untrack(self, dir_path: str) -> None:
    """从待检查列表移除（收敛或放弃时调用）。"""
    db = self._connection()
    try:
        db.execute("DELETE FROM tracked WHERE dir_path = ?", (dir_path,))
        db.commit()
    finally:
        db.close()

def tracked_dirs(self) -> list[dict]:
    """返回 tracked 表中所有目录。"""
    db = self._connection()
    try:
        rows = db.execute(
            "SELECT dir_path, submitted_at FROM tracked ORDER BY submitted_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()
```

3. 改名 `VALID_STATUSES`：

```python
_VALID_STATUSES = frozenset({"pending", "submitted", "converged", "failed"})
```

- [ ] Steps: 修改文件 → 验证 import → 测试

---

### Task 2: 替换 submissions.db

**Files:**
- Modify: `vasp_sop/core/cache.py` — 删除 `_submission_db()`、`mark_submitted`、`is_submitted`、`clear_submission`、`_get_submitted_dirs`
- Modify: `vasp_sop/cli/main.py` — 所有 `mark_submitted` 调用改为 `JobStore().track()` + `JobStore().record("submitted")`

**Changes in cache.py:**

删除以下函数：
- `_submission_db()`
- `mark_submitted(dir_path, task_name)`
- `is_submitted(dir_path)`
- `clear_submission(dir_path)`
- `_get_submitted_dirs()`

删除 submissions.db 数据迁移（生产环境数据已知无需保留）。

**Changes in main.py:**

1. `_advance_one_system()` 内的 `_submit_or_skip`：

当前：
```python
mark_submitted(str(path.resolve()), job.task_name)
JobStore().record(str(path.resolve()), "running")
```

改为：
```python
js = JobStore()
js.track(str(path.resolve()))
js.record(str(path.resolve()), "submitted",
          source=job.task_name or "batch_run")
js.close()
```

2. `_batch_run()` 中的 `mark_submitted("restored")` 调用：

当前：
```python
mark_submitted(p, "restored")
```

改为：
```python
JobStore().track(p)
JobStore().record(p, "submitted", source="restored")
```

3. `_batch_submit()` 中的 `mark_submitted` 调用（如果在）→ 同上。

---

### Task 3: 重构轮询循环

**Files:**
- Modify: `vasp_sop/cli/main.py`

替换 `_batch_run()` 中的轮询部分（当前是 `for wd_str in list(_get_submitted_dirs())`）：

```python
# 新轮询：查 tracked 表 + crisp 活跃列表
crisp_active = _crisp_active_dirs(skip=False)

for row in JobStore().tracked_dirs():
    wd = Path(row["dir_path"])
    wd_str = str(wd.resolve())

    if wd_str in crisp_active:
        continue

    # crisp 已不再追踪 → 检查 OUTCAR
    if check_converged(wd):
        move_crisp_outputs(wd)
        _cache_phase_results(wd)
        JobStore().record(wd_str, "converged")
        JobStore().untrack(wd_str)
        completed += 1
        continue

    outcar = wd / "OUTCAR"
    if not outcar.is_file():
        outcar = wd / "output" / "OUTCAR"
    if not outcar.is_file():
        if time.time() - row["submitted_at"] > 7 * 86400:
            JobStore().record(wd_str, "failed", reason="orphaned")
            JobStore().untrack(wd_str)
        continue

    tail = _tail_text(outcar, 4096)
    if "General timing and accounting" not in tail:
        JobStore().record(wd_str, "failed", reason="vasp_crash")
        JobStore().untrack(wd_str)
        continue

    # VASP 正常结束但未收敛 → CONTCAR 重启或放弃
    _handle_unconverged_poll(wd)
```

---

### Task 4: CONTCAR 重启

**Files:**
- Modify: `vasp_sop/cli/main.py`

```python
MAX_RESTART = 5

def _handle_unconverged_poll(wd: Path) -> None:
    """VASP 正常结束但未收敛 → 重启或放弃。"""
    wd_str = str(wd.resolve())
    latest = JobStore().latest(wd_str)  # {"status", "reason", "attempt", ...}
    attempt = latest.get("attempt", 0) if latest else 0

    if attempt >= MAX_RESTART:
        JobStore().record(wd_str, "failed", reason="unconverged", attempt=attempt)
        JobStore().untrack(wd_str)
        return

    restart_from_contcar(wd)
    # NSW += 500
    incar_path = wd / "INCAR"
    if incar_path.is_file():
        import re
        text = incar_path.read_text()
        m = re.search(r"NSW\s*=\s*(\d+)", text)
        nsw = int(m.group(1)) + 500 if m else 1000
        if m:
            text = re.sub(r"NSW\s*=\s*\d+", f"NSW = {nsw}", text)
        else:
            text += f"\nNSW = {nsw}"
        incar_path.write_text(text)

    job = submit_vasp(wd.resolve())
    JobStore().record(wd_str, "submitted",
                      source=job.task_name, attempt=attempt + 1)
    # tracked 不变
```

---

### Task 5: 更新 _phase() 状态名 + UC_DF 检查

**Files:**
- Modify: `vasp_sop/cli/main.py`

1. `_phase()` 返回值改名：

| 旧 | 新 |
|---|---|
| `TARGET` | `STRUCTURE_OPT` |
| `CPD_POST` | `CHEM_POT_DIAGRAM` |
| `UC_DF` | `UNITCELL_DEFECT` |
| `DONE` | `COMPLETE` |
| `COMPETING` | 不变 |
| `NO_TARGET` | 不变 |

2. `_phase()` 中的 COMPLETE 检查：跳过 `failed` 状态的缺陷目录。

当前检查每个缺陷目录都需要中间文件，改为遇到 `failed` 状态跳过：

```python
for d in defect_dirs:
    if d.name == "perfect":
        continue
    if JobStore().latest(str(d.resolve())).get("status") == "failed":
        continue
    if not (d / "calc_results.json").is_file():
        return "UNITCELL_DEFECT"
    ...
```

3. `_advance_one_system()` 中的阶段名同步更新。

4. `_PRIORITY_MAP` 等常量不变。

---

### Task 6: 更新测试

**Files:**
- Modify: `tests/test_job_store.py` — 加 track/untrack/tracked_dirs 测试
- Modify: `tests/test_cli.py` — 更新阶段名断言

---

### Task 7: 清理

- 删除 `~/.vasp_sop/submissions.db`
- 更新 `AGENTS.md`
- 更新 `docs/architecture.md`
