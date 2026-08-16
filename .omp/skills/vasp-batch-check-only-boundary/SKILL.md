---
name: vasp-batch-check-only-boundary
description: "在 vasp_sop/2026 批次（或任意计算树）上做检查类工作时遵守用户划界：只读检查、绝不提交计算或修改计算目录（POTCAR/INCAR/OUTCAR/标记），发现→报告→备份→等授权。用于用户说\"检查/审计/看看\"而 agent 想直接修复或提交时。"
---

# VASP 批次检查-只读边界纪律

## 何时用
用户要求"检查/审计/看看/先想想要检查什么"批次计算时。2026-08-15 用户明确：**agent 不被允许提交计算，任务是检查**。

## 边界（红线）
- ✅ 允许：读 OUTCAR/POTCAR/INCAR/POSCAR（头部/尾部）、写报告文件（/tmp JSON、终端输出）、备份（cp 到 /tmp）、起草修复方案（文本）
- ❌ 禁止（必须用户明确授权）：`crisp submit`、删/改计算目录文件（POTCAR/INCAR/OUTCAR/OSZICAR/vasprun.xml/.failed 标记）、`systemctl stop/start vasp-sop-loop`、`crisp cancel`
- 撤销自己的越界操作也算写操作，需谨慎：cancel 已提交作业是撤销越界（应做）；还原 POTCAR 属于再次写计算目录（先报告等指示）

## 发现问题的正确姿势
1. 报告：问题清单（目录+证据）+ 影响评估（下游产物/量级）
2. 备份：把要动的文件先 cp 到 /tmp（如 /tmp/potcar_backup_YYYYMMDD/）
3. 方案：给出修复步骤/命令模板，等用户拍板
4. 用户授权后再执行写操作；执行时逐步验证（提交前清 OUTCAR 防并发污染、绝对路径、verbatim POTCAR 段格式）

## 检查工具（已交付）
- `~/vasp_sop/scripts/check_results.py`（commit a6ce769）：D1-D5 五维 + --compare 报告对比；`--json` 输出；systemd 每日 timer `vasp-sop-check.timer`
- 补充维度（grilling 待确认）：D6 INCAR 执行一致性（OUTCAR 回显 vs 盘面 + mtime）、D7 NELECT、D8 形成能合理性

## 已知判据（grilling 中，以用户确认后为准）
- OUTCAR 回显 = 执行真相；收敛 = `reached required accuracy` + 豁免（非自洽 band/dos/dielectric、energy-flat 最后两步 TOTEN 差 <1e-3 eV）
- 能量离群阈值 8 eV/atom（捕获 TITEL= 解析错位 ~800 eV/atom）
- POTCAR 段格式污染特征：OUTCAR `POTCAR:` 行 vs `TITEL` 行矛盾；盘面段头非 `  PAW_PBE <el> <date>` 行首
