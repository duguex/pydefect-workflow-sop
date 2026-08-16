---
name: vasp-sop-stale-marker-loop
description: "Diagnose vasp-sop systems stuck in COMPETING or cpd dirs resubmitted every cycle due to stale local .failed markers: full-tree marker scan, convergence cross-check, repeated-completion quantification, phase-gate impact. Use when 相位卡在 COMPETING / 重复提交 / crisp 反复跑秒级作业 / 相位计数冻结."
---

# vasp-sop 陈旧 .failed 标记循环诊断

**症状**：系统卡 COMPETING、cpd 目录每 cycle 重提（crisp 反复跑秒级微作业）、相位计数多轮冻结、同目录多个 completed crisp 行。

**根因**（2026-08-11 确认，issue #120）：crisp 失败时 `_fetch_results` 把远端 `.failed` 拉到本地 job 目录；之后成功重跑时**本地标记从不清理**（submit.slurm 的 `rm -f .failed` 只作用于远端）。vasp-sop `crisp_terminal_status()`（jobs.py:340）只读本地标记 → 永久 "failed" → `System.competing_dirs()`（system.py:379）每轮重提该目录 + `competing_blockers()`（system.py:410）阻塞 CPD 后处理 → 相位门 `competing_dirs() or competing_blockers()` 永远非空 → 卡 COMPETING。同根因：2025 批次 issue #112。

## 诊断步骤

```bash
# 1. 全树扫描 .failed 标记
find <2026_root> -name .failed | wc -l

# 2. 交叉验证陈旧 vs 真实失败（.venv/bin/python）
#    对每个带标记目录：OUTCAR 存在 + convergence_verdict(d).converged = 陈旧循环
#    陈旧/真实 分类统计 + 按系统分布

# 3. 量化重提浪费
sqlite3 ~/.crisp/data/agent.db "SELECT local_dir, COUNT(*) FROM jobs WHERE status='completed' AND local_dir LIKE '%<batch>%' GROUP BY local_dir HAVING COUNT(*) >= 3 ORDER BY 2 DESC;"

# 4. 相位冻结证据
tail batch_timeline.jsonl   # 多轮 phases 计数不变
```

注意：标记 mtime 早于 completed 行 + OUTCAR 已收敛 = 陈旧；标记 + verdict 未收敛 = 真实失败（保留）。真实失败示例：force_gate_fail、truncated、missing_outcar。

## 修复（先提 issue 再改，流程见 vasp-sop-batch-supervision）

1. **止血**：删陈旧 `.failed`（OUTCAR 更新 + verdict converged 的目录），保留真实失败目录
2. **vasp-sop 代码**：`competing_dirs()`/`competing_blockers()` 的 failed 分支先查 `convergence_verdict`（OUTCAR 收敛视为完成），或比较 marker mtime vs OUTCAR mtime
3. **crisp 契约**：重提时清理本地 `.failed`/`.completed`（vasp-sop jobs.py docstring 已声明该契约；crisp repo docs/issues/0001 记录同根因）

## 关键文件

- vasp_sop/vasp_sop/core/jobs.py:340 `crisp_terminal_status()`
- vasp_sop/vasp_sop/core/system.py:358-418 `competing_dirs()` / `competing_blockers()`
- vasp_sop/vasp_sop/core/orchestrator.py:1693 `run()` cycle 顺序（backfill → poll_tracked → reconcile → advance）
