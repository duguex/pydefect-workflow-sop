---
name: vasp-sop-git-geometry-baseline-selection
description: "从 git 快照恢复 vasp-sop 缺陷几何时，用三明治判定选 BASELINE（收敛后覆盖前）而非 LATEST，避免 LATEST CONTCAR 被 SP/未收敛作业污染导致重跑永不收敛。用于\"恢复哪个快照/为什么跑不收敛/几何恢复\"类问题。"
---

# vasp-sop git 快照几何恢复：BASELINE 选择（三明治判定）

## 何时用

- 从体系 git 仓库恢复 defect 目录几何（POSCAR ← 某 commit 的 CONTCAR）
- 重跑的 stage1/stage2 长时间不收敛（NELM 耗尽循环、100 离子步能量只推进 0.1 eV）
- 判断"哪个快照存的是收敛几何"；目录 CONTCAR 被覆盖（NSW=0 SP 写回输入几何、输入再生成、fetch 覆盖）需要收敛几何重算
- backlog（欠账）目录重跑前确认恢复源收敛

## 快照语义（2026 批次，2026-08-13 观测）

| 快照 | 时刻 | 几何质量 |
|---|---|---|
| `baseline` (input + result snapshot) | 01:16–01:28 | **收敛后-覆盖前 — 首选** |
| `manual snapshot`（如 2026-08-12 03:16） | 03:16 | 不可靠：可能存未收敛作业输出（如 03:10 log acc=0）；与 baseline 不同 = 期间有作业改写，默认按污染处理 |
| `cycle snapshot`（LATEST，如 dad5dc2/ec403a1/b7f32e9） | SP 后 06:22/09:07 | **不可靠**：LATEST = SP 污染/恢复几何 |
| `HEAD` | — | 几何恢复永远别用 |

## 核心陷阱（2026-08 实证，issue pydefect-workflow-sop#138）

**LATEST（cycle 快照，SP 后）的 CONTCAR 可能被污染**：SP 作业（NSW=0）提交时 CONTCAR→POSCAR，输出 CONTCAR=输入副本 → 快照保存的是**中间几何**，不是收敛几何。用错后从中间几何重跑 + 难体系 = 永不收敛（3+ 小时，100 步差 0.1 eV；2026-08-12 Batch B 事故 5 目录 3h）。

**BASELINE 快照（如 1adab58/6ee215a）常保存"收敛后、覆盖前"的干净几何**——恢复后 4 分钟 1 步收敛，能量精确命中旧收敛值（差 5e-6 eV）。

## 三明治判定法（每个目录独立做）

1. 找该目录收敛 log：`grep -l "reached required accuracy" defect/<dir>/*.log`，记 mtime
2. 找 git 快照时间线：`git log --format="%h %ci" --all`
3. 判定：**收敛 log 时间 < 快照时间 < 覆盖作业时间** → 该快照 CONTCAR = 收敛几何
4. **必做两项预检**（信任任何快照前）：
   - 收敛 log mtime 早于 baseline 快照时刻；且收敛与该 baseline 之间**无未收敛（acc=0）作业**
   - 指纹：`git show <commit>:defect/<dir>/CONTCAR | md5sum` 逐 commit 比对候选（`git log --all --format=%h %ci %s` 拿清单）
5. **反例必须查**：若收敛 log 晚于 BASELINE（如 Va_Y5_0 收敛 05:48 > BASELINE 01:28；La2SrSc2O7:1adab58 场景），正确几何反而是 LATEST——一刀切必错。**快照错过**（stage1 收敛在快照后，如 Va_O4_2 07:41 才收敛）：用最近 cycle snapshot（=SP 输入，最近可用几何）重跑 stage1 到收敛再接 stage2

## 恢复与重跑流程

```bash
git -C <sys> show <commit>:defect/<dir>/CONTCAR > defect/<dir>/POSCAR
# 批量提取另法：git -C <sys> archive <commit> | tar -x -C /tmp/gitsnap/（跨设备用 copy 不用 os.replace）
```

- 快照 commit（SOC SP 波次前的 03:16 manual snapshot）：Gd2GaSbO7:Bi=74575fc, La2SrSc2O7=8b6787d, La2Zr2O7=f36193e, Y2Sn2O7=5204cb6, Y2Ti2O7=c7ae2d9
- INCAR 补 `NELM = 100` + `EDIFF = 1e-5`（防 NELM=30 电子步耗尽；症状：每离子步 DAV 恰好 30 步、F= 步进极小、磁矩恒定）；ZBRENT 失败（"I REFUSE TO CONTINUE"）则 EDIFF 放宽回 1e-4；适配模式：stage1 重跑用 baseline 版 INCAR + ISTART=0，直接接 stage2 用 SP/弛豫参数
- 清结果：`rm -f OUTCAR CONTCAR CHGCAR WAVECAR vasprun.xml OSZICAR INCAR.tuned .failed .timeout`
- **POSCAR.bak 陷阱**：crisp submit 的 vasp-cache 身份恢复会从 `POSCAR.bak` 悄悄覆盖修正后的 POSCAR（实测 Sb_Gd1_0/Sb_Gd2_-1 两次回退 manual 几何）——提交前必须删 `POSCAR.bak`，提交返回后**再核对 md5(POSCAR)==md5(baseline CONTCAR)**
- 提交：`crisp submit duguex_5 --skip-prefill`（**用 205.5 免费长时限；禁付费分区 CPU-64C256GB**；113/101 的 test 分区 20 分钟 TIME-LIMIT 会杀慢缺陷；crisp 自动评分可能丢回 test，必须显式指定）
- 验证恢复的几何：与 defect_entry.json 的 structure 位移（弛豫总量 ~1-2 Å 合理）、与 SP 后 CONTCAR 位移（>0.3 Å 说明快照是更早的收敛几何）
- 同一目录严禁并发提交（fetch 竞态覆盖结果）

## 提交后验证清单

- 弱 SOC/无磁体系应 1-4 离子步收敛；能量 vs 旧收敛 log 最后 F=（应差 <1e-4 eV，指标命中 <1e-3）
- `grep "reached required accuracy" <dir>/*.log` 至少一次
- 数值比较用坐标级归一化（fetch 回的 POSCAR 是 crisp 规范化格式，md5 字符串不匹配是假阳性），周期性等价用 pymatgen StructureMatcher
- 两阶段流程：stage1（无 SOC，LSORBIT off，NELM=100/EDIFF=1e-5/ISTART=0）→ stage2（cp CONTCAR→POSCAR + LSORBIT=.TRUE. + ISYM=-1，NSW 保持 100）→ 全收敛后删除各目录 stale calc_results.json + 体系 defect_energy_summary.json/calc_summary.json，重跑 `vasp-sop defect analyze <sys>`（旧 summary 挡重提取）

## 完成判定（勿用 CRISP_COMPLETED）

`CRISP_COMPLETED` 只存在于 vasp-sop 的 submit.slurm；`crisp submit` 作业不写。权威口径：本地 OUTCAR `reached required accuracy` + mtime + agent.db status。

## 相关

- issue #138（pydefect-workflow-sop）：快照污染陷阱
- 根因：`orchestrator.py::_submit_stage2_soc` 旧版非 Bi 分支不复制 CONTCAR→POSCAR（2026-08-12 修复，ADR 0022）
- 脚本参考：/tmp/audit_sp_hit.py（SP 命中判定）、/tmp/batch_a_submit.py、/tmp/batch_b_submit.py（批量恢复+提交模式）