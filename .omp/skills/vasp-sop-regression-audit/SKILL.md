---
name: vasp-sop-regression-audit
description: "系统性审计 vasp-sop 管线退化/参数回滚：旧脚本 pydefect_logic.py 参照比对、plan 字段覆盖扫描、POTCAR 变体检查、vise CLI vs API 参数差异。用户问\"还有哪些退化/回滚/配置问题\"时使用。"
---

# vasp-sop 系统性退化审计

用户问"检查还有哪些功能/设置退化了"时，按四个维度审计（2026-08-10 实证有效）。

## 1. plan 字段覆盖审计

全量扫 52 体系 plan.yaml，统计每个字段的值分布，找缺省/异常：

```python
import os, yaml
from collections import defaultdict
fields = defaultdict(list)
for root in ("2026_undergo_spin_defect", "2025_undergo_spin_defect"):
    for sys_dir in sorted(os.listdir(f"/mnt/shared/home/2sidesniddle/vasp/{root}")):
        plan = f"/mnt/shared/home/2sidesniddle/vasp/{root}/{sys_dir}/plan.yaml"
        if not os.path.isfile(plan): continue
        d = yaml.safe_load(open(plan))
        proj, params, sc = d.get("project", {}), d.get("parameters", {}), d.get("supercell", {})
        # 检查: poscar_src/dopant_elements/functional/encut/hubbard_u/pp/soc/interstitial/complex_defect/remote/supercell_tool/scope
```

关注点：`hubbard_u`（已废弃 ADR 0012——永远打开）、`soc`（Bi 体系应开）、`pp=[]`（POTCAR 变体缺失）、`supercell_tool` 缺省。

## 2. 旧脚本参照比对

`/home/duguex/vasp_sop/pydefect_logic.py`（早期手写脚本，标注"仅供参考"）是 CLI 分支参数的**权威参照**。逐项比对：

| 维度 | 旧脚本（参照） | 新管线 |
|---|---|---|
| defect INCAR | `-uis NSW 50 SIGMA 0.02 LORBIT 11 --options set_hubbard_u True` | API 分支必须等价（NSW 100 是有意偏差；SIGMA/LORBIT/+U 已修） |
| CPD 流程 | mp 拉相→分子修正（Cl2/O2/F2 硬编码）→sre→cv 0.01eV 循环→pc | 保留 ✓ |
| 后处理序列 | cr→efnv→dsi→dvf→pbes→beoi→bes→dei→des→cs→pe | 保留 ✓ |
| interstitial/complex/remote | False/1/5 | plan 默认一致 ✓ |

## 3. vise CLI vs API 参数差异（核心退化面）

API 分支（`_prepare_inputs_vise_api`，io.py）曾漏传：NSW（50→20）、extra_uis（SIGMA/LORBIT 从未生效）、hubbard_u（+U 丢失）、cutoff_energy。已修复（`overridden_incar_settings` + `set_hubbard_u=True` 无条件 + `cutoff_energy`）。**审计方法**：对同一目录对比 CLI 分支生成的 INCAR vs API 分支——逐 tag 比对（NSW/SIGMA/LORBIT/LDAU/LDAUU/ENCUT/ISPIN）。

**ISPIN 教训**：defect 的 ISPIN=2 是 vise 模板默认——**不要**按元素显式加 ISPIN（曾自作主张加 `_needs_spin_polarized`，被用户纠正撤销）。+U 永远打开（ADR 0012），vise 自动适配。

## 4. POTCAR 变体检查

`plan["parameters"]["pp"]` 为空的体系用默认 POTCAR——检查 PSP 库是否有更好变体：

```python
from vasp_sop.materials.mp import list_potcar_variants
list_potcar_variants("MoS2", [])  # {'Mo': ['Mo', 'Mo_pv', 'Mo_sv', ...], 'S': [...]}
```

2026-08-10 发现：MoS2（Mo→Mo_sv 可用未用）、CaO（Ca→Ca_sv 可用未用，同族 CaAl4O7 用了 Ca_pv——不一致）。ZnO 的 Zn_sv_GW 不适用（无退化）。同族体系 pp 一致性是审计要点。

## 输出

给用户：已修复清单 / 确认无退化项（附旧脚本参照）/ 新发现的退化候选（每项给体系 + 证据 + 修复成本）。修复决策留给用户（物理精度决策如 +U/POTCAR/SOC 必须用户裁决）。
