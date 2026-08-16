---
name: defect-eform-structure-reference-mismatch
description: "诊断 vasp-sop 缺陷形成能深负的结构参考态错配：等组成反位对反应红信号、canonical 同源结构对照、旧弛豫链能量断层判别（结构整体替换 vs 电子参数）。用于\"形成能深负但执行条件全一致\"类问题，当 mechanical checks 全过仍不物理时。"
---

# 缺陷形成能深负：结构参考态错配诊断

2026-08-13 实证（Y2Ti2O7/La2SrSc2O7，2026 batch）。当"执行条件全一致"（OUTCAR 回显逐字段相同、POTCAR/KPOINTS/NELECT 正确、电子自洽收敛、磁矩合理/非磁）但形成能仍深负 −6~−11 eV，甚至 U=0/SOC 对照后残留时，先查结构参考态/超胞尺寸/晶格应力，不要停留在 U/化学势假设。

本技能是深负形成能的**结构侧诊断管线**:普查筛出系统性异常 → 等组成反位对消热力学项 → U=0 单变量对照 → 三层结构来源分离 + 历史定位 → 能量断层判别(结构替换铁证) → 尺寸/应力控制 → canonical 同源裁决实验。

## 0. 先筛：批次弛豫能量普查 + 周期畸变普查(deception check, 一遍成本)

在搭任何对照前, 先跑批次**弛豫能量普查**——它已能分离体系:
- 每 dir 对每个 `*.log` 解析离子步 `F=` 行(`^\s*(\d+)\s+F=\s*([-+]?\d*\.\d+(?:E[-+]?\d+)?)`), 计算 run 内 `last_F - first_F`(同协议 run 内弛豫), 每 dir 取**最负**者(=最接近"从理想缺陷几何出发")。
- 每体系取中位数: 某体系缺陷弛豫 4–10 eV 而其余体系 <2.3 eV = 重构异常体(Y2Ti2O7: −6.17 中位, 深 −4.9…−6.7; 其余 −0.45…−2.3)。
- 交叉核对 per-dir 畸变(defect_entry.json → CONTCAR, 物种保守周期指派): 看集体运动(RMS>0.5 Å, >1 Å 原子数几十个)。
- 排除 −1000+ eV 的荒谬值 = 结构替换污染; 排除未收敛目录(无 reached accuracy, CONTCAR 是中间态, 能量不可信但结构信息可用)。
- 周期畸变量化(defect-periodic-distortion-census): 匹配规则 = 每化学物种独立做完整周期距离矩阵 `initial.lattice.get_all_distances(initial.frac_coords[i0], final.frac_coords[i1])` + `scipy.optimize.linear_sum_assignment` 求解, 各物种分配距离合并——物种限制匹配防止把替换当原子运动, 容忍位点重排。指标: RMS / mean / p95 / max / >0.5 / >1.0 / >2.0 Å 计数; 同样方法比 canonical 宿主 vs 弛豫 perfect(区分缺陷局域弛豫 vs 宿主本身进了另一结构谷)。判读: 集体重构 = RMS 0.5–1 Å + 大量 >1 Å 原子且跨缺陷重复; 局域弛豫 = 中等 RMS 仅一两个大位移; 无结构解释 = RMS<0.1 Å 且无 >1 Å 原子 → 优先查电荷/磁态/电子占据/参考兼容。深负缺陷要跟同体系全缺陷 RMS 基线比(软晶格处处中等位移不等于解释深负)。

## 1. 快速红信号：等组成反位对反应

选一对方向相反、净电荷为零的反位(如 Ti_Y^+1 与 Y_Ti^-1, 或中性 q=0 反位——优先中性缺陷先看裸 `E_diff=E_defect−E_perfect`):

```
E_pair = E(Ti_Y+1) + E(Y_Ti-1) − 2E_perfect
```

