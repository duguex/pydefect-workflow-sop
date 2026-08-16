---
name: vasp-sop-check-results-ops
description: "Run and extend the vasp_sop batch result acceptance check (scripts/check_results.py, two-pillar: convergence truth + in-system comparability). Use when user asks 跑验收/检查结果/批次可信度/生成检查报告 for a VASP batch tree on this host."
---

# vasp-sop 批次结果验收检查（check_results.py）

## 触发
用户说「跑验收/检查结果/批次可信吗/生成检查报告」——机制是 `/home/duguex/vasp_sop/scripts/check_results.py`（两支柱：① 收敛真实性 ② 体系内可比性），输出体系验收表。

## 运行（必须用 venv python！）
```bash
/home/duguex/vasp_sop/.venv/bin/python3 /home/duguex/vasp_sop/scripts/check_results.py \
  --root /mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect \
  --json ~/.vasp_sop/check_results.json \
  --report ~/.vasp_sop/check_results_report.html \
  [--compare ~/.vasp_sop/check_results_prev.json]   # 体系级回归对比
```
或 systemd 手动入口：`systemctl --user start vasp-sop-check.service`（用户偏好手动触发，**无 timer**——曾被要求卸载）。
系统 `/usr/bin/python3` 不可用：无 vasp_sop 包 → ADR 0013 反位排除语义丢失 → 939 目录误报欠账。
退出码：0=全部可信，1=有不可信体系（门禁语义）。

## 产物
- `~/.vasp_sop/check_results_report.html` — 体系验收表（收敛 ✓/~ /✗、可比 ✓/✗、证据折叠、记录级告警列）
- `~/.vasp_sop/check_results.json` — 机器可读（systems[]）
- `~/.vasp_sop/check_results_prev.json` — 对比基线（service 自动轮转）
- findings 清单（一次性生成）：`~/.vasp_sop/findings_<date>.html`（门禁/欠账/记录级三组 + 建议动作）

## 判据（已定，勿改语义）
- **判据源**：OUTCAR 回显（INCAR 块 + TITEL/POTCAR: 行 + 尾部收敛/能量）> 盘面文件 > agent.db
- **收敛**：`reached required accuracy` 权威；豁免 = band/dos/dielectric（非自洽）+ energy-flat（NELM 耗尽但末两步 TOTEN 差 <1e-3，参数 --flat-ev）
- **可比性物理 key**：ENCUT/EDIFF/EDIFFG/SIGMA/LSORBIT/ISPIN/LDAUU/LDAUL。**LDAUU/LDAUL 按元素映射且只比共享元素**（元素集不同 = 相组成差异，不是不一致）；数值归一化（1e-05==1e-5）
- **ENCUT 分区豁免**：cpd 相按各自 ENMAX 档合法（不报漂移）；仅 unitcell+defect 组内强制统一；另有 ENCUT≥ENMAX 物理下限（Ga_mp-142 ZPOTRF 场景）
- **白名单记录级**：ISMEAR（金属/绝缘物理必需）、NSW/IBRION/KPAR/NCORE/ISYM
- **ISIF 协议**：cpd=3 / defect=2 / unitcell/structure_opt=3 / SP 豁免 / mol_* 豁免
- **ISPIN 预期** = 含 vasp_sop._U_TABLE 元素 ∪ defect 任务 ∪ SOC(LSORBIT=T)；实际生成逻辑见 vasp_sop/vasp/io.py（patch_incar_u 强制 ISPIN=2）
- **磁矩塌缩**：真磁性元素（Mn/Fe/Co/Ni/Gd+4f，**排除 Ti/Cu/Zn**——Ti4+ 无磁正常）ISPIN=2 但 |mag|<1
- **磁矩漂移**：同目录 *.log 序列末值差 >--drift-mag（默认 2 μB）；log 用 read_tail 尾部
- **零点一致性**：cpd/composition_energies.yaml 来源相 OUTCAR 变体集合 == defect/ 链变体集合
- SOC OUTCAR 磁矩是 3 值矢量（取模）；磁矩行必须按 head+tail 文件顺序取最后一个（拼接顺序错 → 取到头部初始值）

## 已知坑
- OUTCAR INCAR 回显块以 ` INCAR:` 开头、空行结束；ENMAX 行在 POTCAR 信息区（每元素一行，与 TITEL 序一致）
- 组扫描排除 `.big_sc_bak`/`defect_new`/隐藏/`_` 前缀目录（str(d) 全路径匹配）
- POTCAR 段提取拼接必须段间换行（`"\n".join(segs)`），否则 VASP forrtl severe(59) I/O error（15/15 提交失败现场）
- 报告 HTML 自包含（用户偏好 HTML 非 md）

## 用户边界（硬性）
检查/审计任务**只读**：不提交计算、不修改计算目录。发现问题 → 报告（可生成 findings 清单 HTML）→ 等授权再动。
