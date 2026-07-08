# 当前状态机逻辑（as-is）

> 2026-07-08，修改前

## 1. 整体循环 (_batch_run)

```
_batch_run()
  │
  ├── 回填缓存 (backfill)
  │     cpd/ 竞争相目录 → 收敛了就缓存 + JobStore("done")
  │
  ├── 孤儿清理 (orphan sweep)
  │     unitcell/ defect/ 下 output/ 残留 → 移出来
  │
  ├── 轮询已完成 (poll)
  │     submissions.db 活跃条目
  │     └── check_converged? → True → 收集 → 缓存 → clear_submission
  │                          → False → 什么都不做（卡住！）
  │
  └── 逐个推进系统 (_advance_one_system)
        查 _phase(s) → 执行对应阶段操作
```

## 2. 阶段机 (_phase)

```
_phase(s)
  │
  ├── target_vertices.yaml 存在?
  │   ├── No ──┐
  │   │        ├── target 没 done → TARGET
  │   │        ├── _competing_dirs 非空 → COMPETING
  │   │        └── 否则 → CPD_POST
  │   │
  │   └── Yes ──┐  （已越过 CPD 门，不再回退）
  │              ├── UC 输入未生成 → UC_DF
  │              ├── UC job 没 done → UC_DF
  │              ├── unitcell.yaml 不存在 → UC_DF
  │              ├── CPD 中间文件缺 → UC_DF
  │              ├── defect_energy_summary.json 不存在 → UC_DF
  │              ├── 逐缺陷检查中间文件 → 缺就 UC_DF
  │              └── 全部齐全 → DONE
```

## 3. _advance_one_system 各阶段

### TARGET
```
if target 在缓存中: restore_from_cache
else: 什么都不做（等外部提交）
```

### COMPETING
```
for 每个 need-VASP 的竞争相:
    _submit_or_skip → crisp submit
```

### CPD_POST
```
move_crisp_outputs(收敛的竞争相)
compute_chemical_potentials() → target_vertices.yaml
```

### UC_DF（最复杂）
```
1. 构建缺陷结构 build_defects()
2. 生成 VASP 输入 _generate_vasp_inputs()

3. 提交 UC 任务（band / dos / dielectric）
   for 每个 UC task:
     check_task_complete?  → JobStore("done"), skip
     is_submitted?         → skip
     JobStore("done")?     → skip
     prepare_inputs + _submit_or_skip → crisp submit

4. 提交缺陷任务
   if summary 不存在:
     for 每个缺陷目录:
       input_ready?         → skip
       check_converged?     → JobStore("done"), skip
       is_submitted?        → skip
       JobStore("done")?    → skip
       _submit_or_skip     → crisp submit

5. 判断是否触发后处理
   if uc_all_done and df_vasp_done and df_vasp_ondisk:
       build_unitcell_yaml
       _analyze_defects → defect_energy_summary.json
```

## 4. _submit_or_skip

```
_submit_or_skip(path)
  │
  ├── dry_run? → print + return
  │
  └── 正常:
        job = submit_vasp(path)     → crisp submit
        mark_submitted(path, job.task_name)  → submissions.db
        JobStore().record("running")
        print "→ 系统 任务名: job.task_name"
```

## 5. 三个数据源

| 数据源 | 存储 | 用途 | 问题 |
|---|---|---|---|
| **submissions.db** | SQLite | 防止重复提交，轮询 | 不清楚除，卡死 |
| **JobStore (jobs.db)** | SQLite | 计算状态 waiting/running/done | 3 态不够，缺少 failed |
| **Maggma cache** | JSON 文件 | 跨项目缓存 VASP 结果 | 不是状态追踪 |

## 6. 当前已知问题

1. **轮询不处理未收敛** — `check_converged` False 时什么都不做
2. **少数未收敛阻塞全部** — `df_vasp_ondisk` 要求全部收敛才后处理
3. **状态不全** — 无 failed / unconverged
4. **两个库** — submissions 和 jobs.db 分离
