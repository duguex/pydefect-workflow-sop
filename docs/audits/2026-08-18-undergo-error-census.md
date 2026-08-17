# Undergo 生产树计算错误普查与 SOP 覆盖审查（2026-08-18）

> 目标：统计 2025/2026 `undergo_spin_defect` 两棵生产树下"单个计算大量错误"的情景与普遍高频问题，并逐项审查当前 SOP（vasp-sop 代码 + ADR + 门控）是**自动规避 / 自动修复 / 检测需人工 / 仍会出现**中的哪一种。
> 数据来源：两树全部 `vasp_stdout.log`（2025，2163 个）、`{jobid}.log` CRISP 状态行（2025 19821 / 2026 9810）、OUTCAR（2025 11912 / 2026 1816）、`.failed` 标记、`batch_timeline.jsonl`、`batch_snapshot.json`、`analyze_status.json`。关键结论已人工复核（见 §3.4）。
> 只读审计：未修改任何生产目录、未提交任何作业。

## 1. 普查结果

### 1.1 2025 树（41 体系，12327 计算目录）

**三层口径统计**（硬失败 / 未收敛 / 静默质量问题，全部按"至少命中一次"的目录数）：

| 层 | 问题 | 目录数 | 占比/次数 | 备注 |
|---|---|---|---|---|
| 硬失败 | **ZBRENT** 离子松弛失败 | 951 | 44%，8709 次 | 最主要的硬失败 |
| 硬失败 | BRMIX / "very serious problem" | 706 | 33% | 电荷密度混合不稳定 |
| 硬失败 | increasing NELM | 46 | 53 次 | 真电子不收敛（`NELMDL` 噪声已剔除） |
| 硬失败 | SICK JOB | 17 | — | |
| 硬失败 | POTCAR 物种数不匹配 | 17 | — | **MgS/SrS 的 Be 替位缺陷共 13 目录全废**（生成时 Be POTCAR 未追加） |
| 硬失败 | SIGKILL（作业级） | 24 目录 / 307 失败日志 | 89% 集中一处 | `CaMg2(SO4)3/unitcell/band` **274 次全失败**（EXIT_CODE 255 + signal 9 → 内存超限死循环） |
| 未收敛 | **活跃 defect 松弛未收敛** | **519 / 859 (60%)** | — | 以 `reached required accuracy` 缺失反向测量；其中 **388 个 (75%) 同时含 ZBRENT** → ZBRENT 是未收敛的主导机制 |
| 静默 | **MAGMOM 未指定** | **1714 (79%)** | — | OUTCAR 告警"did not specify the initial magnetic moment"；默认全原子磁矩=1，**对自旋缺陷项目是致命静默错误** |
| 静默 | perfect_missing 阻断分析 | 4 体系 | — | CaO / MgO / SiC / diamond（`analyze_status.json` skip_reason） |

已排除（两树零命中）：tetrahedron、NaN、EDDDAV、RMM-DIIS、TOO FEW BANDS、KPAR、OOM 文本、time limit、singular、ZPOTRF、FEXCP、PRICEL。

**单目录最恶劣**（按错误密集度）：`CaMg2(SO4)3/cpd/CaMg2[SO4]3_mp-1229186`（ZBRENT 93 次 + 重提交 20 次）、`CaMg2(SO4)3/unitcell/band`（274 次提交全失败）、`SrGe4O9/defect/Sr_Ge4_-2`（ZBRENT 42）。按体系 ZBRENT 目录数：BaGe2S5 71、SrGe4O9 68、BaGe4O9 67、Ba2MgGe2O7 65…——集中于低对称多原子复杂氧化物/硫化物。

### 1.2 2026 树（14 体系）

| 类 | 计数 | 说明 |
|---|---|---|
| **SIGKILL (signal 9)** | **1339 日志** | 头号硬失败；**~90% 集中在 `Gd2GaSbO7:Bi/cpd` 重试风暴** |
| TIME LIMIT | 155 | 与 SIGKILL 同源，部分大超胞超墙钟 |
| "aborting loop / EDIFF not reached" | 1617 | 弛豫中电子步噪声，非真失败（末步是否达精度才是判据） |
| ZBRENT | 1 | **与 2025（951 目录）形成强烈对比**——协议/重试机制已见效 |
| Tetrahedron soft | 10 | 轻微 |
| 真卡死目录 | 1 | 仅 `CaAl4O7/.big_sc_bak/Va_Ca1_-2`（备份目录） |

