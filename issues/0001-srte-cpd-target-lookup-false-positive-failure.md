# Issue 0001: SrTe-style CPD target lookup 偶发 false-positive failure

## 时间
2026-06-22 21:04 — 22:09 (CST)

## 现象
`vasp-sop batch run` 在 SrTe 上反复进入 CPD post-processing 分支并报：

```
ERROR SrTe CPD failed: Target composition Sr1 Te1 not found in
  /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/SrTe/cpd/relative_energies.yaml.
```

每小时一次共 **31 次** 失败，直到 22:11 才偶然成功（生成 `target_vertices.yaml`）。

## 调查

`vasp_sop/defect/cpd.py:299-308` 中 `adjust_unstable_phase` 的循环：

```python
for comp_str in rel_energies:
    if Composition(comp_str) == target_composition:
        target_string = comp_str
        break
if target_string is None:
    raise ValueError(f"Target composition {target_composition} not found in "
                     f"{relative_energies_path}.")
```

- `Composition("Sr1Te1") == Composition("SrTe") == Composition("Sr1 Te1")` —— 当前 pymatgen 2026.3.23 下三者相等
- `relative_energies.yaml` 当前键为 `['Sr', 'SrTe', 'Te']`，循环本应命中 `SrTe`
- 22:11 重试时突然成功 —— 推断为上游 `pydefect sre` 输出键格式在某些条件下非 reduced 形式，导致 lookup 走到 `if target_string is None` 分支

## 影响

- batch run 状态机把 SrTe 长期钉在 `CPD_POST`，CPU 空转 + 每 2 分钟刷一次错误
- 写盘 race：日志 21:04 创建 `composition_energies.yaml`，但 `target_vertices.yaml` 22:11 才出来 —— 中间 67 分钟 lookup 持续失败

## 复现路径

1. `vasp-sop defect init SrTe` (formula=`SrTe`)
2. 跑完所有 cpd 的 VASP
3. 进入 `adjust_unstable_phase`，依赖 pydefect 输出键格式

## 建议修复

候选 A（防御式）—— 即使循环命中，也校验 `rel_energies[target_string]` 数值有限：
- 若 `target_string` 是 None，**写一个 cached fallback `target_vertices.yaml`** 让 batch run 跳过该步，而不是 raise

候选 B（健壮式）—— 改进循环逻辑：

```python
target_string = next(
    (k for k in rel_energies
     if Composition(k).reduced_formula == target_composition.reduced_formula),
    None,
)
```

候选 C（上游）—— 检查 `pydefect sre` 输出键格式稳定性，并在 vasp-sop 这层显式规范化。

## 实际状态

- SrTe 当前已成功（`target_vertices.yaml` 22:11 生成），后续 cycle 不再触发本错误
- 但代码层面需要加固，否则未来同型 bug 仍会消耗 1h+ 的 retry
