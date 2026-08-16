---
name: vasp-sop-soc-stage2-geometry-audit
description: "Audit vasp-sop two-phase SOC supplement geometry basis: the SOC single-point leg never copies CONTCAR→POSCAR, so it runs on stale POSCAR geometry; recover lost stage-1 CONTCARs from per-system git snapshots. Use when checking why SOC single points look wrong, whether stage-1 relaxed geometries were used, or how to restore overwritten CONTCARs."
---

# vasp-sop 两阶段 SOC 几何基础审计（含 git 恢复）

## 背景（ADR 0014）

两阶段 SOC：stage1 无 SOC 弛豫（NSW=100）→ stage2 SOC 单点（NSW=0）。stage2 能量进入形成能。诊断为只读操作；重算需先修 orchestrator + 停 loop。

## 核心缺陷（2026-08-12 实锤）

`vasp_sop/core/orchestrator.py::_submit_stage2_soc`：
- **非 Bi 缺陷**：只 `patch_incar(NSW=0)`，**不复制 CONTCAR→POSCAR** → SP 跑在"提交时已有 POSCAR"上
- **Bi 缺陷**（名字以 `Bi_` 开头）：复制 CONTCAR→POSCAR，并保持 NSW=100（SOC 弛豫，另一套协议）
- SP（NSW=0）跑完后 VASP 把 CONTCAR 写成 SP 输入几何 → **stage1 收敛结构被覆盖**
- 已修（2026-08-12，回归 560 tests pass；ADR 0022 统一为 SOC 弛豫 NSW=100、不区分 Bi）；运行中 loop 若持旧代码，重算前先重启

影响规模：171/292（或 171/306）SP 目录几何偏移 0.05–7.7 Å，能量偏差最高 6.9 eV（Y2Ti2O7 perfect 高估 1.98 eV）；E_perfect 偏差可达 ~2 eV。

## 命中判定（磁盘证据，不用跑 VASP）

对每个 SP 目录：
1. **识别 SP 目录**：最终 log 只有 1 帧 F=，或 OUTCAR 回显 `NSW = 0`（OUTCAR 头部，跳过 DEFAULTS 段、取最后一次出现）
2. 取倒数第二个 %j.log（stage1 最后一段 run），首末帧 F= 能量差 = SP 输入几何误差
   - 误差 <0.05 eV 可忽略（>0.2 eV 需重算）；Y2Ti2O7 306 个 SP 目录实测：49% <0.05，16% >1.0（La2SrSc2O7/Va_La1_-3 达 6.9 eV）
   - st1 若只有 1 帧（单点链）→ 另找更早的弛豫 log
3. SP 能量 vs stage1 收敛终点：**SP 比弛豫终点高 = 几何错**（SOC 单点变分上不可能高于弛豫终点）
   - Y2Ti2O7/perfect 实锤：SP -755.483 vs 弛豫终点 -756.693，高 1.21 eV
4. 交叉验证 perfect：E_perfect vs N×unitcell 每 f.u. 能量差 ≤0.3 eV 正常；或 pymatgen StructureMatcher 对比 perfect POSCAR vs unitcell/structure_opt/CONTCAR（fit=True = SP 输入正确）
5. 注意反例：La2Zr2O7/Y2Sn2O7 的 perfect SP 输入经 vasp-cache 结构预填充为正确结构，SP 与独立参照（4×unitcell）吻合到 0.01 eV → 无几何问题。**必须逐目录核对，不能按体系一刀切**

## 结构对比陷阱

缺陷 vs perfect 结构对比**禁用索引对齐**（构建/再生成会重排序 → 假 5-8 Å 位移）；用 species-aware 最近邻匹配（PBC 最小像）。真实反位弛豫 ≤~2 Å。

## stage1 收敛几何恢复（git 快照）

