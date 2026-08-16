---
name: vasp-incar-drift-audit
description: "Audit whether VASP calculation directories actually ran with the INCAR parameters on disk: compare OUTCAR header echo (lines 13-41, VASP's authoritative re-echo) vs INCAR, check mtime provenance (INCAR newer than OUTCAR = rewritten without rerun), and count ionic/electronic steps reliably via LOOP/LOOP+ timing lines. Use when a system was regenerated/patched mid-batch, after INCAR rewrites, or when asking \"did it actually run with these settings?\""
---

# VASP INCAR drift audit

## When to use
- INCAR 被批次重写（`_generate_vasp_inputs`/prepare_inputs 每轮无条件重写）后，判断已算结果用的是不是新参数
- 用户问"INCAR 设置和实际运行是否一致"
- 重算多次后排查参数漂移污染

## Method
1. **OUTCAR 头部回显是权威**：VASP 会把实际读取的参数回显在 OUTCAR 第 ~13-41 行（`   NELM = 100`、`   SIGMA = 0.1`…）。比对 INCAR vs OUTCAR 头部，不要信 INCAR mtime。
2. **mtime 取证**：`stat -c "%y" OUTCAR INCAR calc_results.json`。INCAR mtime 比 OUTCAR 新 = 参数被重写但没重跑（漂移）。crisp 拉回产物会刷新 calc_results mtime——mtime 新不代表是新算的。
3. **正则**：`^\s*KEY\s*=\s*(\S+)`（INCAR 与 OUTCAR 头部同模式）。

## Energy provenance checks
- `calc_results.json` 的 energy = pymatgen `Outcar(...).final_energy` = **最后一行 sigma->0**，不是 TOTEN。用 TOTEN 对比会差几 meV（smearing 正常差）——别误报。
- vasprun 最后 `<calculation>` 的 `e_fr_energy` = TOTEN、`e_wo_entrp` = sigma->0。

## Step counting（别踩 F= 陷阱）
- `grep "F="` 会误匹配 `NGXF=` 等行——**离子步/电子步计数全不可靠**。
- 正确标记：`LOOP:` = 一次电子迭代，`LOOP+:` = 一次离子步。awk 流式：`/^ *LOOP:/{e++} /LOOP\+:/{i++; if(e>mx)mx=e; e=0}`。
- 电子迭代计时行是 `EDDAV:`（每迭代一行），不是 RMM:/DAV:（不存在于该格式）。

## Known zero-impact findings (insulator)
- SIGMA 0.1→0.02、LORBIT 10→11、NSW 20→100 对收敛能量影响 < 1e-7 eV（sigma->0 外推本就无展宽；实测 CaAl4O7 perfect 差 3e-8 eV）。漂移 ≠ 数值污染，但要证明。

## Caution
- 用户偏好：**只检查不改**，改动（含重算）必须先确认。找到漂移先报告，等指示。
