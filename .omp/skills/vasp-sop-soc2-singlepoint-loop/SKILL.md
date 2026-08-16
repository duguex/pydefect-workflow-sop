---
name: vasp-sop-soc2-singlepoint-loop
description: "诊断 vasp-sop soc2/NSW=0 单点作业的截断-OUTCAR 无限重提循环：verdict schema v4 语义、poll 委托、同目录并发守卫、crisp 状态区分 running 误报。用户报\"作业完成但结果不算数/重跑不完/纯费电\"时使用。"
---

# vasp-sop soc2 单点截断循环诊断

ADR 0014 的 stage2（NSW=0 + LSORBIT 单点）作业完成后，OUTCAR **没有 ionic timing 段是正常形态**——判 truncated/crashed 会触发无限重提烧核循环。

## 症状
- 作业 slurm log 尾部 `CRISP_COMPLETED` + `reached required accuracy`，但 OUTCAR/vasprun.xml 截断在电子步中途（无 accuracy 无 timing）
- 同一目录多个 `*.log`（多次提交痕迹；slurm job id 跨时间复用，agent.db 可能只留最新 task——同 local_dir 覆盖）
- `batch blockers` 报 crashed 数量突增
- 目录反复重提、从不收敛

## 三步核查（按序）

1. **crashed 目录区分 crisp 状态**：对每个 crashed 目录查 agent.db latest（running/submit = 误报，运行中 OUTCAR 写到一半；completed = 真异常；failed = 失败）
2. **completed 但 OUTCAR 无 timing**：`grep -c "reached required accuracy" OUTCAR` 和 `grep -c "General timing" OUTCAR`——两者皆 0 = 输出被并发/竞态破坏（不是 VASP 失败）
3. **循环确认**：crisp 日志（~/.crisp/logs/crisp.log）同目录多次 "Dispatched to SLURM" + poll 路径 record(failed, vasp_crash) + untrack

## 修复（2026-08-12 已入代码，5ebdd71）

- **verdict schema v4**：OUTCAR 尾部有 `reached required accuracy` 但无 timing → fallthrough 到单点豁免（NSW<=1/ibrion 非弛豫 → converged）；真截断（无 accuracy）仍 truncated；弛豫被 kill 后走力门。schema 变更必须 bump `_VERDICT_SCHEMA`（convergence.py），否则 sidecar 缓存重放旧判定
- **poll 路径**（orchestrator `_poll_tracked`）：crash 判定委托 `convergence_verdict`（`v.reason == "truncated" and not v.converged`），不再裸查 timing
- **同目录并发守卫**：`_submit_or_skip` 提交前查 `crisp_active_dirs()`，live 目录跳过——一个点覆盖所有提交腿（cpd/defect/soc2/UC/perfect）

## 验证

- 单元测试：`tests/test_convergence_nelm.py::TestSocSinglePointVerdict`（3 例）、`tests/test_defect_zbrent_restart.py::TestPollSinglePoint`、`TestConcurrencyGuard`
- 真实批次：修复后重提作业（无并发）OUTCAR 完整 → verdict converged

## 关联

- plan 键层级陷阱：`stage2_soc` 必须在 `parameters:` 块（顶层会静默禁用 stage2 腿——解析器已容错 + warning）
- 状态判定一律走 `vasp-sop batch dir-status <root> <dir>`（DB + 磁盘证据合并，避免 agent.db 不完整误判）
