---
name: vasp-sop-exclusion-gate-audit
description: 在 vasp-sop 加缺陷集排除/筛选语义（is_valid_defect_dir 类门）时审计所有消费路径，避免取消后作业每轮复活。用于实现 ADR 0013 类筛选、排除复杂缺陷/SOC 体系等。
---

# vasp-sop 排除门全路径审计

## 背景（2026-08-10 实证）

ADR 0013（阴-阳错位反位排除）落地时只改了 `is_valid_defect_dir` 门——但该门**不是**所有提交路径的入口。实际效果：取消作业后 wave2 和 poll 每轮重新提交被筛目录（498 个作业白跑 + 占派发额度，三轮回取消才清完）。根因：多个路径直接 iterdir / 读 tracked 表，绕过门。

## 门的全部消费路径（改门后逐个检查）

| 路径 | 文件 | 验证方式 |
|---|---|---|
| wave2 组扫描 | `orchestrator.py` wave2_submit 第一个 `for c in sorted(df_root.iterdir())` | 被筛目录不进 groups/verdicts |
| wave2 提交扫描 | 同函数第二个 iterdir 循环 | 被筛目录不进 `_submit_or_skip` |
| poll/restart | `_poll_tracked`（tracked 表） | 被筛目录必须 `untrack` + continue，绝不 restart/resubmit |
| reconcile | `_reconcile_stale` | stale submitted 记录不复活被筛目录 |
| webui 统计 | crisp `webui/api/routes_progress.py::_task_dirs` | **镜像规则必须同步**（crisp 侧独立实现 `_is_anion_cation_antisite`，改 vasp-sop 规则时同步改 crisp + 重启 crisp-gui） |
| backfill/finalize | `_backfill` / `finalize_converged` | 被筛目录的收敛结果不进后处理 |

## 验证闭环（改完必须全做）

1. `pytest tests/` 全量（现有：419 passed）
2. `systemctl --user restart vasp-sop-loop.service`（加载新代码）
3. 清 crisp 队列残留：`agent.db` 查 `status IN ('submit','submitted','running','ready_fetch')` + 被筛名 → `crisp cancel -n <task>`（xargs -P 8 并行）
4. **等 2 个 poll 周期（poll 120s）后复查**：被筛活跃数必须 = 0 且无新创建——否则有漏网路径，回表 grep 日志 `Submit crisp VASP in` 定位

## 陷阱

- `crisp cancel` 后 poll 若仍在 tracked 表会立即复活——先修代码重启再取消
- tracked 表检查 `wd.parts` 含 `defect` 段判断（unitcell/cpd 目录不受影响）
- 测试假 OUTCAR 无 IBRION 标签会被 verdict 判"非弛豫→converged"——断言前给 INCAR 补 IBRION=2
- webui 完成率口径 = vasp-sop 门（crisp 侧镜像），改一侧不改另一侧 = 显示不一致