- 原子数守恒、电荷抵消 → 化学势项与 q·VBM 严格消除(不依赖化学势后处理的裁决量, 比单个 E_diff 更可靠)
- **E_pair 为深负(如 −13.8 eV)→ 深负载体在裸总能差 E_def − E_perfect, 与化学势/VBM/U 无关**
- 若 U=0 对照下 E_pair 仍深负 → 彻底排除 Hubbard U 单一原因
- 化学势污染通常只造成 ~0.1–0.3 eV/atom 偏移, 解释不了消热力学项后仍有 ~10 eV 的负反应能

## 2. 单变量电子参数对照(U=0)

从各生产目录当前收敛 CONTCAR 建独立单点目录, 保持 POTCAR/KPOINTS/SOC/电荷相同, 仅切换目标参数(如 U=4→U=0), 并设 NSW=0、IBRION=-1、ISTART=0。**必须包含 matched perfect**。比较单缺陷 E_diff 和 E_pair: 若 E_pair 在两套协议都深负 → 该参数不是统一根因; 若某单方向缺陷大幅转正但配对仍负 → U 改变电子结构但非统一解释。

## 3. 三层结构来源分离 + 生成态验证 + 历史定位

将结构来源视为**独立对象**:
1. **生成态**: `defect_entry.json` 的 structure(pydefect 初始生成)
2. **canonical 宿主**: `defect/supercell_info.json` 的 structure(唯一不可变参考)
3. **弛豫态**: 目录 CONTCAR/POSCAR(可能被替换/污染)
4. **历史**: baseline/manual/recovery git 快照中的同名文件

诊断比较(全部元素匿名化后做周期 StructureMatcher 匹配, 或最小费用周期距离匹配量化 RMS/max/p95/>1Å 原子数):
- `defect_entry.json` vs `supercell_info.json`: 匿名位置集合 RMS ≈ 0 → 生成阶段正确(正常单反位应与 canonical 共享同一宿主位置集合, 只改变一个位点元素)
- `CONTCAR` vs `supercell_info.json`: 匿名 RMS 0.7–0.8 Å + StructureMatcher fit=False → **弛豫/提交阶段的宿主位置集合不同**(子格置换级别差异,RMS 数值会误导——必用 StructureMatcher 终审); 十几个位置偏移 >1 Å = 不是同一结构参考态
- 不要因第 3 层与第 1 层不同就推断生成器 bug。
- **历史定位**(git): baseline 已错配 → 根因在初始缺陷生成/超胞对应; baseline 正常而 recovery 后错配 → 根因在几何恢复或 stage2 覆盖。`git show <commit>:defect/<dir>/{POSCAR,CONTCAR}` 与当前逐位对比(rms≈0 = 从头到尾就是被替换结构)。

## 4. 能量断层判别(结构整体替换 vs 电子参数)

同一目录新旧两代弛豫日志的能量跳跃:
- U=4 效应 ≈ 0.1–0.5 eV（典型量级）；**跳变 48–58 eV = 结构被整体替换**(如 −813 → −754, perfect)。排除归因语境中 U 上限取 ~1–2 eV：48–58 eV 跳变远大于 SOC（≈0.8 eV/88 原子）与 U 的任何量级，排除二者
- 判别顺序:
  1. NELECT:BRMIX "old charge density: X new Y" 两代相同 → 排除电荷错误
  2. POTCAR TITEL/ZVAL 相同 → 排除势错误
  3. 晶格相同 → 排除晶胞替换
  4. **DAV:1 初始能量就不同(如 +7113 vs +7280)→ 初始结构不同, 第一迭代即分叉 = "输入被替换"而非"弛豫路径不同"的决定性证据**
  5. StructureMatcher(ltol=0.3, stol=0.5) fit(anonymize(canonical), anonymize(prod)) = False → 实锤
- 两代 log 并存 ≠ 结构被替换(08-10 含设计内续算重置); 必须用能量断层/DAV:1 分叉确认。
- OUTCAR 被 crisp fetch 截断 → 能量权威源是 slurm log `F=` 行。
- 3 分量 mag = SOC; 1 分量 = 无 SOC——对比断层时确保两代协议同类。

