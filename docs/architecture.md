# vasp-sop 架构与业务逻辑

> 最后更新: 2026-07-08
> 涵盖: submissions.db 删除、JobStore 状态变更、NSW 收敛判定、CONTCAR 重启集成

---

## 1. 整体流程

```
用户: vasp-sop batch run .
                │
         ┌──────┴──────┐
         │ _batch_run() │
         └──────┬──────┘
                │
     ┌──────────┼──────────────┐
     │          │              │
     ▼          ▼              ▼
  回填缓存   孤儿清理     轮询已完成 + 重启
  (backfill) (orphan)     (poll + restart)
                             │
                             ▼
                      逐个推进系统
                      _advance_one_system()
                      (每个系统一次)
```

## 2. 数据存储

单一数据库 `~/.vasp_sop/jobs.db`，两张表：

### job_history — VASP 计算最终状态

| 字段 | 类型 | 说明 |
|---|---|---|
| `dir_path` | TEXT | 计算目录完整路径 |
| `status` | TEXT | `converged` / `failed` |
| `reason` | TEXT | 失败原因: `unconverged` / `vasp_crash` / `orphaned` / `stalled` |
| `timestamp` | REAL | 记录时间 |
| `attempt` | INTEGER | CONTCAR 重启次数（0-5） |
| `task_name` | TEXT | crisp 任务 ID |

### tracked — 待检查列表

| 字段 | 类型 | 说明 |
|---|---|---|
| `dir_path` | TEXT | 已提交到 crisp 但尚未出结果的目录 |
| `submitted_at` | REAL | 提交时间 |

`submissions.db` 已删除。所有状态统一在 `jobs.db` 中。

## 3. `_batch_run()` 主循环

### 3.1 回填缓存 (Backfill)

```python
for 每个系统:
    for 每个 cpd/竞争相手目录:
        if JobStore().latest(dir) == "converged":
            continue           # 已记录，跳过
        if not check_converged(dir):
            continue           # 没收敛，跳过
        _cache_put(dir)        # 写入 maggma 缓存
        JobStore().record(dir, "converged", source="backfill")
```

### 3.2 轮询已完成作业 (Poll)

```python
crisp_active = _crisp_active_dirs(skip=False)  # crisp 当前活跃列表

for row in JobStore().tracked_dirs():
    wd = Path(row["dir_path"])
    if str(wd.resolve()) in crisp_active:
        continue                # 还在集群上跑

    if check_converged(wd):
        move_crisp_outputs(wd)
        _cache_phase_results(wd)
        JobStore().record(wd, "converged")
        JobStore().untrack(wd)
        continue

    outcar = wd / "OUTCAR"
    if not outcar.is_file():
        outcar = wd / "output" / "OUTCAR"
    if not outcar.is_file():
        if time.time() - row["submitted_at"] > 7 * 86400:
            JobStore().record(wd, "failed", reason="orphaned")
            JobStore().untrack(wd)
        continue

    tail = tail_read(outcar, 4096)
    if "General timing and accounting" not in tail:
        JobStore().record(wd, "failed", reason="vasp_crash")
        JobStore().untrack(wd)
        continue

    # VASP 正常结束但未收敛 → CONTCAR 重启或放弃
    _handle_unconverged_poll(wd)
```

### 3.3 CONTCAR 重启 + 停滞检测

```python
MAX_RESTART = 5
STALL_THRESHOLD = 0.99  # 受力改进 < 1% 即停滞

def _handle_unconverged_poll(wd):
    cur_f = parse_max_f(OUTCAR)  # 解析当前最大受力
    history = JobStore().history(wd)

    # 停滞检测：与上次重启前的受力比较
    if cur_f > 0 and attempt > 0:
        prev_f = history 中上一次 restart 的 prev_f
        if cur_f >= prev_f * STALL_THRESHOLD:
            JobStore().record(wd, "failed", reason="stalled")
            untrack
            return

    if attempt >= MAX_RESTART:
        JobStore().record(wd, "failed", reason="unconverged")
        untrack
        return

    restart_from_contcar(wd)   # CONTCAR → POSCAR
    NSW += 500
    submit_vasp(wd)             # 重新提交到 crisp
    JobStore().record(wd, "submitted", attempt + 1)
```

### 3.4 逐个推进系统

```python
for 每个系统:
    p = _phase(s)
    if p == COMPLETE or NO_TARGET:
        continue
    _advance_one_system(s)
```

## 4. 阶段机 (`_phase()`)

### 4.1 阶段定义

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

### 4.2 COMPLETE 的具体判断

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

## 5. 收敛判定 (`check_converged`)

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

### dielectric 特殊处理

DFPT 介电计算（`IBRION=8`、`LEPSILON=True`）不做离子弛豫，`check_converged` 中的受力判断不适用。`check_task_complete("dielectric")` 直接跳过 `check_converged`，只检查 OUTCAR 存在 + VASP 正常结束。

## 6. `_advance_one_system()` 各阶段操作

### 6.1 STRUCTURE_OPT 阶段（原 TARGET）

```python
if 缓存命中: restore_from_cache(target_dir)
```

### 6.2 COMPETING 阶段

```python
for 每个需要提交的竞争相目录:
    if 未提交且未收敛且 JobStore 未 done:
        _submit_or_skip(dir) → crisp submit
        JobStore().track(dir) + record("submitted")
```

### 6.3 CHEM_POT_DIAGRAM 阶段（原 CPD_POST）

```python
move_crisp_outputs(收敛的竞争相)
compute_chemical_potentials(cpd_root)
```

### 6.4 UNITCELL_DEFECT 阶段（原 UC_DF）

```python
build_defects(defect_root, target_dir)
_generate_vasp_inputs(defect_root)

for task in (band, dos, dielectric):
    if check_task_complete(task_dir, task):  JobStore converged，跳过
    if JobStore "submitted":                 跳过
    if JobStore "converged":                 跳过
    prepare_inputs + _submit_or_skip

if defect_energy_summary.json 不存在:
    for 每个缺陷子目录:
        if 输入不齐全:                      跳过
        if check_converged:                 记录 converged，跳过
        if JobStore "submitted":             跳过
        if JobStore "converged":             跳过
        _submit_or_skip

if UC 全部完成 AND 缺陷全部完成 AND 全部有 OUTCAR:
    build_unitcell_yaml(uc_root)
    _analyze_defects(...)
```

## 7. _submit_or_skip

```python
def _submit_or_skip(path):
    job = submit_vasp(path)         → crisp submit
    JobStore().track(path)          → 加入待检查列表
    JobStore().record("submitted")  → 记录提交状态
```

## 8. 关键改动路线

| 改动 | 原因 | 日期 |
|---|---|---|
| submissions.db 删除 → JobStore track/untrack | 统一数据源 | 07-08 |
| JobStore 状态: waiting/running/done → submitted/converged/failed | 覆盖不收敛和崩溃 | 07-08 |
| 轮询: tracked_dirs + crisp jobs | 无需 submissions.db | 07-08 |
| 轮询: 收敛/不收敛/崩溃 三分支 | 不让不收敛的卡死 | 07-08 |
| CONTCAR 重启 + 停滞检测 | 自动恢复不收敛的缺陷 | 07-08 |
| check_converged: 受力 → NSW 比较 | 和 pymatgen 一致 | 07-08 |
| check_task_complete: dielectric 跳过受力 | DFPT 不做弛豫 | 07-08 |
| Phase 改名 | 更直观 | 07-08 |
| _phase() 跳过 failed 缺陷 | 不阻塞 COMPLETE | 07-08 |
