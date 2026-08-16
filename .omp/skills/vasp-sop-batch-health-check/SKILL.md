---
name: vasp-sop-batch-health-check
description: "快速核查 vasp-sop 批处理推进状态：loop 存活、队列泄洪、重复提交、失败原因分类。用于用户问\"现在进度/是不是卡住了/有没有重复提交/loop 在跑吗\"。"
---

# vasp-sop 批处理健康核查

快速回答"进度如何/卡没卡/loop 活着吗/有重复提交吗"。

## 1. Loop 存活

```bash
hub ps | grep -E "batch|vasp-sop"
ps aux | grep -E "vasp-sop batch run" | grep -v grep
```

- `batch-loops-2026`（PID 71160 等）= 2026 根常驻 loop；2025 根历史上**无常驻 loop**（用一次性 run，跑完即退）——若 2025 有排队作业但无进程，属部署缺口。
- hub 受管进程 `restarts=0` 表示死过没拉起。

## 2. 队列泄洪（agent.db）

```bash
sqlite3 ~/.crisp/data/agent.db "SELECT status, COUNT(*) FROM jobs WHERE local_dir LIKE '%2025%' GROUP BY status ORDER BY 2 DESC;"
```

- `submit` 持续减少 + `completed` 增长 = 泄洪正常。**泄漏停机判据**：submit 队列 max(submit_time) 停留数小时不变 = daemon 没在派发（cap 饱和或 auth 抖动）。
- daemon 日志：`~/.crisp/logs/crisp.log`
  - `Global cap saturated (60 >= 60)` = 全局并发上限 `MAX_CONCURRENT_JOBS`（env `CRISP_MAX_JOBS`）占满，设计行为，等完成即可
  - `No cluster available` = 选路失败，常伴随 101/113 `AuthenticationException`（keyboard-interactive 密码+OTP 抖动），恢复后自动泄洪

## 3. 重复提交核查（零输出=无重复）

```bash
sqlite3 ~/.crisp/data/agent.db "
SELECT local_dir, COUNT(*) FROM jobs
WHERE status IN ('submit','submitted','running','ready_fetch')
GROUP BY local_dir HAVING COUNT(*) > 1;"
sqlite3 ~/.crisp/data/agent.db "
SELECT 'active_jobs', COUNT(*) FROM jobs WHERE status IN ('submit','submitted','running','ready_fetch');
SELECT 'unique_dirs', COUNT(DISTINCT local_dir) FROM jobs WHERE status IN ('submit','submitted','running','ready_fetch');"
```

两数相等即无重复。多历史行（failed,failed,completed）是串行重试链，非重复。

## 4. 失败原因分类（persistent vs transient）

```bash
sqlite3 ~/.crisp/data/agent.db "SELECT substr(submit_time,1,19), status, substr(coalesce(error_msg,''),1,120) FROM jobs WHERE local_dir LIKE '%<dir>%' ORDER BY submit_time DESC LIMIT 5;"
# 最新 slurm log 尾部：ZBRENT / I REFUSE = persistent（调参或 terminal）；KILLED BY SIGNAL / TIME LIMIT = transient（重试可救）
```

- `EXIT_CODE: 1` + 一堆 `1` 行 + ZBRENT = 电子不收敛（persistent）
- `EXIT_CODE: 255` + KILLED SIGNAL 9 = 被外部杀（transient，重提大概率成功）
- 同目录数十次同因失败 = 无上限重试在浪费核时（ADR 0008 的分类重试要修的点）

## 5. 快速进度汇总

```bash
crisp status   # 集群负载：usage/running/pending/idle
```

注意 205.5 满载时可能是 2026 根独占，2025 排队干等而 101 集群 1919 核空闲——路由问题（issue #126）。
