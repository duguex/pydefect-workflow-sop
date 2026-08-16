---
name: vasp-sop-unconverged-census
description: "盘点 vasp-sop 2026 批次未收敛计算时用准确欠账口径：先扣 ADR 0013 排除的反位目录（is_valid_defect_dir=false，显示为 never_ran 但不是欠账），再合并 crisp agent.db 分类 live/cancelled/failed/never_submitted，识别 operator-cancelled 未重提缺口。用户问\"还有哪些没算/为什么没收敛/展示未收敛\"时使用。"
---

# vasp-sop 未收敛盘点（ADR 0013 感知口径）

blockers.scan_system 的 never_ran 大头是 ADR 0013 排除的反位目录——盘点必须先用 is_valid_defect_dir 门扣除，否则把设计排除当欠账。

## 1. 分类口径（每体系）

对每个 defect 子目录依次判定：
1. `is_valid_defect_dir(c)` false → **invalid_excluded**（ADR 0013 反位，永不计算，勿报欠账）
2. `convergence_verdict(c).converged` → converged
3. crisp 作业 live（running/submit/submitted）→ live（正常）
4. `input_ready(c)` false → input_incomplete（缺 INCAR/POTCAR/KPOINTS）
5. 按 crisp 最新记录分：cancelled / failed / never_submitted

## 2. Block 词表（ADR 0007，blockers.py，勿手写目录遍历）

```python
from vasp_sop.core.blockers import scan_system
blocks = scan_system(d)   # {相对路径: Block}，done 已排除
```
- `never_ran`：输入齐、无 OUTCAR、无 live 作业 = 从未提交/待播种
- `missing_inputs`：detail 列出缺的文件（INCAR/POSCAR/POTCAR/KPOINTS）
- `unconverged`：跑完但力门未过，detail = verdict.reason + max_f（force_gate_fail / nsw_exhausted / electronic_not_conv / ...）
- `crashed`：OUTCAR 无 timing 段（VASP 没正常结束）
- UC 任务（band/dos/dielectric）走 check_task_complete，不含在收敛语义

## 3. 合并 crisp agent.db

```sql
SELECT local_dir, status, substr(submit_time,1,16), substr(coalesce(error_msg,''),1,60)
FROM jobs WHERE local_dir LIKE '%2026%' ORDER BY submit_time
```

- failed 记录按目录去重（同目录多次失败 = 重试链，如 La2SrSc2O7 161 记录/12 目录）——去重后才是真欠账
- **operator/legacy 时代 cancelled 的 defect 不会被调度器重提**——检查 cancelled 目录当前 verdict 是否收敛，未收敛即欠账（08-08 SOC 切换、NELECT 修复 cancel 的批次）
- error_msg 分类：ZBRENT=电子不收敛（persistent）、TIME LIMIT/KILLED=瞬态、missing or empty POSCAR=输入缺失
- **failed 历史记录 ≠ 目录当前状态**：必须对每个 failed 目录重跑 convergence_verdict / 查 OUTCAR mtime——多数已重跑成功（如 947d258 stale 修复浪潮后）

## 4. live 作业真相判定（勿用 submit_time）

slurm log mtime（`ls -t <dir>/*.log | head -1` + stat mtime）距今 >1h 而无 OUTCAR = phantom/stall；距今 <5 分钟 = 正常长算；提交 8h+ 且 log 停在数天前 = phantom（需 crisp 清理重提）。daemon 判断用 `pgrep -af daemon.cli` + `tail ~/.crisp/logs/crisp.log` 的 LIFECYCLE 行，勿用 `ps | grep crisp`（fork 不改 cmdline）。

## 5. 已知陷阱与常见误读

- batch status 的 D/T 把 ADR 0013 排除目录算进 T（如 CaAl4O7 显示 73/185=39%，实际有效集 72/72 全收敛）——显示误导，报数时给有效集口径
- 反位含 dopant 的阴离子位部分（Fe_O*/Bi_O*）——排除正确，dopant 金属位部分（Fe_Al/Bi_Sb）全在算
- 大晶胞相（max_abc>25Å）被 preflight 门挡但每 cycle 刷 WARNING——噪音非故障
- 大量 `never_ran` defect = 链式播种（ADR 0010）待链根收敛，**正常**，不是卡死
- COMPLETE 体系若 plan 后有 dopant_elements 变更 → 缺陷重建了但相位锁 COMPLETE（state.json 权威，ADR 0001）**永不调度**——调度器不回头，需人工介入（提 issue）
- cpd 输入重生成（全 SOC/参数修复）后若 crisp 无提交记录 → 相位已过 COMPETING 调度器不回头重提，需 `batch retry` 手动

## 6. 输出格式

按体系 × 原因 × 作业状态分组计数表，再列明细（体系/原因/verdict detail/最新作业状态/错误类/相对路径）。

## 7. 参考实现

scan_system(root) 返回 {rel_path: Block}；Block.reason ∈ done/missing_inputs/crashed/unconverged/never_ran；validity 门 vasp_sop/defect/__init__.py::is_valid_defect_dir（ADR 0013）；收敛权威 vasp_sop/vasp/convergence.py::convergence_verdict。