- 2026 批次全部体系目录自身是 git 仓库（git_snapshot.py 机制，提交信息 "cycle snapshot"/"manual snapshot"；ADR 0019）
- 关键：SOC SP 波次**之前**的 manual snapshot（2026-08-12 03:16，如 Y2Ti2O7/c7ae2d9、La2Zr2O7/f36193e、Gd2GaSbO7:Bi/74575fc、La2SrSc2O7/8b6787d、Y2Sn2O7/5204cb6）存有全部目录 CONTCAR（不含 OUTCAR）
- 恢复：`git -C <体系目录> show <commit>:defect/<dir>/CONTCAR > 目标文件`（写 /tmp 勿写批次树；批量用 `git archive <commit> | tar -x -C /tmp/gitsnap/`）
- **陷阱**：SP 之后的 cycle snapshot 存的是 SP 后几何（=SP 输入），不可用于恢复；判断快照归属用 `git log --format="%h %ad %s" --date=format:"%m-%d %H:%M"` 对比 SP 提交时刻（perfect 目录最新 log mtime 或 job_history 的 soc_stage2 记录）
- **快照错过判定**：该目录 stage1 最后 run log 的 mtime > 快照时刻 → 快照存的是旧几何（可能是被丢弃的错 NELECT run），需重跑 stage1（Va_O4_2 案例：用废几何重跑 stage1 继续下降 35 步，换 ec403a1=SP 后快照=最近可用几何后 46 步收敛回旧终点）
- 验证恢复的几何：与 defect_entry.json 的 structure 位移（弛豫总量 ~1-2 Å 合理）、与 SP 后 CONTCAR 位移（>0.3 Å 说明快照是更早的收敛几何）
- `crisp cache.db` 全空 = 无其他恢复通道（无远端副本，JobStore 无几何）

## 重算建议顺序

1. **先停 loop**（`systemctl --user stop vasp-sop-loop`；SIGTERM 挂起 → `kill -9 $(pgrep -f "vasp-sop batch run")` + `reset-failed`）——否则 loop 会改写文件/重提 SP
2. 先修 `_submit_stage2_soc`：非 Bi 分支也复制 CONTCAR→POSCAR（与 Bi 一致）
3. **Batch A（快照捕获收敛）**：快照 CONTCAR → POSCAR（`shutil.copyfile`；/tmp 与 /mnt 跨设备 os.replace 会失败）→ INCAR 确保 NSW=0 + LSORBIT=True + ISYM=-1 → `crisp submit --skip-prefill` 逐目录（脚本 /tmp/batch_a_submit.py，用 /tmp/gitsnap/）
4. **Batch B（快照错过）**：用 SP 后最近快照几何 → stage1 INCAR（baseline 提交版 + ISTART=0）→ 提交 stage1 → 收敛后接 stage2（脚本 /tmp/batch_b_submit.py，plan /tmp/batch_b_plan.json）
5. 重算后复核：SP 能量应 ≤ stage1 收敛终点（SOC 变分）

## 防误提交（清单纪律）

- 清单必须用"曾计算目录"（audit_sp_hit + perfect），**勿用"有 POSCAR 的目录"**——会把 ADR 0013 排除反位目录（Ti_O*/O_Ti* 等 ~250 个）也提交（实测误提交 211 个：182 取消 + 29 已跑完）

## 关键判据

- 单点/SOC 弛豫能量：log 最后 `F=` 行（OUTCAR 尾部被 fetch 截断不可靠）
- 执行参数：OUTCAR 头部回显（LAST occurrence，跳过带描述的多 token 行；描述行如 "IBRION = -1 ionic relax:..." 是默认段，非执行值）
- INCAR.tuned == OUTCAR 回显 = crisp 执行链正常；磁盘 INCAR 可能陈旧（再生成未重跑；loop 曾 19:58 重提 perfect NSW=100——提交前逐目录复查 INCAR）

## 输出

每目录表：dir、SP 能量、stage1 首末帧、几何误差、git 恢复 commit；分级清单（ok/<0.05、需重算 >0.2）。状态文件模式参照 /tmp/soc_status_20260812.md（优先级：活跃 crisp 作业 > log CRISP_COMPLETED > 计算中 > 无log）。

## 残余深负说明

几何修复后残余深负 E_diff（Y2Ti2O7/La2Zr2O7 ~−7 eV 反位）仍存在 = 真实计算输出，不是几何 bug；U=4.0 施加在 Ti(d0) 的嫌疑未解（另行对照实验）。