**单目录最恶劣**：`Gd2GaSbO7:Bi/cpd/Ga4Bi2O9_mp-23519` — 253 个作业日志，**243 次 SIGKILL**，仅 8 次成功（重试风暴后最终收敛）。同体系另 4 个 cpd 相各 236–244 失败周期。

**`.failed` 标记**：80 个中 **79 个是陈旧的**（后续已成功，标记未清）——`crisp_failed` 类计数不能直接当作当前失败数。

**CPD 配置类**（来自 batch_timeline）：`Duplicate CPD compositions`（Gd2GaSbO7:Bi、Y2Sn2O7）；`CPD mce preflight failed`（6 体系缺 OUTCAR/CONTCAR）。
**日志混树**：2026 根 `batch_run.log` 含大量 2025 树条目（unified loop 单日志行为，见 §3.3）。

### 1.3 两树对比结论

- 2026 的**物理错误结构**与 2025 完全不同：ZBRENT/BRMIX/未收敛基本消失（2025 的头号问题），只剩**调度/资源层**（SIGKILL 风暴）+ 配置层（CPD preflight/duplicate）。
- MAGMOM 告警 2026 **零命中**（人工复核，见 §3.4）——输入侧修复实证有效。
- "单个计算大量错误"的两树共性形态 = **重试风暴**：同一目录反复被杀反复重提（2025 band 274 次、2026 cpd 243 次），最终要么靠运气收敛要么耗尽。

## 2. SOP 覆盖交叉分析（四桶）

| # | 问题 | 普查证据 | SOP 机制（位置） | 归类 |
|---|---|---|---|---|
| 1 | MAGMOM 未指定 | 2025: 1714 (79%)；2026: **0** | 输入侧 INITIAL_MAGMOM（io.py:548, protocol.py:72，ADR 0024/#151） | **自动规避**（实证） |
| 2 | ZBRENT | 2025: 951/8709；2026: 1 | EDIFF=1e-6 补丁（orchestrator，issue #119）+ 有界重试 → terminal | **自动修复（有界）**，超限转检测需人工 |
| 3 | 离子未收敛 60% | 2025: 519/859（388 含 ZBRENT） | force gate + CONTCAR 续算 ≤5 + stall 检测（orchestrator:1473-1563） | **自动重试（有界）**，超限检测需人工；残余：收敛率仍低 |
| 4 | SIGKILL/OOM 重试风暴 | 2026: 1339 日志；2025: band 274 次 | 瞬态失败每循环重提（**无上限**）；frozen_job 诊断+auto_heal 休眠（见 #A1） | **仍会出现** |
| 5 | BRMIX | 2025: 706 (33%) | 无专门处理；auto_heal scf_no_converge 未接入生产 | **仍会出现** |
| 6 | NELM 耗尽 | 2025: 46 | ADR 0016 电子门（convergence.py:61-95，只认最后离子步，全文件扫描+mtime 缓存） | **检测需人工**（确定性拒绝，不重算） |
| 7 | POTCAR 物种不匹配 | 2025: 17（Be 输入 bug） | 生成时无 POTCAR↔POSCAR 校验；输入 restore 只按物种补 | **仍会出现** |
| 8 | perfect_missing | 2025: 4 体系 | analyze_status skip_reason 门 | **检测需人工** |
| 9 | CPD duplicate 组成 | 2026: 2 体系 | cpd.py:442-488 拒绝门（ValueError） | **检测需人工**（保多晶型取舍是 scope 决策，正确设计） |
| 10 | CPD preflight 缺输出 | 2026: 6 体系 | truncated→vasp_crash 重提（orchestrator:456-522） | **部分自动修复**（截断自动重提；缺 CONTCAR 需人工） |
| 11 | stale `.failed` 标记 | 2026: 79/80 | CPD 门控路径 mtime 感知（system.py `_failed_newer_than_output`）；其余路径仅展示层读取（report.py:72） | **已处理（门控路径）**；展示残留 |
| 12 | kpar/tetrahedron | 两树近零 | 无专门机制 | 无覆盖但无实害（低优先级） |
| 13 | stage1/stage2 未走完 | 2026 正常 | `_stage2_pending` 自动补 + wave3 硬门（ADR 0025） | **自动修复** |
| 14 | 宿主身份/参考相错 | 存量 2 体系重建中 | config.py 按 energy_above_hull 排序 + StructureMatcher（ADR 0023） | 未来**自动规避**；存量**检测需人工**（全量重建） |
| 15 | 运行期自旋漂移 | 未直接测量 | 无运行期检测（仅输入侧防塌缩） | **仍会出现** |
| 16 | CONTCAR 损坏/部分 | 未见直接证据 | restart_from_contcar 直接复制，无完整性校验 | **仍会出现**（风险项） |

