---
name: defect-incar-parity-regenerate
description: 审计 vasp-sop defect INCAR 参数完整性（NSW/SIGMA/LORBIT/+U）并全量重生成。当 INCAR 参数疑似回滚（vise API 分支漏传）、需要改 defect 计算参数后重跑、或收敛率异常低（NSW=20 restart 循环）时使用。
---

# Defect INCAR 参数完整性审计与批量重生成

## 背景（2026-08-09 实战）

vasp-sop 的 defect INCAR 有两条生成路径：
- **CLI 分支**（`vise vs -uis "NSW 50 {extra_uis} {encut}"`）——参数齐全
- **API 分支**（`vasp_sop/vasp/io.py::_prepare_inputs_vise_api`，charge 非 None 时走此路——**所有 defect 目录**）——NELECT 修复（vise API 传 charge）引入时**漏传参数**，vise 模板默认值生效：
  - NSW 50→**20**（restart 循环根源，实测收敛率 2%：201 完成仅 4 收敛）
  - extra_uis（SIGMA 0.02 LORBIT 11）**从未生效**（实际 SIGMA 0.1/LORBIT 10）
  - hubbard_u（+U）**丢失**（Gd 体系无 LDAU，物理结果错）

## 审计方法

1. **INCAR 参数对比**（每个 defect 目录）：
   ```python
   import re
   txt = open(f"{d}/INCAR").read()
   for k in ("NSW","SIGMA","LORBIT","LDAU","ENCUT","NELECT","ISPIN"):
       m = re.search(rf"^{k}\s*=\s*(\S+)", txt, re.M)
   ```
2. **+U 检查**：plan.yaml `parameters.hubbard_u=True` 的体系 INCAR 必须有 `LDAU = True`（Gd2GaSbO7:Bi/Fe 掺杂体系）
3. **POTCAR 变体验证**：`grep TITEL POTCAR` 对比 plan.yaml `parameters.pp`（vise 默认变体通常匹配 Ca_pv/Ba_sv/Gd_3/Ga_d，但要验证）
4. **NELECT**：用 vasp-sop 权威审计 `verify_nelect(defect_root, config)`（builder.py），0 问题才合格

## 批量重生成（幂等脚本）

```python
# /tmp/regenerate_incar_full.py 模式（ProcessPoolExecutor 6 worker）
prepare_inputs(d, config, kspacing=0.1, task_type="defect",
               extra_uis="SIGMA 0.02 LORBIT 11", charge=q)
```
- 原始 NSW=20 INCAR 备份为 `INCAR.nsw20.bak`（只建一次）
- 删 INCAR 强制重生成（prepare_inputs 的 input_ready 会跳过已有目录）
- 两棵根并行跑：2026 ~1366 目录 / 2025 ~1511 目录，约 15-20 分钟
- 重生成后必跑 verify_nelect 全量确认

## 修复代码（io.py 已含）

`_prepare_inputs_vise_api` 必须镜像 CLI 分支：
- `CategorizedInputOptions(..., set_hubbard_u=config.hubbard_u, cutoff_energy=config.encut)`
- `VaspInputFiles(options, overridden_incar_settings={"NSW": "100", ...extra_uis 解析})`
- `patch_incar` 兜底（vise 版本漂移）

## 陷阱

- `'Ca'.isupper()` 返回 False（Python isupper 要求全 cased 大写）——POSCAR 物种行判定用 `tok.isalpha() and len(tok) <= 2 and tok[0].isupper()`（排除 Direct 等）
- crisp 的 completed 记录被 daemon trim 到 200 条——统计完成数用**磁盘 OUTCAR 尾部 "reached required accuracy"** 判定
- SQLite complete_time 是 `T` 分隔格式——与 `datetime('now')`（空格）比较要 `strftime('%Y-%m-%dT%H:%M:%S','now',...)` 或替换字符，否则字符串比较恒真/恒假
