---
name: potcar-outcar-batch-audit
description: "以 OUTCAR TITEL 回显为准审计 VASP 批次 POTCAR 赝势种类/变体/版本/顺序一致性：扫描全部计算目录（cpd/defect/unitcell）、规范化元素 token（Ba_sv/Ga_d/Gd_3 后缀）、检出变体混用（如 Ga vs Ga_d）与顺序错位，并用同结构 SOC 单点对比量化影响。用户问\"检查 POTCAR 种类/赝势混用/以 OUTCAR 为准\"时使用。"
---

# POTCAR 批次审计（以 OUTCAR 为准）

## 何时用
- 用户要求"检查 2026 批次 POTCAR 种类 / 赝势是否有混用 / 以 OUTCAR 为准"
- 形成能/能量不可比需先排除赝势基准分裂

## 核心原则
**执行真相 = OUTCAR 头部 TITEL 回显**（VASP 实际加载的 POTCAR），不是盘面 POTCAR 文件。crisp fetch 只截断 OUTCAR 尾部，头部可靠。

## 提取陷阱（实测踩过）
1. TITEL 行有**前导空格** → 必须 `re.search(r"^\s*TITEL\s*=\s*(\S+)\s+(\S+)\s+(\S+)")`，`re.match` 从行首会全漏（0 结果）
2. TITEL 块位置**可超过 200 行**（大 POTCAR 序在 280 行后）→ 扫前 800 行，别截 200
3. 元素 token 带赝势后缀（`Ba_sv`/`Ga_d`/`Gd_3`/`Ca_pv`/`Y_sv`/`Zr_sv`/`Sr_sv`）→ 顺序核对前先规范化 `re.sub(r"_[a-z0-9]+$","",tok)`；POSCAR 第 6 行是无后缀元素名，直接比会全误报
4. OUTCAR 尺寸门 `<4000 bytes` 跳过（可能只是错误 stub）
5. 取 TOTEN 用**最后一次出现**（首次 TOTEN 是中间值，比如 -168 正确值之前可能出现过 +975 的乱值）；`grep -m1` 会拿到错值

## 扫描流程
```python
# 每目录收集: rel, kind(cpd/defect/unitcell), tokens[], poscar_elements
# rglob("OUTCAR") 全树（含 cpd_excluded/子目录/new 等）
TITEL = re.compile(r"^\s*TITEL\s*=\s*(\S+)\s+(\S+)\s+(\S+)")  # groups: 类型, 元素, 版本
```
1. **顺序核对**：`[norm(t) for t in tokens] == poscar_line6` → 错位是真异常（会致命）
2. **变体混用**：每体系×每规范化元素 → Counter(tokens)；`len>1` = 混用（如 Ga 体系 {'Ga':15,'Ga_d':73}）
3. **版本混用**：每(体系, token) → Counter(版本)；`len>1` 异常
4. 跨体系元素→token 总表（惯例参考：Ba_sv/Ca_pv/Sr_sv/Y_sv/Zr_sv/Gd_3/Ga_d/La/Mn/Ti/W/Sb/Fe/B/Al/O 各唯一）

## 影响量化（关键）
发现混用后：**同结构 NSW=0 单点对比**（INCAR/KPOINTS/POSCAR 全同，仅换 POTCAR）：
- 从 unitcell/structure_opt 取收敛 CONTCAR 作 POSCAR；INCAR 用原目录（Gd2GaSbO7:Bi 的已是 SOC NSW=0 单点——正好是链上实际用腿）
- 两种 POTCAR 来源：异常目录盘面 POTCAR vs 同体系 defect/perfect/POTCAR（同元素序）
- `crisp submit --dir /tmp/ga_cmp/X --calculator vasp --skip-prefill`（skip-prefill 防 CONTCAR→POSCAR 覆盖）；两作业几乎同刻完成
- ΔE 实测：Ga vs Ga_d = **+0.297 eV/超胞**（Ga_d 更高）

## 影响路径（形成能链）
- unitcell/{band,dielectric} → VBM/PC 修正参照，混用污染整个修正链
- cpd 竞争相（裸 Ga 相 + 主相裸 Ga vs defect Ga_d）→ 化学势凸包混合基准
- 修复 = 重算异常目录为体系主变体 → 重建 pbes/dielectric/cpd 凸包 → analyze

## 产出
终端汇总表：体系×目录数×主模式×异常清单 + 跨体系元素变体总表 + 量化 ΔE + 修复选项。脚本留存 /tmp/scan_potcar.py（含规整版 scan_potcar2.py 思路）。
