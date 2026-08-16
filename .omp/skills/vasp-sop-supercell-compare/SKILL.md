---
name: vasp-sop-supercell-compare
description: 对比 doped 扩胞 vs 现有超胞：用 vasp-sop 现成 _build_supercell_doped（不传 min_atoms、不用 build_all），并行+实时进度
---

# 超胞技术路线对比（doped vs 现有）

## 何时用
用户问"多少体系的超胞跟当前技术路线不同"、评估 doped/pydefect 扩胞差异、或验证某体系重扩是否改变超胞时。

## 关键前提（踩过的坑）
- **对比必须用 vasp-sop 现成代码路径**，不能自己手写 `get_ideal_supercell_matrix` 调用参数——现成 builder 只传 `min_image_distance`（不传 `min_atoms`），手写时传了 `min_atoms=200` 会让 doped 搜出不同矩阵，结论全错（50 体系误报 49 异）。
- **只用 `_build_supercell_doped`，不要用 `build_all`**：`build_all` 会重新生成所有 defect 目录的 VASP 输入（几百个，极慢，10 分钟+ 超时）；`_build_supercell_doped(defect_root, uc_contcar, config)` 只扩胞+写 supercell_info.json（单体系 1-50s）。
- 现有超胞记录在 `<root>/<sys>/defect/supercell_info.json`，含 `unitcell_structure`（基胞）、`transformation_matrix`、`structure.sites`（超胞原子数）。

## 流程
1. 复制每个体系的 `plan.yaml` + `cpd/<target>`（CONTCAR/POSCAR）到临时目录，`defect/` 建空目录——**不复制 defect/**（省时）
2. 用 `System.target_dir` 逻辑找 target：plan.yaml 的 `poscar_src: MP mp-XXXX` → cpd 目录里名字以 `mp-XXXX` 结尾的子目录
3. 调 `_build_supercell_doped(defect_root, uc_contcar, config)`（uc_contcar 优先 CONTCAR）
4. 读新 `supercell_info.json`，与现有比较：`transformation_matrix` 行列式（体积倍数）+ 原子数
5. `ProcessPoolExecutor(max_workers=8)` 并行 + `as_completed` 实时打印进度（用户明确要求看进度）
6. 对比基准：现有超胞是 pydefect 路线（按 min_atoms 200-600 扩）产物，doped 按 min_distance=10.0 扩——两者系统性不同（2026-08-09 实测 50 体系 28 同 22 异，doped 普遍给出更小超胞 88-144 原子 vs 现有 300-576）

## 参考脚本
`/tmp/compare_buildall.py`（用 `_build_supercell_doped` 版）。复用时改 ROOTS 路径即可。
