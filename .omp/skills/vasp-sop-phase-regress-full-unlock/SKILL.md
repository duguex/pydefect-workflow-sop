---
name: vasp-sop-phase-regress-full-unlock
description: "Unlock vasp-sop systems stuck past COMPETING with never-ran cpd/defect/UC dirs: full phase-regress recipe with old_full_soc pre-marking, the STRUCTURE_OPT target trap, stale restored-submitted defect retry, and stale-INCAR UC regeneration. Use when 相位已过 COMPETING 但 cpd/defect 欠账不重提 (ADR 0020 Gd 事故类), or 10 体系快算推进."
---

# vasp-sop 相位回退全解锁（2026 批次 快算 playbook）

触发：系统 phase 已越 COMPETING（UNITCELL_DEFECT），但 `batch status` 显示 CPD/Defect D/T 欠账，且 loop 每轮跳过不重提。根因两类，先分类再解锁。

## 0. 诊断分类

```bash
vasp-sop batch status <root>      # D/T 欠账看腿
vasp-sop batch blockers <root>    # never_ran / unconverged 计数
sqlite3 ~/.vasp_sop/jobs.db "SELECT dir_path,status,source FROM job_history WHERE dir_path LIKE '%<sys>%' GROUP BY dir_path ORDER BY timestamp DESC LIMIT 20;"
```

机制认知（决定分类）：**defect/UC 腿在 UNITCELL_DEFECT 分支是活的**（wave2 的 `p in (UNITCELL_DEFECT, COMPETING)` 段）——never 目录不是结构死区；**cpd 提交腿只活在 COMPETING 分支**（ADR 0020/#122）——过相位体系缺 cpd = 结构死区，必须相位回退。

| 症状 | 根因 | 解锁 |
|---|---|---|
| defect 目录有输入（INCAR+POSCAR+defect_entry.json）但不提交 | JobStore `latest='submitted'` 且 `source='restored'`（loop 启动从 crisp 活跃集恢复，crisp 作业已死）→ 提交腿 `latest==submitted → continue` | `vasp-sop batch retry <root> <sys>/defect/<dir> …`，UNITCELL_DEFECT 腿下轮重提（2026-08-12 实证 10 目录一击即解） |
| UC band/dielectric 有旧 INCAR 但无 POSCAR | `_prepare_all_inputs` 只在 `band/INCAR` 缺失时触发生成；旧 INCAR 挡住重生成 → empty-POSCAR 门每轮拒绝 | `rm unitcell/{band,dielectric}/INCAR`（**连同 INCAR.tuned——执行版输入，重生成须从模板重建**），下轮重生成+提交 |
| cpd 目录有输入（POSCAR/INCAR）但永不被提交 | cpd 提交腿只活在 COMPETING 分支（结构死锁；`_infer_phase_locked` 的 `target_vertices` 持久门不回头） | 相位回退（下） |

## 1. 相位回退（每系统）

```bash
sys=La2Zr2O7   # 或任意卡死体系
cd <root>
mkdir -p $sys/.phase_bak_YYYYMMDD     # 必须系统根下，绝不能放 cpd/ 内（preflight 把 cpd/* 子目录当相）
for f in target_vertices.yaml standard_energies.yaml composition_energies.yaml relative_energies.yaml chem_pot_diag.json; do
  [ -f $sys/cpd/$f ] && mv $sys/cpd/$f $sys/.phase_bak_YYYYMMDD/
done
```

移动而非删除（可恢复）。删 `target_vertices.yaml` → 相位持久门失效 → 上游推断：`STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM`。回退后的 CPD 后处理：阶段相能量不变（converged 目录 verdict 跳过重投），重跑的是 mce/sre/chem_pot_diagram（本地计算，无 VASP），完成后自动回 UNITCELL_DEFECT。

## 2. ⚠️ 先预标记 old_full_soc，防 stage2 腿误投

回归后有 `_stage2_soc_pending`（JobStore converged 且无 `soc_stage2` 记录）会把**已全 SOC 收敛的旧相**当待补重投 SOC 单点（浪费）。判别 + 标记：

