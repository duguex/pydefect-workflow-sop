---
name: vasp-sop-new-system-bootstrap
description: "Bootstrap a new vasp-sop defect system tree (fresh formula or ADR-0023 rebuild) in the 2026 batch: defect init, host-identity gate, clean cpd fetch with main-phase restore ordering, generate-inputs, git baseline, loop start, and the JobStore purge for resurrected void trees. Use when 建新体系/重建树/新掺杂体系."
---

# vasp-sop 新体系 bootstrap（2026 批，含掺杂）

## 何时用
在 2026_undergo_spin_defect 根下新增一个体系树（如 Ba3W2O9:Mn）或重建树（Y2Ti2O7_mp5373 / BaAl4O7_mp1019534）。宿主多形体选择按 ADR 0023（最低 e_above_hull）；任何新树先跑 `skill://vasp-sop-reference-phase-polymorph-audit` 的宿主身份门。

## 步骤（2026-08-14 实证顺序）
1. `mkdir <root>/<Sys>` → `vasp-sop defect init -f <Formula> -d <Dopant>`（cpd/ 自动建 + 抓相 + fallback 下载主相；已修 bug 的 fallback 会按 energy_above_hull 选最低相）。
2. **宿主身份门**：本地 `unitcell/structure_opt/POSCAR` vs MP 主相 StructureMatcher fit 必须 True（rms≈1e-15 未弛豫；弛豫后 ≤0.15 算同拓扑）。
3. **干净 cpd 相集**（勿复用旧树 cpd 目录——OUTCAR 可能被未收敛作业污染；重建树尤其）：删掉旧 cpd 副本 → `vasp-sop materials fetch -e <els> -d <dop> -o cpd`。
4. **坑：fetch 的 combo restore 会清空 cpd 里已有目录**——包括主相。所以主相必须在 fetch 之后重新下载：
   ```python
   from mp_api.client import MPRester
   with MPRester(os.environ['MP_API_KEY']) as m:
       s = m.get_structure_by_material_id(mpid)
       s.to(fmt='poscar', filename='cpd/<Formula>_<mpid>/POSCAR')
   ```
   并把 POSCAR 复制到 `unitcell/structure_opt/POSCAR`（否则 NO_TARGET）。
5. `vasp-sop batch run <root> --dry-run` 验证新树进 STRUCTURE_OPT（旧体系显示 skipped 正常）。
6. `vasp-sop batch generate-inputs <root>`：只为 !input_ready 目录生成（旧体系自动跳过），cpd 输入生成 ~7min/体系×相数。
7. git 基线：`vasp_sop.core.git_snapshot.init_system_repo(root)`（内部自带 baseline commit；二次 commit 无变更返回 False 正常）。
8. 起/确认 loop：`systemctl --user start vasp-sop-loop.service`（ExecStart 指向 2026 root，--loop --poll 120）。

## 坑：loop 复活已作废树
`_restore_crisp_active` 会把 crisp 活跃任务记入共享 JobStore（~/.vasp_sop/jobs.db），retry 机器随后**复活任何被 cancel 的树**——包括已作废的（如 recompute/Y2Ti2O7_d12，P2 宿主，ADR 0023 作废）。处置：
```bash
crisp cancel -n <task>          # running 的逐个取消；ready_fetch 取消失败正常（已 fetch 完）
sqlite3 ~/.vasp_sop/jobs.db "DELETE FROM tracked WHERE dir_path LIKE '%/recompute/%'; DELETE FROM job_history WHERE dir_path LIKE '%/recompute/%';"
```
清完后等 1 个 poll 周期（120s）确认日志不再出现该树。已完成的旧树目录在磁盘上无害，但 JobStore 记录必须清，否则每轮重提。

## 坑：ZBRENT bracketing fatal
新相首跑可能 ZBRENT fatal（"I REFUSE TO CONTINUE"）——2026 批已知模式，loop 自动 EDIFF 补丁重试，不是输入系统性错误，无需干预。

## 后续
到达 COMPETING 后程序自动构建 defect 腿——**必须过审批门**（skill://vasp-sop-independent-recompute-approval-gate：defect_in/电荷态/cpd 相集显式批准后才提交缺陷作业）。掺杂价态→电荷态映射示例：Mn(+2..+6) 掺 W⁶⁺ 位 → q = −4..0。
