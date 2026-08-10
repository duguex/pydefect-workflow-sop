# ADR 0016 — 电子收敛门（NELM 耗尽检测）

- 状态：已接受（2026-08-10）
- 关联：ADR 0010（播种/restart）、ADR 0015（cpd 刷新）

## 背景

CaAl4O7 的 Fe_Al2_1 被判收敛（OUTCAR 有 "General timing" + "reached required accuracy"），但 pydefect 的 `efnv` 拒绝：`SCF not reached`（`calc_results.electronic_conv=False`）。根因：**VASP 在最后电子步 NELM 耗尽时仍打印 "reached required accuracy"**（并警告 "spurious results ... we suggest increasing NELM"）——力恰好低于 EDIFFG 时误报离子收敛，但能量不可靠。

## 决策

`convergence_verdict` 增加**电子收敛门**（在 timing 检查后、所有返回点前）：
- OUTCAR 含 `increasing NELM` 警告 → 判 `not converged`（reason=`electronic_not_conv`）
- 警告可能距 EOF 数 MB（后续离子步跟随）——先查 256KB 尾窗，未命中再全文（带 mtime 缓存）
- 对所有任务类型生效（含 band/dos 等非弛豫任务——电子垃圾同样拒绝）

## 影响

- 2026 全量扫描：收敛目录中仅 CaAl4O7/Fe_Al2_1 命中（已 NELM=200 重算）
- 2025 存量扫描：**11 个目录命中**（BaGe4O9/BaO/Ca2Ge7O16/CaCO3/CaMg2(SO4)3/CeO2/Sr2MgGe2O7/SrGe4O9）——形成能不可信，2025 恢复时重算
- 判定与 pydefect 对齐：我们判 converged 的目录，pydefect 不再拒绝
