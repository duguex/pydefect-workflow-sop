# vasp-sop 架构与业务逻辑

> 日期: 2026-07-08
> 目的: 透明化当前架构，供审查

---

## 1. 整体流程

```
用户: vasp-sop batch run .
                │
         ┌──────┴──────┐
         │ _batch_run() │
         └──────┬──────┘
                │
     ┌──────────┼──────────┐
     │          │          │
     ▼          ▼          ▼
  回填缓存   孤儿清理   轮询已完成
  (backfill) (orphan)   (poll)
                          │
                          ▼
                   逐个推进系统
                   _advance_one_system()
                   (每个系统一次)
```

---

## 2. `_batch_run()` 主循环

### 2.1 回填缓存 (Backfill)

```python
for 每个系统:
    for 每个 cpd/竞争相手目录:
        if JobStore().latest(dir) == "done":
            continue           # 已知完成了，跳过
        if not check_converged(dir):
            continue           # OUTCAR 没收敛，跳过
        # 收敛了 → 写入 maggma cache + JobStore
        _cache_put(dir)
        JobStore().record(dir, "done")
```

**作用**：把已完成但未缓存的竞争相 VASP 结果补进缓存。

**我改了什么**：把 `cache_lookup(dir)` 删了（原来是先查缓存，有就跳过）。因为 `_cache_put` 本身是幂等的，不需要预先检查。

### 2.2 孤儿输出清理 (Orphan Sweep)

```python
for 每个系统:
    for unitcell/ 和 defect/ 的子目录:
        if 子目录/output/OUTCAR 存在但没被处理:
            move_crisp_outputs(child)   # output/ → 上级目录
            _cache_phase_results(child) # 写入缓存
```

**作用**：crisp 把输出放在 `output/` 里，正常情况下轮询会移出来。如果进程中断了，`output/` 残留着，下次清理找到并处理。

### 2.3 轮询已完成作业 (Poll)

```python
for 每个 submission DB 中的活跃目录:
    if check_converged(wd):
        move_crisp_outputs(wd)     # output/ → 上级目录
        _cache_phase_results(wd)   # 写入 maggma 缓存
        clear_submission(wd_str)   # 清除提交标记
        JobStore().record(wd, "done")
```

**问题**：如果 `check_converged(wd)` 返回 False（VASP 跑完但没收敛），这个分支什么都不做。提交标记不清除，作业被认为还在跑，但事实上 crisp 已经完成了。**这是阻塞点之一。**

### 2.4 逐个推进系统

```python
for 每个系统:
    p = _phase(s)   # 判断当前阶段
    if p == DONE or NO_TARGET:
        continue
    _advance_one_system(s)  # 执行当前阶段的操作
```

---

## 3. 阶段机 (`_phase()`)

### 3.1 阶段定义

```
TARGET → COMPETING → CPD_POST → UC_DF → DONE
```

| 阶段 | 含义 | 判定条件 |
|---|---|---|
| `NO_TARGET` | 没有 MPID，无法运行 | `_target_dir()` 返回 None |
| `TARGET` | 目标相 VASP 没算完 | JobStore 说 target 没 `done` |
| `COMPETING` | 竞争相还有没提交的 | `_competing_dirs()` 返回非空 |
| `CPD_POST` | 竞争相算完了，CPD 待生成 | 无 competing dirs，`target_vertices.yaml` 不存在 |
| `UC_DF` | CPD 完成，UC/缺陷阶段 | `target_vertices.yaml` 存在 |
| `DONE` | 全线完成 | 全部 10 种中间文件齐全 |

### 3.2 UC_DF → DONE 的具体判断

```python
if target_vertices.yaml 存在:
    if UC 输入未生成:           return UC_DF
    if UC 有任务没做完:         return UC_DF
    if unitcell/unitcell.yaml 不存在: return UC_DF
    if cpd/*.yaml/.json 缺:     return UC_DF
    if defect 目录不存在:       return UC_DF
    if defect_energy_summary.json 不存在: return UC_DF
    for 每个缺陷子目录:
        if 缺 calc_results.json:  return UC_DF
        if 缺 correction.json:   return UC_DF
        if 缺 defect_structure_info.json: return UC_DF
    if 缺 perfect_band_edge_state.json: return UC_DF
    return DONE
```

---

## 4. `_advance_one_system()` 各阶段操作

### 4.1 TARGET 阶段

```python
if _crg(formula, mpid) 缓存命中:
    restore_from_cache(target_dir)   # 从缓存恢复文件
# 否则什么都不做（target 还没提交，等外部提交）
```

### 4.2 COMPETING 阶段

```python
for 每个需要提交的竞争相目录:
    if 未提交且未收敛且未缓存:
        _submit_or_skip(dir)  →  crisp submit
```

### 4.3 CPD_POST 阶段

```python
# 把收敛的竞争相输出移出来
for 每个 cpd 子目录:
    if 收敛: move_crisp_outputs(dir)
# 运行 CPD 管线
compute_chemical_potentials(cpd_root)
# 生成 target_vertices.yaml, chem_pot_diag.json 等
```

### 4.4 UC_DF 阶段 (最复杂)

