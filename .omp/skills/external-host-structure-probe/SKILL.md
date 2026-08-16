---
name: external-host-structure-probe
description: "When bootstraping a new vasp-sop system whose host formula is not in Materials Project (or needs an external structure), probe external structure sources in order, verify space-group consistency before considering any substitute, and stop at the host-anchor boundary: only the user can supply the real structure. Use when 宿主不在 MP/新体系宿主定锚/需要外部 CIF。"
---

# 外部宿主结构探测（MP 未收录时）

## 适用
新体系 bootstrap，`fetch_formula_polymorphs(formula)` 返回 None（MP 未收录该组成）——宿主定锚需要外部结构。

## 顺序（每个失败才进下一个）
1. `fetch_formula_polymorphs`（MP formula search）——确认无
2. 元素空间查询：`chemsys='Li-Zn-Ge-O'` 类——找最近似相 + 凸包相（E_hull）
3. COD：`https://www.crystallography.net/cod/result.php?formula=<URL编码>&format=csv`
4. OQMD：`http://oqmd.org/materials/composition/<formula>`（301 常见——API `http://oqmd.org/oqmdapi/composition?filter=`）
5. CrossRef：`https://api.crossref.org/works?query.bibliographic=<formula+关键词>` 拿 DOI
6. Semantic Scholar：`https://api.semanticscholar.org/graph/v1/paper/search`
7. 论文全文站（ScienceDirect/RSC/ResearchGate/X-MOL）通常 403/302——不要纠缠

## 关键判定：空间群一致性（几何第一资产）
MP 最近似相**必须**与文献空间群同构才可考虑作骨架：
```python
mp = m.get_structure_by_material_id('mp-xxx')
sg = mp.get_space_group_info()  # e.g. ('P2_1', 4)
# 文献: P4₁32 (No. 213) → P2_1 不同构 → 不可改造
```
**组成相似 ≠ 骨架可用**。空间群/位点占据不同 → 宿主身份错 → 整树作废（ADR 0023 教训）。

## 边界
探测链全部失败 → **停**。宿主是几何第一资产——绝不瞎造（无 Wyckoff 精确占据的构造是伪宿主）。向用户要 CIF/POSCAR（论文补充材料/实验/ICSD），拿到后 bootstrap 全自动续行。

## 附带
- 新 3d 掺杂元素（Cr 等）不在 U_TABLE 时：加 `(3.0, 2)`（与 Mn/Fe 同惯例），两阶段 +U 自动覆盖。
- 文献元数据（空间群/晶格/位点）先记下，供拿到结构后交叉验证。