## 3. 关键事实复核（本次审计人工验证）

1. **`auto_heal` 是死代码**：`vasp_sop/defect/compute.py::run_vasp` 是 `apply_correction` 唯一调用链入口，而 `run_vasp` 全仓**零调用**（只有 `defect/__init__.py:17` 导出）。生产路径 `core/orchestrator.py` 不经过它 → errors.py 的 12 模式诊断 + auto_heal 的 5 类参数修复（frozen_job/positive_energy/scf_no_converge/edwav/brion_error）在生产全部休眠。
2. **NELM 门只认最后离子步是设计**（convergence.py:61-95，ADR 0016）：早期离子步的 NELM 警告不污染最终力/能量；全文件扫描（警告可远在 EOF 前）+ mtime 缓存。2025 的 46 个 increasing NELM 目录中只有末步命中者会被拒绝。
3. **stale `.failed` 已被 CPD 门控路径正确处理**（system.py:322-339 `_failed_newer_than_output`：标记比所有输出旧 = 陈旧，不阻挡）；缺陷/UC 路径不读 `.failed` 作门控。残留影响仅在报告展示层。
4. **MAGMOM 输入侧修复实证**：2025 树 79% OUTCAR 含 "did not specify the initial magnetic moment" 告警；2026 树全树该串**零命中**。
5. **2026 batch_run.log 混树**是 unified loop（ADR 0009）的预期行为——单日志写在第一个 batch root 下，同时服务多个 root；属文档缺口而非 bug。

## 4. 差距清单（→ docs/next-actions.md）

| # | 差距 | 证据 | 候选修复 |
|---|---|---|---|
| A1 | **auto_heal 休眠**：5 类参数修复未接入生产循环 | 人工复核 #3.1 | 接入 orchestrator 的 failure-class 路径（frozen_job/EDDDAV 诊断 OOM 冻结、scf_no_converge、brion_error），或明确删除死代码 |
| A2 | **瞬态重试无上限**：持久性资源失败（OOM）导致无界重试风暴 | 2025 band 274 次；2026 cpd 243 次 | 按目录累计失败次数上限 + EXIT_CODE 255/signal 9 → 识别为资源类 → 停止自动重提、进 blockers 需人工 |
| A3 | **POTCAR↔POSCAR 物种数生成时校验** | 2025 Be 缺陷 13 目录全废 | build_all 后校验 POTCAR 头物种数 == POSCAR 物种数 |
| A4 | **运行期自旋漂移无检测** | 输入侧已证有效（2026 零告警） | 收敛后对比预期磁态（MAGMOM 输入 vs OUTCAR 末磁矩）告警 |
| A5 | **BRMIX 无专门处理** | 2025: 706 目录 | 明确归入泛化重试或增加混合参数修复 |
| A6 | **CONTCAR 完整性校验** | restart_from_contcar 直接复制 | 复制前校验原子数/坐标合法性 |
| A7 | **stale `.failed` 展示误导** | 2026: 79/80 | 展示层按 mtime 感知（复用 `_failed_newer_than_output`） |

低优先级：kpar/tetrahedron 专门机制（两树近零实害）；4+ 元素 CPD 建图（pydefect 限制，已有半覆盖）。

## 5. 方法学备注（供后续普查复用）

- 逐模式 `grep -l` 循环会被 timeout 静默截断，产出偏低假数字（ZBRENT 曾误报 367，实为 951）——应单趟多模式扫描。
- 「未收敛」这类无专属错误串的状态，用**成功标记的缺失**测量更可靠，但必须先按计算类型分层（静态 band/dos/dielectric 本就不打印 `reached required accuracy`），否则大幅高估。
- 纯 `NELM` 关键词绝大多数是 `NELMDL` 噪声；真信号是 `increasing NELM`。
- `.failed` / `crisp_failed` 计数是**重试痕迹**，不是当前失败数——以"最新作业日志是否 CRISP_COMPLETED"判存活。