```python
# 1. 构建缺陷结构（如果没有）
build_defects(defect_root, target_dir)
_generate_vasp_inputs(defect_root)  # 生成 INCAR/POTCAR/KPOINTS

# 2. 提交需要跑的 UC 任务
for task in (band, dos, dielectric):
    if 任务已完成 (check_task_complete):  记录 JobStore done，跳过
    if 已提交 (is_submitted):              跳过
    if JobStore 说 done:                   跳过
    prepare_inputs(dir)                     # 确保输入文件齐全
    _submit_or_skip(dir) → crisp submit    # 提交到集群

# 3. 提交需要跑的缺陷任务
if defect_energy_summary.json 不存在:
    for 每个缺陷子目录:
        if 输入不齐全:                    跳过
        if 已收敛 (check_converged):      记录 JobStore done，跳过
        if 已提交:                         跳过
        if JobStore 说 done:               跳过
        _submit_or_skip(dir) → crisp submit

# 4. 如果全部跑完了，触发后处理
if UC 全部 done AND 缺陷全部 done AND 缺陷全部有 OUTCAR:
    if defect_energy_summary.json 不存在:
        build_unitcell_yaml(uc_root)       # 生成 unitcell.yaml
        _analyze_defects(...)              # 11 步 pydefect 后处理
```

---

## 5. 状态追踪 (JobStore)

SQLite 表:

```sql
job_history(dir_path, status, timestamp, source)
```

状态三态：

```
waiting → running → done
```

**实际未覆盖的状态：**

| 情况 | 当前状态 | 应该是什么 |
|---|---|---|
| VASP 提交到 crisp | `running` | ✅ |
| VASP 完成 + 收敛 | `done` | ✅ |
| VASP 完成 + 未收敛 | 卡在 `running` 标记 | 缺 `unconverged` 状态 |
| VASP 崩溃/错误 | 同上有 OUTCAR 标记 | 缺 `failed` 状态 |

---

## 6. CONTCAR 重启

已有函数 `vasp_sop/vasp/io.py:restart_from_contcar()`:

```python
def restart_from_contcar(path):
    """Copy CONTCAR → POSCAR and set ISTART=1 for restart."""
    shutil.copy2(CONTCAR, POSCAR)
    INCAR 中设置 ISTART = 1
```

已有完整的重启循环 `vasp_sop/defect/compute.py:run_vasp()`:

```python
for attempt in range(20):
    submit 所有未收敛的缺陷
    等待完成
    for 每个缺陷:
        if check_converged(d):      done
        elif max_f 没下降:          stalled
        else:                       restart_from_contcar + 重提交
```

但这个函数走的是**本地 `submit_vasp`（subprocess + mpirun）**，没有集成到 **crisp 提交路径**。

---

## 7. 我改了什么（对原有逻辑的改动）

| 改动 | 位置 | 是否改变了语义 |
|---|---|---|
| 删除回填中的 `cache_lookup` | `_batch_run` backfill | ✅ 安全（`_cache_put` 幂等） |
| 回填加 `JobStore` 跳过 | `_batch_run` backfill | ✅ 性能优化 |
| 提交时记录 `running` | `_submit_or_skip` | ✅ 新增 |
| 轮询时记录 `done` | `_batch_run` poll | ✅ 新增 |
| `_phase()` 用 JobStore 代替 `cache_lookup` | `_phase()` | ⚠️ 语义有变 |
| `_phase()` 加 10 种中间文件检查 | `_phase()` | ✅ 更严格 |
| 跳过收敛目录时写 JobStore | UC_DF 提交循环 | ✅ 修复死锁 |
| 批量生成 VASP 输入（copy 替代 N×subprocess） | `_generate_vasp_inputs` | ✅ 性能优化 |
| 删除 `perfect` 的 `correction.json` 检查 | `_phase()` | ✅ 合理 |
| 删除 `defect_volume_fraction.json` 检查 | `_phase()` | ✅ 可视化文件 |
| dvf 失败不阻塞 | `analysis.py` | ✅ 非关键步骤 |

---

## 8. 当前阻塞点汇总

### 阻塞 1：CONTCAR 重启未集成到 crisp 路径

```
crisp 作业完成 → VASP 没收敛
  → 轮询发现 check_converged=False
  → 什么都不做
  → 提交标记不清除
  → 系统永远卡住
```

**影响**：30 个 UC_DF 系统中有 4-20 个缺陷属于此类。

### 阻塞 2：少数未收敛缺陷阻止全系统分析

```
df_vasp_ondisk = all(check_converged(child) for child in defect_dirs)
                → 因为 4 个缺陷没收敛，返回 False
                → 后处理不触发
                → 其他 26 个收敛的缺陷也卡住
```

**影响**：`_phase()` 判断 DONE 时也会按目录逐个检查中间文件，缺失就返回 UC_DF。

### 阻塞 3：JobStore 三态不够用

```
running 状态同时包含了：
  1. 正在集群上跑（正常）
  2. 跑完了但没收敛（应重启）
  3. 跑崩溃了（应放弃）
```

无法区分这三种情况，导致系统不知道下一步该做什么。
