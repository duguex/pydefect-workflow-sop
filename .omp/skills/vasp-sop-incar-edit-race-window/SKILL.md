---
name: vasp-sop-incar-edit-race-window
description: "修改 crisp 提交的 VASP 作业 INCAR 参数时，正确利用 fetch 恢复与提交快照之间的竞速窗口，并验证 INCAR.tuned（执行版）确认参数生效。用于任何\"改了 INCAR 但作业还是按旧参数跑/参数被还原\"的排查。"
---

# vasp-sop INCAR 修改生效窗口（crisp 提交/取回链）

## 问题

手动改本地 `INCAR` 后，作业仍按旧参数执行，或磁盘参数被静默还原（2026-08-12 BaAl2B2O7 POTIM=0.5 → 0.2 实证，issue #134）。

## 机制（crisp 代码定位）

1. **提交**：`crisp/shared/calculators/vasp.py::tune_input` 把磁盘 INCAR 复制为 `INCAR.tuned`（快照），tuning NCORE/NSIM/LREAL 只改 tuned 副本
2. **上传**：`crisp/daemon/server.py` `fname.replace(".tuned","")` —— INCAR.tuned 以 INCAR 名上传集群执行，**执行版 = 提交时刻快照**
3. **取回**：fetch 把远端执行版 INCAR 下载回本地，**覆盖本地 INCAR**（`daemon/connection.py` 注释 "VASP's INCAR.tuned → INCAR after auto-tuning"）

## 操作流程

1. **先查 agent.db 时间线**：`SELECT status, slurm_job_id, submit_time, complete_time FROM jobs WHERE local_dir LIKE '%<dir>' ORDER BY submit_time DESC LIMIT 2`
2. **确认窗口**：目标目录无 running/submitted 活跃作业，且最近 fetch（complete_time）已落盘
3. **改参数**：sed/编辑 INCAR（此时 fetch 已恢复完毕，不会覆盖）
4. **验证快照**：等 ~20-60s 让 daemon 处理，`grep -H "^<TAG>" <dir>/INCAR <dir>/INCAR.tuned`——INCAR.tuned 反映新参数才确认生效（tuned 是执行版）
5. 提交后再次核对 `INCAR.tuned` 内容 = 期望参数

## 判据

- **收敛判定只用 OUTCAR `TOTAL-FORCE` 块真实 max|f|**；slurm log（agent.db slurm_output 字段）的 `BRION: g(F)` 是外推估计，与实际力可差 50 倍（#136）
- `BRIONS problems: POTIM should be increased` = IBRION=1 时 POTIM 过小（默认 0.5）
- IBRION=3 是阻尼 MD 不是优化器，振荡缺陷升级路径只到 IBRION=1

## 相关

- 大胞相（>25Å）→ `cpd_excluded_phases.yaml` 排除，勿放开 jobs.py MAX_LATTICE 守卫
- 改 vasp_sop 源码后必须重启 loop（`kill -9 $(pgrep -f "vasp-sop batch run")` + `systemctl --user reset-failed vasp-sop-loop && start`）