**扫描受影响范围**: 信号 = 目录内 08-09 era 与 08-11/08-12 era slurm id 分段(slurm id: 20xxxx/208xxx≈08-09, 2396xxx≈08-11, 1107xxx/1111xxx≈08-12)log 并存 + `INCAR.nsw20.bak`/`POSCAR.bak` 存在。2026 批次实测两代并存目录: Y2Ti2O7 95、Gd2GaSbO7:Bi 36、CaAl4O7 27、SrAl4O7 24、BaAl2B2O7 17、La2SrSc2O7 12、La2Zr2O7 8、Y2Sn2O7 7(BaAl4O7/SrGa4O7:Fe 各 1); 非 SOC 体系同样中招(重生成批次全部体系统一跑)。

## 5. 尺寸/应力控制(深负在 12Å/ISIF=3 下是否存活)

### 5a. 12 Å doped 重建对照(defect-deep-negative-size-and-stress-diagnosis)
同一不可变 canonical 宿主, 只重新装包:
1. 生产树外 worktree: 复制 plan.yaml, 设 `supercell: {tool: doped, min_distance: 12.0}`; `cpd/<target>/CONTCAR` 作 unit cell; 清空 `defect/`。
2. `_build_supercell_doped(defect_root, uc_contcar, config)`(来自 `vasp_sop.defect.builder`), **绝不 `build_all`**。Y2Ti2O7: 88→176 原子, 10.08→14.4 Å。
3. 从新 `supercell_info.json` 宿主构建: perfect 拷贝 + 同宿主配对反位(电荷 q 与 −q 使 reservoir/VBM/chem-pot 抵消), `NELECT = ΣZVAL − q`(Y_sv=11, Ti=4, O=6)。
4. Stage-1 无 SOC 弛豫(NSW=100, NELM=100, 保留 U), `crisp submit duguex_5 --skip-prefill`。
5. 读: perfect 必须快速收敛(−1512.345 eV, RMS<0.01 Å); 缺陷常 NSW 耗尽无离子收敛——不要把其能量当最终值, **其畸变轨迹才是信号**。

### 5b. ISIF=3 应力控制(small-cell-stress-reconstruction-test)
固定初始晶格本身是否携带可剥应力驱动重构:
1. 从不可变 canonical 宿主(不是弛豫缺陷 CONTCAR)以 `ISIF=3` 弛豫(NSW=100, NELM=100, 同 U/POTCAR/KPOINTS, 无 SOC), 要求 reached accuracy + 电子/离子收敛; 记录相对 canonical 的晶格/体积变化(Y2Ti2O7: 体积 −2.87%, a/b −1.07%, c −0.75% = 真实应力存在)。
2. 从收敛 ISIF=3 perfect 直接生成互补缺陷(保留弛豫晶格, POSCAR 物种块按 POTCAR 序重排), 缺陷以固定晶格 `ISIF=2` 跑, matched NELECT, `--skip-prefill`。
3. 离子收敛后才比: 最终力、周期物种保持位移指标、单 `E_def−E_perfect`、无 reservoir 配对反应。
4. 判读: 集体位移大幅下降 + 配对反应强烈上移 = 宿主应力是重构贡献者(Y2Ti2O7: 部分对 −17.8 → −4.6 eV, 一方向转正); 若缺陷仍集体畸变 + 配对仍深负 → 均匀应力不是唯一解释。
5. POSCAR/POTCAR 物种数不匹配导致的提交失败是输入布局错误, 重排物种后重提, 不作物理证据。

### 5c. 判读表
| 观察 | 含义 |
|---|---|
| 12 Å 细胞停止重构, 对 → 正 | 尺寸驱动重构确认 |
| 12 Å 仍重构(RMS>0.5 Å, 无离子收敛) | 内在体系现象 |
| ISIF=3 移除异常 | 初始固定晶格应力是主因 |
| ISIF=3 缓解部分, 深对仍存 | 应力是贡献者非唯一因 |
| 全体系弛豫普查 ~5+ eV 而 peer <2.3 eV | 体系级深谷问题; 建议从单一 canonical 宿主重建整棵缺陷树 |

