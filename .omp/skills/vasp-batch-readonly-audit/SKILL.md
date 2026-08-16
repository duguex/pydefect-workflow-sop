---
name: vasp-batch-readonly-audit
description: "只读审计 vasp-sop 批次（2026/2025_undergo_spin_defect）四维健康度：能量合理性、磁矩、输入一致性、输入输出相容性。用户要求\"检查所有计算/能量磁矩合理性/输入一致性/只检查不改\"时使用。"
---

# vasp-sop 批次只读审计（2026-08-12 实证）

用户说"检查 2026/2025 所有计算（能量、磁矩合理性，输入一致性，输入输出相容性），只检查不动手改"时按此流程。**全程不写批次树**，脚本与中间数据放 /tmp。

## 1. 数据采集（4 个脚本）

- `audit_2026_scan.py`：逐目录 INCAR/INCAR.tuned 解析 + NELECT 审计（ΣZVAL−q，ZVAL 从目录 POTCAR TITEL/ZVAL 读，q 从目录名 `_(-?\d+)$`）+ mtime（INCAR 新于 OUTCAR>1h）+ calc_results。全树 2061 目录约 2 分钟。
- `audit_2026_echo.py`：OUTCAR 头部回显 vs INCAR/tuned 比对 + 收敛标记。1052 已算目录约 1.5 分钟。
- `audit_2026_mag2.py`：**磁矩必须从最新 slurm log 的 `mag=` 取**（OUTCAR 尾被 crisp fetch 截断，只有 NELECT 与分块）；无 mag= 行 = ISPIN=1 run。
- `audit_2026_conv.py`：最新 log 的 CRISP_COMPLETED/ionic acc/NELM reached + 最后 F= 行。

## 2. OUTCAR 回显解析铁律（易错）

- 取**每个 key 最后一次出现**：OUTCAR 头部先打 DEFAULTS 块（IBRION=-1/ISPIN=1/LSORBIT=T），真实 INCAR 回显在后面。
- 只收单 token 值（`\s+KEY\s*=\s*(\S+)$`）：描述行 "IBRION = -1 ionic relax:..." 会污染。
- ENCUT 回显只打 1 位小数（348.247→348.2），比对时 round(f,1)。
- `grep -c "LOOP+"` 会误匹配 LOOP:（正则陷阱）；离子步计数用 `LOOP\+:`。

## 3. 核心判据

- **执行版 = INCAR.tuned**（crisp 提交快照）；磁盘 INCAR 可能陈旧（再生成未重跑）——mtime 交叉验证。
- 形成能原始差分：`E_diff = E_f − q·vbm + Σ μ_std[k]·v_k`（标准态参照）。非 SOC 体系正常中位 +7~9 eV、<−2 eV 个位数；SOC 体系出现 −5~−11 eV 批量 = 异常（2026 实证：Y2Ti2O7 51/162 条，缺陷弛豫相对 perfect 全局重构 5-8 Å + 历史错 NELECT 废 run 污染链嫌疑）。
- 磁矩合理性对照表（实测自洽）：Va_Al3+ q→(3+q) μB、Va_Sb5+ →(5+q)、Va_Zr4+ →(4+q)、Va_O →(2−q)、Fe2+/Fe3+ 高自旋 3-5 μB、d0 宿主 perfect 近零。
- **+U 元素（Gd/Fe/Zn）无 MAGMOM → SCF 塌缩 NM**（Gd2GaSbO7:Bi 实证：LDAUU=5 但局域矩≤0.08 μB）；defect 净矩全来自 O 2p 空穴即告警。
- 无 OUTCAR 的 defect 目录 = X→O/O→X 反位（ADR 0013 排除），**不是欠账**；summary 电荷态数 == 计算目录数 − 1（perfect）。
- 交叉核对 summary vs defect_energy_info.yaml vs 手算，排除修正项/化学势假象。

## 4. 已知协议值（2026 批）

cpd：ENCUT=1.3×ENMAX(520/414/348...)、EDIFFG=-0.01（2025 协议 -0.005，2026 两值混合）、NELM=50（135 目录残留 100）；defect：ENCUT=400、NSW=100、NELM=30（修复集 NELM=100/EDIFF=1e-5、SOC 单点 NELM=200）、EDIFFG=-0.03、SIGMA=0.02、LORBIT=11；unitcell：structure_opt 520/ISIF=3 vs band/diel/dos 400/ISIF=0（2025 同构，非漂移）。SOC 体系宿主缺陷两阶段（no-SOC 弛豫→SOC SP），Bi 掺杂缺陷曾有 SOC 弛豫混合。

## 5. 输出

按 级别（A 物理/ B 协议/ C 陈旧）表格 + 每项证据（目录+数值）+ 建议下一步（不执行）。审计脚本可复跑，批次树零写入。
