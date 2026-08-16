---
name: nelect-audit-repair
description: 审计并修复 vasp-sop 缺陷计算 INCAR 的 NELECT（电荷数）错误：从 POTCAR 头读 ZVAL 计算正确值、verify_nelect 校验、vise API 重建。触发：检查/修复缺陷电荷设置、NELECT 审计。
---

# NELECT 审计与修复（vasp-sop 缺陷输入）

## 何时用
检查或修复缺陷计算目录 INCAR 的 NELECT（电子数）设置；或审计全树电荷错误（修复后验证、重算前筛查受影响目录、给用户重算/对照表）。

## 正确性规则（2026-08-08 确立）
- 正确 NELECT = Σ(Nᵢ × ZVALᵢ) − q（Nᵢ 取 POSCAR 第 6-7 行物种计数, VASP5 格式；q 从目录名尾部正则 `_(-?\d+)$` 解析, 如 Va_Se1_-2 → q=-2；perfect → q=0）
- ZVAL 从**该目录 POTCAR 头**读：`TITEL = PAW_PBE <变体名>` 后随的 `ZVAL = x`；键按元素基名（`变体名.split("_")[0]`，如 Ba_sv→Ba、Ca_pv→Ca、Gd_3→Gd）——**禁止硬编码元素表**
- **中性（q=0）不写 NELECT**——VASP 默认 ΣZVAL 即正确；写错值或写 0 都是错
- 带电（q≠0）必须显式 NELECT = ΣNᵢZVALᵢ − q

## ZVAL 三层解析（严格按序）
1. **目录自身 POTCAR**（TITEL 行给变体名如 Zr_sv, 键按 split("_")[0] 归元素）——最精确
2. **plan.yaml `parameters.pp` 精确变体**（如 [Ba_sv, Se]）→ PSP 目录对应变体 POTCAR 的 ZVAL（PSP 根：`/mnt/shared/VASP_POT/POT_GGA_PAW_PBE`）
3. **元素名兜底**：`PSP/<El>/POTCAR`；无简单名则非 GW 变体（跳过 _GW）

坑：不同变体 ZVAL 不同（Ca=10 vs Ca_pv=8；Gd vs Gd_3；Ba vs Ba_sv=10）。2025 树部分体系 plan pp 为空、目录无 POTCAR（被清理），必须用第 3 层。

## 判定
- q=0 且 INCAR 无 NELECT → **正确**（VASP 默认 ΣZVAL 即正确中性电子数；写 0 是错的）
- INCAR NELECT == 正确值 → 正确
- 否则 → 错误；若 vasprun.xml 存在且其 `<i name="NELECT">` ≠ 正确值 → **错误-已污染**（VASP 实际执行了错值，结果不可信）

## 校验（项目已有组件）
```python
from vasp_sop.defect.builder import verify_nelect, verify_inputs
problems = verify_nelect(defect_root, config)  # 返回问题列表
```
`verify_nelect` 即三层解析：目录 POTCAR → plan.yaml pp 变体 → 元素名查 PSP（简单名优先，排除 _GW）。

全树扫描（只读）:
```bash
cd /home/duguex/vasp_sop
.venv/bin/python -c "
from pathlib import Path
from vasp_sop.core.config import PipelineConfig
from vasp_sop.defect.builder import verify_nelect, verify_inputs
for rootname in ('2025_undergo_spin_defect','2026_undergo_spin_defect'):
    root = Path(f'/mnt/shared/home/2sidesniddle/vasp/{rootname}')
    for d in sorted(root.iterdir()):
        df = d/'defect'
        if not df.is_dir(): continue
        cfg = PipelineConfig.from_yaml(d/'plan.yaml', root=d)
        print(d.name, len(verify_nelect(df,cfg)), sum(1 for p in verify_inputs(df,cfg) if '[ERR]' in p))
"
```

## 铁证级验证
- **vasprun.xml** 的 `<i name="NELECT">` = VASP 实际执行值（比 INCAR 可靠；文件可达百 MB, 只读前 200KB）
- **OUTCAR 头部** = 实际 ZVAL（交叉验证 VASP 实际用的势）
- 独立交叉验证：pymatgen `Structure.from_file(POSCAR)` 算组成 + POTCAR 头读 ZVAL, 独立算正确 NELECT 对比 INCAR（不调用 verify 代码）

## 修复流程
1. 先给用户问题清单，确认后再改（用户明确要求：**不得直接改生产数据**；每步修改留痕 CSV 明细/commit）
2. 对每个错误目录：删 INCAR（保留 POSCAR/POTCAR）→ `prepare_inputs(wd, cfg, kspacing=0.1, task_type="defect", extra_uis="SIGMA 0.02 LORBIT 11", charge=q)`（走 vise API 重新生成, q 从目录名 `_(-?\d+)$` 解析）
3. `verify_nelect` 复验，直到 0 问题
4. 重算：输入已变 → crisp 缓存必 miss（identity 含 INCAR/NELECT）→ batch retry + batch run --retry-failed

## 输出 CSV（Excel 兼容）
列：root, system, defect_dir, q, composition, zval_source, correct_nelect, incar_nelect, vasprun_nelect, status, system_state
- **必须写 UTF-8 BOM**（`b"\xef\xbb\xbf" + raw`），否则 Excel 打开中文乱码
- 错误明细：`grep "错误" 全量.csv > errors.csv`（保留表头）

## 陷阱
- **POSCAR 尾部全零行 = 原子速度 0，合法**（N 坐标 + N 速度行）；不是污染；坐标行数 N 或 2N 都正常，<N 或 >2N 才是错
- ZVAL 必须用对变体（Ca_pv=8 ≠ Ca；Gd_3 4f-core ≠ Gd）——用错变体审计值就错
- 已收敛目录可能无 POTCAR（COMPLETE 体系清理）→ verify 用 plan pp/元素兜底；跳过无 POSCAR 的杂项子目录（如 c4v）→ status=无POSCAR 不崩
- 2025 树历史错误模式：宿主固定 NELECT（424/573…）被拷贝到所有缺陷目录，不同 q 相同值=拷贝特征（vise CLI 时代拷贝优化产物 `_generate_vasp_inputs`, 已删除 commit c2a61af, 现为逐目录 vise API 传 charge）
- 全树（~2800 目录）审计约 5 分钟；逐目录读 vasprun 更慢（约 20 分钟），先按 INCAR 判定再对错误目录读 vasprun

## 已知数据文件
- `/mnt/shared/home/2sidesniddle/vasp/nelect_audit.csv`（全量, UTF-8 BOM）
- `nelect_audit_errors.csv`（错误明细）
- `input_audit_status.md`（现状）