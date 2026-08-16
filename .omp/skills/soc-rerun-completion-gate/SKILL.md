---
name: soc-rerun-completion-gate
description: "审计 vasp-sop SOC 重算批次是否真正全算完，再决定能否更新结果/analyze：活跃作业清零检查、OUTCAR 尾部收敛扫描（勿全文件读）、按 calc_results 有无分类未收敛目录（反位排除 vs 真欠账）、重提交前用 git 三明治判定验证 POSCAR 几何来源、欠账统一提交 duguex_5（NELM=100/EDIFF=1e-4）。用于\"都算完了吗/可以更新结果了吗\"类问题。"
---

# SOC 重算完成度门（analyze 前必跑）

## 何时用
用户问"都算完了吗 / 可以更新结果了吗"，或任何 SOC 重算批次收尾前。**未过此门不得跑 analyze/更新 calc_results**（旧结果/失败残留会污染形成能）。

## 1. 活跃作业清零
```bash
crisp jobs  # 活跃 = submit/submitted/running/ready_fetch，应为 0
```

## 2. OUTCAR 收敛扫描（尾部读取，勿全文件）
OUTCAR 可达 70MB+，全文件读会超时。只读尾部 ~300-400KB：
```python
pat = re.compile(r"free  energy\s+TOTEN\s*=\s*(-?\d+\.\d+)")
with open(oc, "rb") as f:
    f.seek(0, 2); sz = f.tell(); f.seek(max(0, sz-300000))
    t = f.read().decode(errors="ignore")
conv = "reached required accuracy" in t
n = len(pat.findall(t))  # TOTEN 数 = 离子步数
```
注意：**OUTCAR 的 "reached required accuracy" 才可信**；log 里的 CRISP_COMPLETED 只表示 VASP exit 0（NELM 耗尽/100 步跑满也 exit 0）；agent.db completed 只是 fetch 完成（crisp 提交的作业 OUTCAR 里无 CRISP_COMPLETED 标记）。

## 3. 未收敛目录分类（防误判欠账）
```python
has_cr = os.path.isfile(f"{dd}/calc_results.json")
```
- **有 calc_results 且目录是空位/替位（Va_*/X_Y）→ 真欠账**（需重提交）
- 有 calc_results 但目录是反位（Ga_Sb/Gd_Sb/Sb_*/O_Ga 等阳离子反位，ADR 0013 排除）→ **不算欠账**（残留旧 analyze 产物干扰，忽略）
- 无 calc_results → 非结果集（排除目录/误提交取消残留），忽略

## 4. 重提交前验证 POSCAR 几何来源（git 三明治判定）
**LATEST/cycle 快照的 CONTCAR 会被后续 SP(NSW=0)/未收敛作业覆盖污染**（issue pydefect-workflow-sop#138）。用：
1. `git log --all --format="%h %ci %s" -- defect/<dir>/CONTCAR` 看快照时间线
2. 找该目录**唯一收敛的 log**（`grep "reached required accuracy"` 各 `*.log`，注意 log mtime = fetch 时刻）
3. 判定：收敛 log 时间 < 快照时间 < 覆盖作业时间 → 该快照 CONTCAR = 收敛几何
4. `git show <commit>:defect/<dir>/CONTCAR` 恢复为 POSCAR（数值归一化比较，勿用 md5——fetch 回的 POSCAR 是 crisp 规范化格式，同几何不同字节）
5. 也可用第一步能量验证：从收敛几何起步的 SOC 弛豫第一步应 ≈ 收敛能量 + SOC 效应（~-0.5~-1 eV）；若高 1+ eV → 几何污染

## 5. 欠账重提交（duguex_5，NELM=100）
```bash
# 清理旧结果（防 vasp-cache 混淆）
rm -f OUTCAR OSZICAR CONTCAR CHGCAR WAVECAR vasprun.xml INCAR.tuned .failed .timeout
# INCAR: NSW=100 + LSORBIT=True + ISYM=-1 + NELM=100 + EDIFF=1e-4（1e-5 曾触发 ZBRENT 失败）
crisp submit duguex_5 --skip-prefill
```
- **不要用 CPU-64C256GB 等付费分区**（113/101 的 test 分区仅 20 分钟限制，慢缺陷必 TIME-LIMIT；duguex_5=205.5 免费长时限）
- 从收敛几何起步的弱 SOC 体系预期 1 步收敛（~10 分钟）；难体系（Y5 格位、Ti 空位）1-3h 属正常

## 6. 完成后再过一遍 1-3，全绿才能 analyze/更新结果
