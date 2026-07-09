# docs/architecture

vasp-sop 架构文档索引。

| # | 文档 | 内容 |
|---|---|---|
| 01 | [01-overview.md](01-overview.md) | 整体流程、关键改动路线 |
| 02 | [02-data-storage.md](02-data-storage.md) | 数据库设计 (jobs.db)、job_history + tracked |
| 03 | [03-batch-loop.md](03-batch-loop.md) | `_batch_run()` 主循环、回填、轮询、CONTCAR 重启 |
| 04 | [04-phase-machine.md](04-phase-machine.md) | `_phase()` 阶段机、COMPLETE 判断条件 |
| 05 | [05-advance-system.md](05-advance-system.md) | `_advance_one_system()` 各阶段操作 |
| 06 | [06-convergence.md](06-convergence.md) | `check_converged()` NSW 逻辑、dielectric 特殊处理 |
