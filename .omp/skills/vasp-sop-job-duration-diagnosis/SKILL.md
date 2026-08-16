---
name: vasp-sop-job-duration-diagnosis
description: "诊断 vasp-sop/crisp 作业为何耗时（小时级）：拆解 submit→start 排队 vs 实际计算（%j.log mtime vs agent.db submit_time、OUTCAR Elapsed time、VASP4 POSCAR 解析、OSZICAR 缺失陷阱、SOC 每步成本）。用户问\"为什么这么慢/单算多久\"时使用。"
---

# 作业时长归因（队列 vs 计算）

用户问"为什么单个计算要小时级"时，先拆解再回答——多数情况**排队 > 计算**。

## 三成分拆解

1. **排队时长** = 作业实际起算 − 提交时刻
   - 起算时刻：目录下最新 `%j.log` 的 mtime（VASP 首个 log 文件创建时间）
   - 提交时刻：agent.db `jobs.submit_time`（UTC）
   - 实测参考：2026-08 队列深时 submit→start ~3h（QOS 派发瓶颈）
2. **计算时长** = OUTCAR 尾部 `Elapsed time (sec)`（VASP 自己的计时）
   - 快收敛的小缺陷 1-2 分钟；SOC/难收敛 + NSW=100 跑满才到小时级
3. **规模/成本**：POSCAR 是 **VASP4 格式**（首行 `Gd16 Ga7 Sb9 O56` 含元素+原子数；标准第 7 行 counts 解析会失败返回 None）

## 陷阱

- **OSZICAR 本地不存在**：crisp 只回拉 OUTCAR/vasprun.xml/CONTCAR——离子步数只能 `grep -c '^\s*\d+ F=' OUTCAR`，读 OSZICAR 得 0
- SOC 体系（~88 原子）约 **9min/离子步**（64 核），属物理成本
- 播种（ADR 0010）省离子步（40 vs 48.3，~17%）**不省每步墙钟**——+U/SOC 重算后播种收益打折（电子结构变化大）

## 回答模板

给出：排队 Xh（log mtime vs submit_time）+ 计算 Ymin（Elapsed time）+ 原子数/是否 SOC/离子步数——然后明确结论（如"小时级 = 排队 3h + SOC 物理时长，播种在设计内生效"）。
