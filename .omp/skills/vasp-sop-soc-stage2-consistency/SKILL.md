---
name: vasp-sop-soc-stage2-consistency
description: 解锁 vasp-sop SOC 体系的两阶段 SOC（ADR 0014）并保证形成能级别一致：回退相位前预标记 old_full_soc 防重复 SOC 单点、处理回退后 STRUCTURE_OPT 落点、按 hull 一致性门判定哪些 stage2 是必需而非可选。
---

# vasp-sop SOC 体系 stage2 解锁与级别一致性

场景：SOC 体系（soc+stage2_soc=true）相位已过 COMPETING，cpd 欠账不再自动提交；要解锁补算时必须同时保证形成能级别一致。

## 核心不变量

stage2 SOC（NSW=0 单点 / Bi_* 续算弛豫）**替换**目录总能量（非叠加修正）。形成能 = E_def − Σμ(hull)，μ 来自 cpd 相凸包。所以**同一体系 hull 全部相 + 缺陷必须全 SOC 或全非 SOC**；混入即不可比，化学势本身无效。

- 旧协议 full-SOC 相（old_full_soc）在其体系里是 SOC 级 = **等效 st2**，不是非 SOC 相
- 推论：一旦某体系 defect 腿做了 SOC（st2_done），其 cpd 凸包的 SOC 补充就是**正确性必需**，不是可选增强；"stage-1 精度先收尾"只有在该体系 hull 里 0 个旧 full-SOC 相时才成立（罕见）

## 盘点（每体系每腿）

统计每目录：
- `LSORBIT∧converged∧history 含 soc_stage2` → st2_done
- `converged∧无 LSORBIT` → 待补（st1 完成）
- `LSORBIT∧converged∧无 soc_stage2 记录` → old_full_soc（旧协议全 SOC，**等效已 SOC**，不欠补）
- 判定：若 defect 已 SOC 而 hull 有"待补"→ 该体系**不可发布**，补算是必需非可选。

## 机制事实（代码，orchestrator.py）

- cpd stage2 腿只活在 **COMPETING 分支**（~544，`if p=="COMPETING"` 内）；defect stage2 腿在 defect 提交腿（~787，COMPETING 与 UNITCELL_DEFECT 都跑）→ 为什么 5 个体系 defect 全补了、部分体系 cpd 没补
- 相位持久化门：target_vertices.yaml 存在即永不过回 COMPETING（system.py `_infer_phase_locked`）
- `_stage2_soc_pending(child, js)` = JobStore latest==converged 且 history 无 source==soc_stage2 记录
- `_submit_stage2_soc`：patch LSORBIT=True ISYM=-1；非 Bi → NSW=0 单点；Bi（目录名 Bi_*）→ POSCAR←CONTCAR SOC 弛豫；提交后写 source=soc_stage2 记录
- #129 教训：stage2_soc plan 键必须在 `parameters:` 下，toplevel 会被静默忽略（曾全线禁用）

## 解锁步骤（回退前先做，防重复计算）

1. **预标记 old_full_soc**：对每目录 `js.record(str(path), 'converged', source='soc_stage2')`——否则 `_stage2_soc_pending` 会把它们当待补重投 SOC 单点（浪费 ~14+ 作业）。
2. **defect stale restored-submitted**：`batch retry` 解除（这些 dirs 的 JobStore 是 source=restored 的 submitted，提交腿每轮跳过）。
3. **UC 空 POSCAR**：`_prepare_all_inputs` 只在 `unitcell/band/INCAR` 缺失时触发；旧 INCAR 挡着 → 删 `band/INCAR` + `dielectric/INCAR` 触发重生成（含 POSCAR）。
4. **相位回退**：把 `cpd/{target_vertices,standard_energies,composition_energies,relative_energies,chem_pot_diag}.yaml/json` 移到 `<sys>/.phase_bak_<date>/`（cpd/ 外，防 preflight 把它当相）。
5. **回退后可能落 STRUCTURE_OPT**：若 target 相 OUTCAR 曾被清输出（08-10 +U/SOC 批次），回退后 `_infer_phase_locked` 检查 `js.latest(target)!=converged` → STRUCTURE_OPT，**loop 的 wave1 会自动重提 target**（无需手动），收敛后自动进 COMPETING 铺开 cpd+stage2。确认 target 磁盘有 OUTCAR；没有就让它重跑（一轮弛豫），别手动 backfill 假装收敛。target JobStore 记录可能 stale 同理。
6. **验证三件事**：old_full_soc 无新增作业；stage2 只投"待补"；系统回到 COMPETING 且 cpd 全部入队。

## 关键：解锁顺序（先投补充，后回退相位；顺序反了失效）

**为什么不能先回退**：体系 cpd 全收敛时删 target_vertices → competing_dirs 空 → 相位落到 **CHEM_POT_DIAGRAM**（不是 COMPETING）→ stage2 腿不触发；且 CPD post 会在**混合 hull** 上立即重写标准能量 → 锁死混合结果。

分三种情形：

1. **已在 COMPETING 的体系**：直接回退（上节步骤），COMPETING 腿会自动投 never-cpd + stage2。
2. **已过相位且 cpd 全收敛的体系**（如 Y2Sn，13 类待补）：
   a. 直接调 `_submit_stage2_soc(child, sys, js, False, info_fn, priority=10)` 逐个投待补目录（非 Bi → LSORBIT+NSW=0 单点；Bi_* → LSORBIT+CONTCAR→POSCAR 续算；自动写 soc_stage2 记录）
   b. 等全部收敛（disk verdict converged + INCAR 带 LSORBIT + 不在 crisp live；跑完单点 1-2h / Bi 弛豫更久）。**任一失败/terminal → 停，别动 hull**（会再混）
   c. 验证 hull 全 SOC（0 个非 SOC 相）
   d. 再删 CPD 产物 → CPD post 全 SOC 重算 → target_vertices 重建
   e. defect analyze 重跑 wave3（一致 μ）
3. **错过窗口的一般情况**：先投补充 → 等全部收敛 → 回退 CPD 产物 → 等 loop 在**全 SOC hull** 重跑 CPD post → 验证 target_vertices.yaml 重建（mtime 新）→ defect_energy_summary.json 比它新（analyze 重跑）→ 完成。

## 陷阱

- 备份目录必须放 cpd/ **外**（cpd/ 内子目录都会被视为相）。
- 混 hull 判定要重查，不要沿用旧盘点：待补目录可能刚被补投（soc_stage2 记录已布防）。
- **INCAR 被剥裸协议诊断**（force_gate + ZBRENT + stall 相，issue #132）：疑点目录 INCAR 缺 SIGMA=0.02/LORBIT=11/NELM=30（对照已收敛 sibling）→ 被某重写路径剥参数。裸 INCAR（SIGMA 回默认 0.2）→ 能量面糊 → 力跳变 + ZBRENT 抖动 + stall。修复：删 INCAR 用 `prepare_inputs(..., extra_uis="SIGMA 0.02 LORBIT 11")` 重生成完整协议再从 CONTCAR 续算；加参数完整性守卫（#132 建议）。
- 已知案例：La2Zr2O7/Y2Ti2O7 回退后 target 落 STRUCTURE_OPT 自动重跑（target OUTCAR 已清）；Gd 无需回退（cpd 已全）。

## 无头监督（错过窗口补刀）

轮询脚本（crisp live + verdict + LSORBIT）→ 全收敛 → 移产物 → 验 target_vertices/analyze；hub start detached 托管（模式见 vasp-sop-batch-rescue-playbook 的 supervisor）。