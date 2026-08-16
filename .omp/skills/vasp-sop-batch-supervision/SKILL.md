---
name: vasp-sop-batch-supervision
description: "以 30 分钟间隔监督 vasp-sop 2026 批次推进：跑 scripts/batch-watch.py（hub 常驻循环），解读快照/ALERT，按\"有价值问题先提 GitHub issue 再改\"流程处理。用户要求定时监督计算进度或\"提 issue 再改\"时使用。"
---

# vasp-sop 批次监督（30 分钟循环 + issue-first）

**触发**：用户要求定时监督计算推进、30 分钟间隔查看进度、或"有价值的问题提 issue 再改"。

## 机制

- **监控脚本**：`/home/duguex/vasp_sop/scripts/batch-watch.py`（vasp-sop repo 内）——每轮输出 batch status 全表 + crisp 队列（1h 窗口）+ 新失败分类（30min 窗口）+ loop 存活，追加 `~/.vasp_sop/batch_watch.log`，异常标 `ALERT`。
- **常驻循环**（hub）：`hub start` 名 `batch-watch`，`bash -c "while true; do .venv/bin/python scripts/batch-watch.py; sleep 1800; done"`，detached + restart on-failure。查日志 `hub logs batch-watch` 或直接读 log 文件。
- **手动跑**：`.venv/bin/python scripts/batch-watch.py`（cwd=/home/duguex/vasp_sop）。

## 解读要点

1. **时间戳陷阱**：crisp agent.db 的 submit_time 是 **ISO 文本**（'2026-08-10T23:12:27Z'）——epoch 数字过滤静默失效（统计到全量历史）。必须 `julianday(submit_time) > julianday('now','-30 minutes')`。vasp-sop JobStore 的 timestamp 是 epoch float。
2. **failed 计数**：crisp 的 failed 是历史累计（数百个正常）——只看 30min/1h 窗口内的**新** failed。
3. **ALERT 判定**：新失败 > 0 且错误同型（如 EXIT_CODE 1 成批）= 参数/输入问题（查本地 OUTCAR 尾部）；单个零散失败 = 正常瞬态。
4. **进度真源**：`vasp-sop batch status <root> --human` 的相位表（CPD/UC/Defect D/T + Run）——每 30 分钟对比一次快照即可。

## 问题处置（issue-first）

- 发现**有价值/可复现**问题 → `gh issue create --repo duguex/pydefect-workflow-sop --label "bug,P1/P2"`（label 用 bug/enhancement/P0-P2，repo 无 triage 标签）。
- **只提不改**，等用户确认后再改代码——除非用户明确授权。
- issue body 含：现象 + 证据（具体目录/日志/mtime）+ 根因链 + 临时修复（如有）+ 机制级建议。

## 已知盲区（诊断时先查）

- 漂移扫描（INCAR>OUTCAR）：60s 容差漏检同秒批次；无 OUTCAR 的未跑目录完全不在清单（协议变更后旧 INCAR 残留 SOC → ZBRENT 批量崩，见 issue #115）。
- libs/vise fork U 表缺 Ti（生产 .venv）——set_hubbard_u 对 Ti 静默无 LDAU。
- 磁性体系 LWAVE=False 无 WAVECAR → 磁态逐轮漂移（MAGMOM 锁定已修 Fe，见 issue #116）。
