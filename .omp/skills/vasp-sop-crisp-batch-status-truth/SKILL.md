---
name: vasp-sop-crisp-batch-status-truth
description: "判定 crisp 直接提交的 vasp-sop 重算批次（SOC 弛豫/stage1 续跑）的真实完成状态：CRISP_COMPLETED 标记陷阱、权威口径（本地 OUTCAR 收敛+mtime / agent.db）、批次清单只用曾计算目录（排除 ADR 0013 反位）、POSCAR 与 git 快照 md5 验证。用户问\"汇报进度/为什么没跑/结构用对了吗\"时使用。"
---

# crisp 直接提交批次的完成状态判定（vasp-sop 2026 SOC 重算类）

## CRISP_COMPLETED 标记陷阱（本会话 448 假完成实锤）

- `CRISP_COMPLETED` 只写在 **vasp-sop 的 submit.slurm** 输出里；`crisp submit --skip-prefill` 直接提交的作业**不写**该标记
- 因此"最新 log 含 CRISP_COMPLETED"判定会：(a) 把 Batch A SP 的旧 log + 新 INCAR 的目录误判为"新协议完成"；(b) 把已收敛的新作业判成"未完成"
- **权威判定顺序**：
  1. 本地 OUTCAR：`reached required accuracy`（收敛）+ mtime（区分新旧）
  2. crisp jobs 活跃列表（submit/submitted/running/ready_fetch）
  3. agent.db（`~/.crisp/data/agent.db`，jobs 表：status/submit_time/cluster_name）
  4. slurm 侧 squeue 仅作"队列空 ≠ 结果回来"参考（fetch 可能冻结）

## 分类口径（统一后不再漂移）

- 完成 = OUTCAR conv + 无活跃作业
- 中断 = OUTCAR 存在但无 conv（看离子步数，如 ~6 步 = 提交后即断）
- 取消残留 = 误提交被 cancel，log 小（<100KB）+ 无结果文件
- 注意 INCAR LSORBIT 决定 stage1/stage2 身份（统计时先查 LSORBIT 再分类，别只看 log mtime）

## 批次清单构建规则

- 重算/提交清单**只用"曾计算目录"**（audit_sp_hit 的 hit 集 + perfect + pilot + Batch B），**绝不用"有 POSCAR/INCAR 的目录"**
- 教训：full_rollout_soc.py 用"有输入"枚举 → 误提交 211 个 ADR 0013 排除反位目录（Ti_O*/O_Ti*/Ga_O* 等），cancel 182 + 29 已跑完无害但浪费
- 排除目录（is_valid_defect_dir=false）显示为"无 log/未提交"但**不是欠账**

## 结构（几何）验证

- 恢复的 POSCAR 必须 md5 对 git 快照：`git -C <体系> show <commit>:defect/<dir>/CONTCAR | md5sum` vs `md5sum POSCAR`
- 快照层次：manual snapshot（03:16，SP 前收敛几何）> cycle snapshot（LATEST，SP 后=SP 输入几何）> baseline
- Batch B 用 LATEST（stage1 收敛后的 cycle 状态），28/28 全匹配即结构链正确

## agent.db 记录丢失（daemon 重启）

- 22:35 daemon restart 后，21:16-17 提交的作业在集群跑完但 agent.db **无记录**（提交记录丢失）→ 轮询/fetch 不认这些作业，永远"无记录"
- 提交后立即验证：`agent.db` 新记录出现 + `crisp jobs` 可见，否则提交可能丢

## 进度汇报模板

```
SOC弛豫-完成: N（OUTCAR conv 权威）
排队/运行: N（crisp jobs）
stage1-中断待重提: N（OUTCAR interr 清单）
stage1-完成待接stage2: N
无log: N（排除目录，非欠账）
```
