---
name: vasp-sop-rerun-closure-audit
description: "审计 vasp-sop 重算批次是否真的全部收敛（权威判据 OUTCAR reached required accuracy，非 CRISP_COMPLETED）、扫描全部欠账（有 calc_results 但未收敛）、验证 git 几何三明治、重提交配方（duguex_5 + NELM=100 + 预期 1 步收敛）。用 于\"都算完了吗/可以更新结果了吗/还有没算的吗\"类问题，或 SOC 重算批次收尾。"
---

# vasp-sop 重算批次收尾审计协议

用于判定一批重算（如 2026 SOC 两阶段 ADR 0022 重算）是否真正全收敛、找齐欠账、验证几何、重提交收尾。源自 2026-08-12/13 会话（多次判据误报后成型的配方）。

## 1. 权威完成判据（最重要）

**OUTCAR 尾部含 `reached required accuracy` = 收敛**。读取方式（OUTCAR 巨大，勿全读）：

```python
with open(oc, "rb") as f:
    f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz - 300000))
    t = f.read().decode(errors="ignore")
conv = "reached required accuracy" in t
```

**禁止用以下判据**（全部踩过坑）：
- `CRISP_COMPLETED`/log 标记 —— **ZBRENT 失败后 fetch 也写 completed**（如 Sr_Sc1_1、Ti_Y4_-1 曾漏判；VASP 离子步 ZBRENT 括号失败 "I REFUSE TO CONTINUE...BYE" 但 slurm 脚本 exit 0 → CRISP_COMPLETED → fetch completed → agent.db completed，而 OUTCAR 未收敛）
- agent.db status=completed —— 记录可能丢失（daemon 重启后 6+ 目录无记录）、可能 cancelled/failed 静默；**记录丢失假象**：目录显示 cancelled/failed/无记录但新作业实际收敛（OUTCAR mtime 在新批次后）——勿据此判欠账；反向也成立：running 但无 OUTCAR 是正常进行中
- mtime "近期有活动" —— fetch 快照时间不等于计算状态
- 磁盘 INCAR 的 NSW —— 与执行版（INCAR.tuned/OUTCAR 回显）可能不符（如磁盘写 100、实际执行 NSW=0 单点 = 旧 SP 结果残留）

## 2. 欠账全量扫描

欠账判定三件套（代码 + OUTCAR + mtime 窗口，防旧 OUTCAR 冒充）：
```
欠账 = is_valid_defect_dir(dd) == True
   且 NOT（OUTCAR ≥50KB 且含 "reached required accuracy" 且 mtime > 新批次起始时间）
```

```python
# 快速版：对全部体系——有 calc_results.json（=结果集成员）但 OUTCAR 未收敛 = 欠账
has_cr = os.path.isfile(f"{dd}/calc_results.json")
if has_cr and not conv: owe.append(...)
```

- **`is_valid_defect_dir` 在 `vasp_sop/defect/__init__.py`（Name_Charge + defect_entry.json + 排除阴-阳反位）——用代码判定，勿凭记忆分类**：metal↔metal 替位（如 Gd_Sb1/Sb_Ga1）是有效结果集，Sb 按阳离子；只有 O_Ga1/Ti_O1/Bi_O1 类阴离子-阳离子反位被 ADR 0013 排除——它们有旧 calc_results 但不是欠账（analyze 时需排除）
- **mtime 必须 > 新批次起始**——否则旧 stage1/SP 收敛 OUTCAR 冒充完成（旧 OUTCAR 冒充：新作业 failed/cancelled 没覆盖，旧收敛 OUTCAR 残留 → 判据误判完成，加 mtime 维度）
- 10 体系扫描一次 ~3 分钟，可全量做，不要只扫 SOC 体系或"mtime 窗口"

## 3. git 几何验证（重提交前必做）

快照时间线三明治判定：**收敛 log 时间 < 快照时间 < 覆盖作业时间**。

- `git log --all --format="%h %ci %s" -- defect/<dir>` 找该目录历史
- `git show <commit>:defect/<dir>/CONTCAR` 提取几何
- **恢复用 BASELINE/manual 快照，不要用 LATEST**（LATEST CONTCAR 可能被 SP/未收敛作业覆盖成中间几何——issue #138）
- 例外：若收敛 log 晚于 BASELINE，则 LATEST 才是正确几何（按目录判定，勿一刀切）
- POSCAR 比较**必须数值归一化**（crisp fetch 规范化短小数 vs git VASP 16 位小数，字符串 md5 全是假阳性）；周期等价用 pymatgen StructureMatcher
- git 快照**不存 OUTCAR/POTCAR**（log/slurm/INCAR.tuned/POSCAR.bak 有）
- 交叉验证：log 文件在快照里（`git show <commit>:defect/<d>/xxx.log`）——查收敛 log 时间线

## 4. 重提交配方

```bash
# 每目录：INCAR 修 NELM=100 + EDIFF=1e-5（NELM=30 电子步耗尽=弛豫永不收敛根因）
# 删 OUTCAR/OSZICAR/CONTCAR/CHGCAR/WAVECAR/vasprun.xml/INCAR.tuned/.failed/.timeout（防 vasp-cache 旧结果混淆）
cd <dir> && crisp submit duguex_5 --skip-prefill
```

- **集群：duguex_5（114.214.205.5）**——免费、长时限；113/101 的 test 分区 20 分钟硬限制会 TIME-LIMIT 杀慢缺陷
- 付费分区（CPU-64C256GB 等）禁用
- **预期 1 步收敛**：从 stage1 收敛几何 + 弱 SOC 扰动 → 第一步即满足 EDIFFG。**第一步能量命中 = 几何正确实锤**（对比同几何 SP 单点能量，差 = SOC 效应 ~0.5-1 eV）
- 难收敛目录特征：历史 log 有 ZBRENT 括号失败（`I REFUSE TO CONTINUE...BYE`）→ 该格位固有难收敛（如 Y2Ti2O7 Va_Y5_0 3h+），重试用 EDIFF=1e-4

## 5. 提交清单纪律

全量提交前清单 = **曾计算的目录**（audit_sp_hit + perfect），**不是"有 POSCAR 的目录"**——后者会把 200+ 反位排除目录一起提交，需批量取消（取消会误杀真结果集目录，211 个教训）。

## 6. 交叉验证（agent.db ↔ OUTCAR 回显）

- agent.db 批次窗口（如 21:40 批次，submit_time 13:40-14:05Z）failed/cancelled 记录 → 漏网候选
- OUTCAR 回显 vs INCAR/INCAR.tuned：执行 NSW=0（单点）但磁盘写 100 = 旧 SP 结果残留
- 可复算口径示例（2026 批次 SOC 体系）：未收敛 24 = 4 阴离子反位（忽略）+ 8 metal↔metal（补算）+ 12 空位/替位（补算）；输出每目录的 is_valid/收敛/mtime/记录四字段表

## 7. 做差可比性（交付口径）

新旧能量 ΔE 是几何修正 + SOC + 参数修复（NELM/EDIFF）混合，**仅定性**。做差前验证：
- NELECT 跨代一致（不一致=电荷差异混入，做差失效）
- KPOINTS 同体系一致；核心参数（ENCUT/PREC/ISMEAR/SIGMA/ISPIN）一致
- 形成能修正：E_f_new = E_f_old + ΔE_def − ΔE_perfect（化学势项跨代不变）