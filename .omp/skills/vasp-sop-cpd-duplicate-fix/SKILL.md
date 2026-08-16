---
name: vasp-sop-cpd-duplicate-fix
description: vasp-sop 批处理报 Duplicate CPD compositions 时：比较相能量、保留低能相、备份移出 cpd 目录、等 loop 重试。含备份目录留在 cpd/ 内被 preflight 误当相目录的坑。
---

# vasp-sop CPD 重复相处置

处理 vasp-sop 批处理报 `Duplicate CPD compositions` 错误（`collect_cpd_phase_provenance` raise ValueError，阻塞系统进入 CPD/后续相位）。

## 触发

loop/批处理日志出现：
```
ERROR <System> CPD failed: Duplicate CPD compositions: <Formula>: <A_mp-xxx>, <B_mp-yyy>; ...
```

含义：`cpd/` 目录下两个相目录的简化化学式相同（MP 同组成多条目），CPD 凸包无法选择，代码防御性拒绝。

## 处置步骤

1. **确认两个相都算完**：`ls <sys>/cpd/<A_mp-xxx>/OUTCAR`，无在飞作业（`crisp jobs` 查 local_dir）。
2. **比较能量**（更负=更稳定，保留它）：
   ```bash
   grep "free  energy   TOTEN" <sys>/cpd/<A_mp-xxx>/OUTCAR | tail -1
   grep "free  energy   TOTEN" <sys>/cpd/<B_mp-yyy>/OUTCAR | tail -1
   ```
   保留 E 更低者。能量差很小时（<1 meV/atom）按数值噪声处理，仍保留低者。
3. **备份+移除高能者**：
   ```bash
   mkdir -p <sys>/.dup_bak_$(date +%Y%m%d)
   mv <sys>/cpd/<高能目录> <sys>/.dup_bak_$(date +%Y%m%d)/
   ```
   ⚠️ **备份必须放 cpd/ 之外**（系统根下即可）。备份留在 cpd/ 内会被 `cpd_preflight` 当相目录（含 OUTCAR/CONTCAR 的子目录都算），报 `CPD mce preflight failed: .dup_bak: OUTCAR, CONTCAR`。
4. **等下一轮 loop** 自动重试 CPD（~2-4 min）。成功后系统进入下一相位（如 UNITCELL_DEFECT），验证 `target_vertices.yaml`/`chem_pot_diag.json` 生成。

## 不做的事

- 不用 `cpd_excluded_phases.yaml` 排除重复相（排除是范围决策，不是失败桶——CONTEXT.md 明确禁止）。
- 不自动选相：能量比较后若仍不确定，交给用户裁决。

## 相关

- 类似毒化问题：vasp-cache 崩溃条目（converged_ionic IS NULL）会让重试无限 vasp_crash——purge `entries WHERE converged_ionic IS NULL`（/mnt/shared/vasp_cache/index.sqlite）。
- CsPbBr3（2025 根）同款错误历史：BiBr3_mp-2913080 vs mp-752602，已清理。
