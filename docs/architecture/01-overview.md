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

## 2. 关键改动路线

| 改动 | 原因 | 日期 |
|---|---|---|
| submissions.db 删除 → JobStore track/untrack | 统一数据源 | 07-08 |
| JobStore 状态: waiting/running/done → submitted/converged/failed | 覆盖不收敛和崩溃 | 07-08 |
| 轮询: tracked_dirs + crisp jobs | 无需 submissions.db | 07-08 |
| 轮询: 收敛/不收敛/崩溃 三分支 | 不让不收敛的卡死 | 07-08 |
| CONTCAR 重启 + 停滞检测 | 自动恢复不收敛的缺陷 | 07-08 |
| check_converged: 弛豫用力/VASP reached…；非弛豫只看结束 | 任务类型分流，见 06-convergence | 07-14 |
| check_task_complete: dielectric 跳过受力；band/dos 要 vasprun | DFPT / 能带产物 | 07-08 |
| Phase 改名 | 更直观 | 07-08 |
| _phase() 跳过 failed 缺陷 | 不阻塞 COMPLETE | 07-08 |