```python
ls_pat = re.compile(r'^\s*LSORBIT\s*=\s*\.?TRUE', re.I|re.M)
# 对每个 cpd 目录：LSORBIT 在 INCAR + verdict converged + history 无 source=='soc_stage2' → old_full_soc
from vasp_sop.core.job_store import JobStore
with JobStore() as js:
    js.record(str(d.resolve()), 'converged', source='soc_stage2')  # 每个 old_full_soc 目录
# 或直接 sqlite 插入：
# c.execute("INSERT INTO job_history (dir_path,status,timestamp,source) VALUES (?,?,?,?)", (p,'converged',time.time(),'soc_stage2'))
```

2026-08-12 实证：La2Zr2O7 8 个、Y2Ti2O7 6 个，不标则每系统多投 ~6-8 个冤枉 SOC 单点。

## 3. ⚠️ STRUCTURE_OPT 陷阱（回退后相位可能不是 COMPETING）

若 target 相（`cpd/<Formula>_mp-xxx`）的 OUTCAR 早在清输出批（如 08-10 +U/SOC regen）被删、而 JobStore 仍记 converged，回退后上游推断先查 `_js.latest(target)!=converged → STRUCTURE_OPT`。**这是正常现象不是失败**：wave1 `wave1_optimize` 会自动重提 target → 收敛 → COMPETING → cpd+stage2 腿自动铺开。等待即可（超胞弛豫小时级）。**别手动 backfill target converged（除非磁盘 OUTCAR 确实还在）**。

## 4. 验证

- 下一轮 loop：grep `Submit crisp VASP` 应见 target/cpd/defect 提交（`Submit crisp VASP in .../<sys>/cpd/<phase>` + `soc2:<name>`/`stage2` 单点）
- `batch_snapshot.json` phases 应出现 STRUCTURE_OPT=2 → COMPETING；回退体系轨迹 UNITCELL_DEFECT → STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → 回 UNITCELL_DEFECT
- `crisp jobs` 活任务数上升
- 不要手动 crisp submit 绕过 JobStore——交给 loop 走 retry/restart 语义

## 相关坑

- **重复 CPD 组成**（`Duplicate CPD compositions`）：比**每原子** TOTEN（`grep "free  energy   TOTEN"` 后除以 POSCAR 第 7 行原子数求和）——两候选常是精确 2× 关系（2× 超胞 vs 原胞），直接比 TOTEN 会选错。保留低者（<1 meV/atom 按噪声仍留低者），`mv` 高者到 `<sys>/.dup_bak_YYYYMMDD/`（系统根外）。别用 cpd_excluded_phases.yaml 排除（那是范围决策）。全批次无残留用仓库原函数验证：`collect_cpd_phase_provenance(<sys>/cpd)` 不抛 ValueError（**注意它按 CONTCAR-first reduced_formula 按键，不是 POSCAR 计数**——用仓库函数扫真重复）。
- **ZBRENT 触顶相**：先读目录内 `%j.log`（`ZBRENT: fatal error in bracketing` / `can't locate minimum` = 电子收敛问题→EDIFF），别只看 verdict force_gate_fail 就放宽 EDIFFG。金属相 EDIFF 收紧 1e-6（issue #131/#119 联动）。
- 排除 max_abc>25Å 相 = 单独决策，写 `cpd_excluded_phases.yaml`。
- EXCLUSION 备份目录绝不能留在 cpd/ 内（preflight 当相目录报错）。
- **别碰 crisp 活跃作业的目录**（dedup 存在但避免竞态）。

## 攻击顺序（快算）

1. `batch retry` defect 目录（最便宜，腿活着）→ 2. 删陈旧 UC INCAR → 3. 预标记 old_full_soc → 4. cpd 体系相位回退（两个体系都做可并行，集群 cap 60 内无虞）。全程 ~30 秒落盘。正在 wave3 后处理的体系延后回退（勿打断在飞后处理）。

## 恢复顺序（2026-08-12 实证成功）

defect stale retry ×N → UC 清旧 INCAR → 预标记 old_full_soc → 移 5 相位文件 → 等 loop 自铺。全程 ~30 秒落盘，集群 cap 60 内并行无虞。