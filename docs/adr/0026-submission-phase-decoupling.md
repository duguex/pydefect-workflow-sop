# ADR 0026: 提交与相位解耦——相位是分析门

- 状态: 已接受 (2026-08-16)
- 相关: ADR 0001（相位持久化→已废弃）、ADR 0011（磁盘派生）、ADR 0013（反位排除）、ADR 0014/0025（两阶段 SOC/U）、ADR 0005（化学环境）

## 背景

旧相位机（STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE）把**提交时机**与**分析依赖**混在同一个状态里：

- `wave1` 只在 STRUCTURE_OPT 提交 target——cpd 竞争相、defect、unitcell 全部被相位顺序阻塞（尽管计算相互独立）。
- 单点腿（band/dos/dielectric）gate 在 UNITCELL_DEFECT——即使 structure_opt 早已收敛，也等不到提交。
- 实践中反复出现「计算独立却被相位门阻塞」：宿主 supercell 独立弛豫、cpd 竞争相独立、单点腿只依赖收敛结构——相位门是人为编排残留。

同时确认：target（cpd 宿主相）与 unitcell/structure_opt 是**同一计算**（wave3 从 target 派生 structure_opt 目录），同一弛豫在状态机里挂了两个名字，加剧混淆。

## 决策

**提交无条件化**：`advance_one_system` 每轮先做一次无条件提交遍历——`wave1_optimize`（宿主 target，幂等：已收敛/已提交跳过）+ `wave2_submit`（竞争相 + stage2 补充 + unitcell 单点腿 + perfect + 缺陷链播种）——任何输入就绪的目录即提交。

**相位收敛为分析门**：

| 相位 | 含义 | 触发的分析 |
|---|---|---|
| `RUNNING` | 有计算未收敛 | 无 |
| `CPD_READY` | cpd 全收敛，凸包未算 | `wave3_cpd`（凸包 + 派生 structure_opt） |
| `ANALYZE_READY` | 凸包 + 全腿收敛，分析未跑 | `wave3_analyze`（unitcell.yaml + pydefect） |
| `COMPLETE` | 分析完成 | — |

`System.phase()` 纯磁盘派生（四道门：cpd 收敛 + 凸包产物 + 腿收敛 + 分析产物）。

**保留的物理依赖**（不是相位门）：
- 缺陷链播种（ADR 0010）：非根电荷态等收敛兄弟 CONTCAR。
- 单点腿：等 `unitcell/structure_opt/CONTCAR`（收敛宿主，由 CPD 门派生）。
- 分析硬门（ADR 0025）：analyze 前全部弛豫腿达最终协议（stage2 补充）。

**保留的阻塞语义**：`.failed` 标记（crisp 终端）在凸包未算时阻塞 CPD；凸包已算后不 regress（与旧 persistence gate 对齐）。

## 影响

- 新相位名替换旧名（`STRUCTURE_OPT/COMPETING/CHEM_POT_DIAGRAM/UNITCELL_DEFECT` 仅作 legacy 常量保留，代码不再使用）。
- `wave3_postprocess` 拆为 `wave3_cpd` + `wave3_analyze`；`cpd_only` 适配。
- 单点腿生成 gate 从相位改为 structure_opt CONTCAR 存在性。
- 测试适配（test_system/test_cli 等相位断言 + 提交行为）；596 通过。

## 替代方案

- **只去单点腿 gate**（渐进）：保留相位机骨架——但 wave1-only（target 单独）与「计算独立」的矛盾仍在，治标。
- **零写入 dry-run 隔离**（同日 commit）：独立于本 ADR；dry-run 在镜像树运行，生产零污染。
