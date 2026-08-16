---
name: vasp-sop-stale-analyze-chain-rebuild
description: "Rebuild stale vasp-sop defect analyze artifacts (calc_results/correction/energy_info/summary) after re-runs: stale-root-cause rules (cr reuse, efnv/dei exists-skip, analyze summary short-circuit), backup+delete recipe, regenerate+verify loop. Use when formation energies look stale/wrong after job re-runs, or 2026 batch 形成能没变/数值有误."
---

# vasp-sop 解析链重建（过时 calc_results/形成能修复）

## 何时用
缺陷目录重跑后形成能没变/明显错误（如深负值来自旧作业能量）。2026-08-13 实证：Y2Ti2O7/La2SrSc2O7 修复后 cr 与最新 log 全一致，E_f 中位数 −0.74→0.00 eV。含 analyze partial 的 soc2 stale-cr 竞态特例（另节）。

## 过时根因（三个独立短路，必须全部绕开）
1. **cr 复用**：`pydefect_adapter._cr_one`（vasp_sop/defect/pydefect_adapter.py:212）已有 calc_results.json 直接复用，只 `_override_ionic_conv`（重写文件但 energy 保留旧值）。新作业完成（fetch 新 vasprun/log）后 analyze 仍用旧 energy。
2. **efnv/dei exists-skip**：`pydefect efnv`/`pydefect dei` 的 parse_dirs 对已有 correction.json / defect_energy_info.yaml 直接 skip（adapter 的 force=True 只控制"不预读旧值"，命令本身仍 skip）。已存在的 correction/energy_info 永不重算。
3. **analyze 顶部短路**：`defect_energy_summary.json` 存在且 classify==full → analyze 整体跳过（analysis.py:309）。

判断依据：crisp fetch 后 OUTCAR 被截断（无 F= 帧、无 timing 块但尾部可能含 reached required accuracy）——**权威能量 = slurm log 最后 F= 行**，不是 OUTCAR/vasprun。

## 修复流程
```bash
# 0. 确认 loop 未跑（pgrep -f "vasp-sop batch run"）
# 1. 备份 + 删除（Y2Ti2O7 删了 151 个文件；备份到 /tmp/cr_backup_<date>/）
#    删除标准：cr energy 与最新收敛 log F= 差 >0.02 eV 且 vasprun.xml 新（mtime 距最新 log <5min）
#    同时删（无条件）：
#      defect/*/correction.json、defect/*/defect_energy_info.yaml
#      defect/defect_energy_summary.json、defect/defect_energy_summary.partial.json
#      <system>/formation_energy_interactive.html
python3 <<'EOF'
import json, os, re, glob, shutil
FRE = re.compile(r'^\s*\d+\s+F=\s*([-\d.E+]+)', re.M)
# newest_conv_log: 最新 mtime 且含 'reached required accuracy' 且有 F= 的 log
EOF
# 2. 重跑（summary 已删 → 无短路；cr 缺失 → 全量重提取）
vasp-sop defect analyze <system>
# 3. 验证（必须做）
#    a. 全树 cr energy == 最新收敛 log F=（0 mismatch）
#    b. defect_energy_info.yaml 的 formation_energy 手算复核：
#       E_f = E_def − E_perfect + q·vbm − Σ std·v（std 来自 cpd/standard_energies.yaml，vbm 来自 unitcell/unitcell.yaml）
#    c. summary + formation_energy_interactive.html 已重生成
```

**新鲜度门**：过时目录的 `vasprun.xml` mtime 必须 ≥ 最新 log mtime（否则重提取拿到的是旧值，只能重算）。

## 关键陷阱
- **单删 calc_results 不够**：correction.json / defect_energy_info.yaml 也必须删（efnv/dei 无 force 语义）。先跑 analyze 再删 correction 会白跑一轮（顶部短路只挡 summary，但 efnv/dei 的 exists-skip 让 correction/yaml 永不更新）。
- 只删 cr + summary 重跑 → 前一轮已生成新 summary 时，第二轮直接短路（522ms 退出）。删 summary 后重跑。
- **OUTCAR mtime == calc_results mtime（fetch 同时刻）不代表值新**——重写文件但 energy 保留旧值，比对值而非 mtime。
- `pydefect efnv` 对已有 correction.json 打印 "In <dir>, correction.json already exists." 后跳过（main_tools.parse_dirs exists-skip）。
- 未收敛 SP 目录的 calc_results 能量（如 03:37 旧 SP −764.358）会与最新收敛 SOC 值差 ~0.12 eV，全树扫 `|dE|>0.02` 可一次抓出。
- SOC 判定：`mag=` 1 分量 = 无 SOC，3 分量 = SOC；同一目录新旧 log 混跑无 SOC 值时以 3 分量为准。
- analyze 日志 INFO 可能只到 stderr 的 WARNING（setup_file_logging 把 INFO 写文件）；用 hub 跑时看 artifact 文件 mtime 判断阶段。
- 参考实现：vasp_sop/defect/analysis.py:298 analyze()、pydefect_adapter.py:212 _cr_one、libs/pydefect/pydefect/cli/main_functions.py:190 make_efnv_correction_main_func。

## 特例：analyze partial 的 stale calc_results 竞态（soc2 单点）

**症状**：analyze 每轮重跑（"post-process partial"），`missing_correction` 恒为同一批目录（常见于 SOC 单点 NSW=0 defect/cpd）；该批目录 `calc_results.json` 里 `electronic_conv=False`，efnv 报 "SCF in X is not reached"；但目录 OUTCAR/vasprun 其实是好的（NELM 内收敛）。

**根因（2026-08-12 实证）**：crisp 取回 vasprun 与 analyze 的 `pydefect_vasp cr` 抽取存在秒级竞态：取回刚写完 vasprun，analyze 读到旧内容 → cr 写出 econv=False → efnv 拒绝出 correction → 每轮重跑同一批。NELM=200 重跑后常复现。

**判定（区分真失败 vs 假阳性）**：
```python
from pymatgen.io.vasp import Vasprun
v = Vasprun('<dir>/vasprun.xml', parse_potcar_file=False, parse_dos=False)
# converged_electronic 规则 = len(final_elec_steps) < parameters["NELM"]
print(v.converged_electronic, v.parameters.get('NELM'), len(v.ionic_steps[-1]['electronic_steps']))
```
- OUTCAR 无 "reached required accuracy" **不等于**电子未收敛（NSW=0 单点不打印）；以 pymatgen `converged_electronic` 或直接 cr 为准
- cr 的 `electronic_conv=True` + vasprun mtime 新 → 目录健康，问题只在过期 cr 文件

**修复**：
1. 对 analyze_status.json 的 `missing_correction` 列表逐个：`rm <dir>/calc_results.json`
2. 重新抽取（**必须逐个 `-d`，pydefect_vasp cr 多 `-d` 只写最后一个**）：
   ```bash
   cd <system>/defect && pydefect_vasp cr -d <dir>
   ```
3. 验证：cr 里 `electronic_conv=True`，能量为当前 vasprun 的 final_energy
4. 下一轮 analyze 自动出 correction → status full

**相关**：同族 verdict `not_relaxation conv=True` 但 cr econv=False 的 soc2 目录（NELM=30 耗尽真失败时：NELM→200 重投）；#125（soc2 vasprun 截断）区分：vasprun `ends_ok=False`/scstep 缺失才是真截断。