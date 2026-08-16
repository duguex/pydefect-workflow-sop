---
name: vasp-sop-cpd-phase-exclude-reuse
description: vasp-sop cpd 相操作员流程：取消在飞任务+移出 cpd/ 排除未收敛相，或从旧树复制协议匹配的收敛 OUTCAR/CONTCAR 复用，含 loop 验证与协议核对。
---

# vasp-sop cpd 相操作：排除未收敛相 + 复用旧树收敛结果

操作员级 cpd 相处理流程（2026-08-14 定型，BaAl4O7_mp1019534 Al13Fe4 复用 / Y2Ti2O7_mp5373 Ti8Bi9 排除）。

## 何时用

- cpd 相反复不收敛（loop WARNING "N ionic restart(s) without convergence — auto-restart capped" / TIME LIMIT 截断 / NSW 用尽），用户决定不再跑（排除）
- 同 mp-id 相在旧树/其他体系已有收敛结果，用户决定复用（cpd 参考相能量与宿主树无关）

## 先查事实（勿问）

1. 该相在**全批次**是否算过：扫 2025/2026 两棵根下同名目录（`cpd/<mp-id>`），读 OUTCAR `reached required accuracy` + 最后 FREE ENERGIE TOTEN（注意：OUTCAR 可能被 crisp 截断，F= 行缺失，能量用 `grep -A2 "FREE ENERGIE" | grep TOTEN`）。
2. 协议对比：EDIFF/ENCUT/ISMEAR/ISYM/LDAUU/LDAUL/MAGMOM/KPAR/NSW/EDIFFG，找出唯一差异项并量化（meV 级差异让用户裁决）。
3. 该相当前在飞任务：`agent.db`（`~/.crisp/data/agent.db`）`local_dir like '%<相名>%'`，取最新 id 的 task_name/status。

## 排除流程

1. `crisp cancel --name <task_name>` 取消在飞/排队任务（返回 success 后记录会被清除，`select ... where id=...` 变 None）。
2. `mv cpd/<相名> cpd_excluded/`（loop 扫描 cpd/ 不再见它，永不重提；不改名留在原处——loop 会当相目录继续提交）。
3. 等一个 loop cycle（~2min），验证 `agent.db` 无新任务 + journalctl 无该相提交。

## 复用旧结果流程

1. `cp <旧树>/cpd/<相名>/OUTCAR <新树>/cpd/<相名>/OUTCAR`，同样复制 CONTCAR。
2. 验证：OUTCAR 含 reached required accuracy、TOTEN 正确、**OUTCAR mtime > INCAR mtime**（否则 loop 漂移扫描判 INCAR 新于 OUTCAR 会重生成重算）。
3. loop 以磁盘收敛判定为准（convergence_verdict），无需手动改 JobStore/agent.db；等一个 cycle 验证 0 新任务。

## 陷阱

- 复用前必须协议核对：EDIFFG/EDIFF/ENCUT/MAGMOM 差异会改变能量（2026-08-14 实测 Al13Fe4 旧 EDIFFG=-0.02 vs 新 -0.01，接受 meV 级差异需用户点头）。
- 旧树 OUTCAR 可能是 SOC 单点（NSW=0）或旧协议残留——先看 INCAR NSW/LSORBIT 再决定能否复用。
- 排除的相若在化学势凸包关键位置（如 Bi 二元相），凸包会缺相——操作前明示影响。
- crisp completed 不等于收敛：TIME LIMIT/EDIFF 未达也会标 completed（fetch 落地），必须看磁盘 OUTCAR reached accuracy。
