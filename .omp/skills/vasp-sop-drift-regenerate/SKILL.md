---
name: vasp-sop-drift-regenerate
description: "批量处理 vasp-sop 2026 批 INCAROUTCAR 漂移目录：重生成输入文件（defect API 路径自动 U+SOC、cpd/unitcell CLI 路径 NELM=50 兜底）+ 从 CONTCAR 续算准备（不提交），含 Sr[FeO2]2 EDIFFG 保护、幂等 TSV 进度、LDAU 覆盖率验证、切体系时重提清单。用户要求更新输入参数或漂移目录处理时使用。"
---

# vasp-sop drift regeneration（漂移目录批量处理）

处理 INCAR mtime > OUTCAR mtime + 60s 的目录（重生成后未重跑 = 结果与磁盘输入不一致）。双模式：**A. 重生成准备（不提交）** 与 **B. 全量重跑 + 转全并行（清输出+retry+解除 exclude）**。2026-08-11 实战流程，417 目录 0 失败。

## 触发
用户要求更新漂移目录输入文件 / 从 CONTCAR 续算准备 / INCAR↔OUTCAR 不一致处理；或**参数协议变更（+U/SOC/EDIFF/EDIFFG/MAGMOM/NELM）后需要重算存量目录**（loop 的 verdict 只看 OUTCAR 会静默接受旧结果，需人工强制重跑）/"现在全并行"推进批次。

## 前置
- repo `/home/duguex/vasp_sop`，项目根 `/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect`
- **必须用 `.venv/bin/python` 跑**（生产环境，loop 同款；conda env 的 vise 行为不同——fork U 表缺 Ti、-uis 吞参数）
- 代码 editable：改完必须 `systemctl --user restart vasp-sop-loop`
- loop service：`~/.config/systemd/user/vasp-sop-loop.service`（exclude 列表在这里改）

## 1. 漂移扫描

```python
import pathlib
BASE = pathlib.Path('.../2026_undergo_spin_defect')
drift = []
for sys_dir in sorted(BASE.iterdir()):
    if not (sys_dir / 'plan.yaml').is_file(): continue
    dirs = list((sys_dir/'defect').glob('*/')) + list((sys_dir/'cpd').glob('*/'))
    for t in ('band','dos','dielectric'):
        d = sys_dir/'unitcell'/t
        if d.is_dir(): dirs.append(d)
    so = sys_dir/'unitcell'/'structure_opt'
    if so.is_dir(): dirs.append(so)
    for d in dirs:
        inc, out = d/'INCAR', d/'OUTCAR'
        if inc.is_file() and out.is_file() and inc.stat().st_mtime > out.stat().st_mtime:
            drift.append(str(d.relative_to(BASE)))
```

**容差陷阱**：状态用 `mtime > out + 60s` 判；**同秒批处理（0.3s 差，如 08-10 02:20 的 BaAl2B2O7/BaAl4O7/SrGa4O7:Fe/Y2Sn2O7 批次）用 +60s 容差会漏——无容差重扫一遍**。漂移列表存 `/tmp/drift-rerun-list.json` 供多步复用。

## 2. 伪漂移判别（关键，避免白重跑）

每个漂移目录对比 **INCAR vs OUTCAR 头部回显**（VASP 权威 re-echo，第 13-41 行）：

```python
def outcar_echo(p):
    d = {}
    for line in p.read_text(errors='ignore').splitlines()[12:45]:
        m = re.match(r'\s*([A-Za-z0-9_]+)\s*=\s*(\S+)', line)
        if m: d[m.group(1)] = m.group(2)
    return d
```

判据：
- **参数完全相同 → 伪漂移**（mtime 只是文件重写）：不重跑
- **EDIFFG 全局 patch 造成的**（旧结果 -0.005 收敛 → 新标准 -0.01）：旧结果更严不劣，**不重跑**
- **EDIFF 旧值 1e-5 vs 新 1e-4**：更严不劣，不重跑
- **真漂移**（+U/LSORBIT/SOC/NELM 差异，如 OUTCAR 无 LDAU 而 INCAR 有）：**必须重跑**
- 注意 EDIFF/EDIFFG 回显会出现在 OUTCAR 头部——对比时**不要误排除**它们

## 3. 重生成输入（模式 A：先不提交；用户决策 2026-08-11）

- **defect（含 perfect charge=0）**：`prepare_inputs(d, cfg, kspacing=0.1, task_type='defect', extra_uis='SIGMA 0.02 LORBIT 11', charge=q)`——API 路径自动 NSW=100/NELM=30/EDIFF=1e-4/U(含 Ti=4 兜底)/SOC
- **cpd/unitcell**：`prepare_inputs(d, cfg, task_type=...)`——CLI 路径（vise 吞 -uis 参数 → patch_incar 兜底）NELM=50/EDIFF=1e-4 + patch_incar_u + SOC
- charge 解析：目录名 `_(-?\d+)$` 正则（`Va_O6_-2` → -2）
- **`restart_from_contcar(d)`**（POSCAR←CONTCAR + ISTART=1）
- **特例保护**：`Sr[FeO2]2` 重生成后必须 `patch_incar(d, EDIFFG=-0.01)`（vise 模板会回 -0.005，用户决策）
- **幂等**：后台脚本 + TSV 进度文件（`rel\tok`），重启跳过 done；`pkill -f` 可安全中断

