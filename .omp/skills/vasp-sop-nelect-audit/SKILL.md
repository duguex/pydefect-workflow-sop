---
name: vasp-sop-nelect-audit
description: vasp-sop 缺陷输入 NELECT 审计与修复流程（正确值计算、verify_nelect、vise API 重建）
---

# vasp-sop 缺陷输入电荷（NELECT）审计与修复流程

用于核查/修复 vasp-sop 缺陷计算目录的 NELECT 正确性（电子数 = ΣNᵢZVALᵢ − q）。

## 何时用
- 新体系加入后、或历史项目（2025/2026 树）怀疑电荷设置错误时
- 输入生成组件改动后回归验证

## 正确值计算
1. 解析目录 POSCAR 组成（物种行 → 原子数）
2. ZVAL 三层解析（优先级）：
   - 目录自身 POTCAR 头（`TITEL` → `ZVAL`，变体后缀剥掉：`Zr_sv`→`Zr`）
   - plan.yaml `parameters.pp` 精确变体（PSP 目录 `/mnt/shared/VASP_POT/POT_GGA_PAW_PBE/<变体>/POTCAR`）
   - 元素名兜底（PSP 下非 GW 变体，键用元素基名——注意 `zv.get(cand.name)` 会因后缀剥除查不到，必须 `zv.get(el)`）
3. q 从目录名尾部正则 `_(-?\d+)$` 解析；`perfect` 或 q=0 → 中性
4. 正确 NELECT = Σ(nᵢ × ZVALᵢ) − q

## 判定规则（重要）
- **中性（q=0）：INCAR 必须没有 NELECT 行**（VASP 默认按 POTCAR ΣZVAL 算，写 0 是错的；写了其他值也是错的）
- **带电（q≠0）：NELECT 必须显式等于正确值**
- 铁证验证：读 `vasprun.xml` 的 `<i name="NELECT">` —— VASP 实际执行值（比 INCAR 更权威）

## 执行步骤
1. 全树审计：遍历 `*/defect/*/`，对比正确值 vs INCAR vs vasprun，生成 CSV（**UTF-8 BOM**，含正确+错误全量，Excel 才不乱码）
2. 修复：对错误目录删旧 INCAR → `prepare_inputs(wd, cfg, kspacing=0.1, task_type="defect", extra_uis="SIGMA 0.02 LORBIT 11", charge=q)`（vise API 路径，逐目录）
3. 校验：`verify_nelect(defect_root, config)`（builder.py）应返回空列表
4. 注意：COMPLETE 体系缺陷目录可能被清理（无 POTCAR）→ 走 plan pp / 元素兜底；plan pp 可能为空（清理连带）

## 坑
- 永远不要自写 NELECT 逻辑（vise `IncarSettingsGenerator._nelect` 已正确实现）
- 拷贝优化（宿主 INCAR 复制到缺陷目录）已删除——检查新代码不要重新引入
- 校验失败必须 raise 阻止提交（电荷错误是静默杀手：VASP 照跑、结果全错）
- 已完成的审计产物：`/mnt/shared/home/2sidesniddle/vasp/nelect_audit.csv`（全量）与 `nelect_audit_errors.csv`（仅错误）