## 6. canonical 同源对照实验(裁决)

从 supercell_info 的不可变结构出发(替换位点取 `sites` 的 equivalent_atoms[0]):
- perfect(不变)
- Ti_Y: index Y 位点替换为 Ti
- Y_Ti: index Ti 位点替换为 Y
- 同一 U/SOC/NSW=0 SP, `--skip-prefill` 防缓存覆盖 POSCAR; 先跑廉价 U=0 SOC NSW=0 单点

```python
# POSCAR 物种必须连续分组: pymatgen replace 后按元素重排再写(POTCAR 序)
ordered = [site for el in ['Y','Ti','O'] for site in s if str(site.specie)==el]
Structure.from_sites(ordered).to(filename='POSCAR', fmt='poscar')
```

判定: 正确结构上 E_pair 恢复正值(+8 eV)vs 旧错配结构 −10.6 eV → 结构参考态错配是深负主因。

**能量闭合核对**(canonical-reconstruction): 每结构算 `E_relaxed − E_canonical`, 验证
`pair_relaxed − pair_canonical = ΔErelax(defect A) + ΔErelax(defect B) − 2ΔErelax(perfect)`。
闭合成立 = 负配对能量由差分结构重构造成。

**解释**: canonical 是正确的局域点缺陷起始参考; 更低能弛豫结构不自动是坏文件——可能是污染路径, 或真实缺陷诱导相重构。用同一协议从 canonical perfect/defects 独立重跑完整离子弛豫、保留每一步; 可复现的整体重构 = 结果不是常规稀缺陷点, 必须按竞争谷/相变分析(如 hole polaron on O-2p + metastable host, e_above_hull +0.6 eV/f.u.)。

## 陷阱
- **执行条件一致 ≠ 结构身份一致**: incar-echo/POTCAR/NELECT 检查全过仍可全部跑在错误结构上(内部自洽收敛 + CRISP_COMPLETED)
- 匿名 RMS 0.5–0.8 Å 可能掩盖子格置换——StructureMatcher fit 是终审
- git 快照 CONTCAR 只验证"恢复了什么", 不验证"恢复的本来就是对的"; 08-10 类重生成批次替换 POSCAR 可能不被 git 追踪
- **POSCAR 物种分组**: 任何 `Structure.replace()` 后写 POSCAR 必须按 POTCAR 序重排, 否则 VASP `ERROR: number of potentials on File POTCAR incompatible with number of species`(crisp auto-fetch 会把它伪装成真作业失败)
- Perfect 与缺陷必须同协议; 不混 SOC / 无 SOC 能量(stage-1 无 SOC 弛豫 vs SOC SP 差 0.5–1 eV)
- 6138 test 分区短时限(20 min/20 core 会 .timeout)→ 用 duguex_5(8259cl, 48 core)
- vasprun.xml 可能被截断(fetch 切断/写一半)→ ParseError; 权威能量源用 slurm `*.log` F= 行
- 带电缺陷目录命名避开 `+`/`-` 歧义(如 `Ti_Y_q+1`, `Y_Ti_q-1`)
- 结果落盘: `_experiments/<name>/manifest.json`(位点/组成/NELECT/哈希)+ `result.json`

## 2026 batch 已知状态(2026-08-13)
- Y2Ti2O7/La2SrSc2O7 深负根因已实锤为结构参考态错配(08-10 重生成批次替换 POSCAR), 非 U/化学势/解析层
- 正确结构上反位对 +8.0 eV(U=0 SOC SP); 旧错配 −10.6 eV
- 712 缺陷需从 supercell_info.json 全量重建, 现有 CONTCAR 不可复用
- 尺寸/应力控制未解深负 → 结构身份是唯一变量(翻转 18.6 eV)