## 4. 阶段 1/2 SOC（ADR 0014 两阶段）

- 5 个 soc 体系（Gd2GaSbO7:Bi/La2SrSc2O7/La2Zr2O7/Y2Sn2O7/Y2Ti2O7）：plan 加 `stage2_soc: true`（已配）
- **漂移目录 + 所有未收敛目录 `_strip_incar_tags(d, 'LSORBIT', 'ISYM')`**——未跑过的目录（无 OUTCAR）不在漂移列表但 INCAR 残留旧 SOC，带 SOC 提交会 ZBRENT 崩（2026-08-11 209 次失败的根因）
- 已收敛目录保留 SOC（ADR 0014 口径共存）；阶段 2（非 SOC 收敛后自动）：`Bi_*` 目录 SOC 续算、其余 SOC 单点

## 5. dopant 缺陷增量重建（defect_in.yaml 旧于 plan dopant 变更时）

- 备份 `defect_in.yaml` → `pydefect ds -d <dopant>`（增量，旧列表保留）→ `pydefect_vasp de`（**已有目录 FileExistsError 跳过**，只建新目录——不碰已有输入）
- 新目录生成输入（`prepare_inputs` charge 从目录名 `_(-?\d+)$` 解析）
- **阴-阳反位被 ADR 0013 排除**（如 Bi_O*：目录保留但永不提交）——别当 bug

## 6. 清输出 + retry + 根级门（模式 B：重跑）

```bash
# 清输出：OUTCAR OSZICAR vasprun.xml CHGCAR CHG WAVECAR DOSCAR EIGENVAL
#         PCDAT PROCAR IBZKPT REPORT LOCPOT ELFCAR + calc_results.json calc_summary.json
# 保留：INCAR/POSCAR/POTCAR/KPOINTS/CONTCAR（续算起点！POSCAR 已是 CONTCAR，直接续算）
vasp-sop batch retry <root> <dir1> <dir2> ...   # 100/批防参数上限，幂等 pending；failed 重提同样走 retry
```

**根级 summary 门**（否则 wave2 整体跳过 defect 提交）：
- `defect/defect_energy_summary.json` / `.partial.json` 存在 → 删
- 已 COMPLETE 体系（如 CaAl4O7）重跑单点 → 删 summary → 收敛后自动重新 analyze/COMPLETE

## 7. 解除串行 exclude 全并行（模式 B）

```bash
sed -i 's| --exclude ...||' ~/.config/systemd/user/vasp-sop-loop.service
systemctl --user daemon-reload && systemctl --user restart vasp-sop-loop
```

## 8. 验证

- `crisp jobs --human`：submit/submitted/running 计数持续增长（submit 193 是排队非卡死；~250 在途正常）
- 抽查新提交目录的 INCAR（参数正确：阶段 1 无 LSORBIT、LDAU 正确、MAGMOM 在——含 Fe 体系）和 tag（分区正确）
- 注意 Sr[FeO2]2 类磁态体系：重跑后**首离子步 mag=** 应回到锁定值（MAGMOM）
- **LDAU 覆盖率验证**：LDAU 缺失 ≠ 都是 bug——无 U 表元素体系（La/Zr/Sr/Al/Ca/O 等）无 LDAU 是正确行为；只查含 U 元素（Ti/Fe/Gd/Zn/Cu/Mn/Ni/Co/镧系）的目录
- crisp agent.db 查失败：`select local_dir, error_msg from jobs where status='failed' and submit_time > X`（ZBRENT = 没 strip 干净）
- **切体系时重提**：loop 的 verdict 只看 OUTCAR，不会因 INCAR 新而重跑——切到漂移体系需 `batch retry` 或清 OUTCAR 强制重提（清单记 docs/next-actions.md）

## 已知坑
- **环境差异**：`.venv`（libs/vise fork，U 表缺 Ti）vs conda env（官方 vise，有 Ti:4）——生成结果不同！验证必须用生产环境重跑，别用 kernel 的 conda env 下结论（eval kernel 的 import 可能缓旧模块——reload 或 subprocess 验证）
- eval 单元格 30s 超时：417 目录 × 3s 必须后台脚本 + 日志轮询
- INCAR 删除前先备份/确认（脚本删了 INCAR 后 prepare_inputs 覆盖 POTCAR——与 config 一致，无碍）
- 重生成后 INCAR 仍比 OUTCAR 新（漂移 warning 会继续触发）——正常，等重提后 OUTCAR 更新
- **运行中作业目录不要清输出**（会破坏 in-flight 轮次）
- 漂移重跑后 CONTCAR 是旧结果几何——POSCAR 已是 CONTCAR（`restart_from_contcar` 准备时做），直接续算

## 相关
- `_warn_incar_drift`（orchestrator.py）：converged 目录的漂移 warning（2026-08-11 加）
- `patch_incar_u`（io.py）：U 兜底（Ti=4 2026-08-11 纳入 _U_TABLE）
- `_U_TABLE` 更新需同步确认 libs/vise fork 与官方表 diff