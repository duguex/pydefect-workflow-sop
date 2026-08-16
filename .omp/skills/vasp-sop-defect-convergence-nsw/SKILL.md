---
name: vasp-sop-defect-convergence-nsw
description: "诊断 vasp-sop 缺陷计算低收敛率（作业完成但 OUTCAR 无 \"reached required accuracy\"）并用 NSW=100 重生成 INCAR 修复。当收敛率 10%、作业反复 restart、或用户问\"为什么跑这么久没完成\"时使用。"
---

# 缺陷计算低收敛率诊断（NSW=20 陷阱）

## 症状
- crisp 作业大量 completed（VASP 正常退出）但磁盘 OUTCAR 无 `reached required accuracy`
- 收敛率极低：实测 201 个完成仅 4 个收敛（2%）
- 作业反复 restart（ISTART=1 + CONTCAR），每轮只推进 20 离子步

## 根因
vise 的 `Task.nsw` 属性硬编码：`20 if is_atom_relaxed else 1`（`libs/vise/vise/input_set/incar_settings_generator.py`）。`_prepare_inputs_vise_api`（vasp_sop/vasp/io.py）用 vise API 生成 defect INCAR 时未覆盖 → NSW=20。20 离子步不收敛（如 force 0.239 vs EDIFFG 0.03）→ VASP 正常退出（退出码 0）→ crisp 标 completed → vasp-sop restart 机制重提 → 每作业 3-5 轮。

## 诊断步骤
1. 收敛率：`sqlite3 ~/.crisp/data/agent.db "SELECT local_dir FROM jobs WHERE status='completed' AND complete_time >= strftime('%Y-%m-%dT%H:%M:%S','now','-2 hours')"` → 逐个读 OUTCAR 尾部 64KB 找 `reached required accuracy`
2. 确认 INCAR：`grep ^NSW <dir>/INCAR`（=20 即中招）
3. **排除 NELECT**（勿跳过）：`vasp-sop` 的 `verify_nelect(defect_root, config)` 权威审计——拼接 POTCAR 解析必须用 vasp-sop 的 `_potcar_zvals`（按 TITEL 分段），自己写正则只取第一个 ZVAL 会误报全错

## 修复（2026-08-09 已验证）
1. `vasp_sop/vasp/io.py::_prepare_inputs_vise_api`：`vif.create_input_files()` 后加 `patch_incar(work_dir, NSW=100)`（vise 模板不覆盖不了 patch）
2. 全树重生成（备份旧 INCAR 为 `INCAR.nsw20.bak` → 删 INCAR → 重跑 `_prepare_inputs_vise_api`，charge 从目录名 `_(-?\d+)$` 提取，q=0 无 NELECT 行）——参考 `/tmp/regenerate_incar_2026.py`（ProcessPoolExecutor 6 worker，~1300 目录 15 分钟）
3. 重生成后 `verify_nelect` 必须 0 问题（vise API 自动算 NELECT，保持正确）
4. 在跑/排队作业不打断：当前轮旧 INCAR，restart 轮自动吃新 INCAR

## 坑
- `'Ca'.isupper()` 返回 False（str.isupper 要求所有 cased 字符大写）——POSCAR 元素行判定用 `tok.isalpha() and len(tok) <= 2 and tok[0].isupper()`（排除 "Direct"）
- crisp agent.db 的 `complete_time` 是 `T` 分隔格式，`datetime('now')` 是空格格式——字符串比较直接失效（`'T' > ' '` 恒真），必须 `strftime('%Y-%m-%dT%H:%M:%S','now',...)`
- daemon 会把 completed 记录 trim 到 200 条——"完成数"要按磁盘 OUTCAR 判定，别信 DB 计数
