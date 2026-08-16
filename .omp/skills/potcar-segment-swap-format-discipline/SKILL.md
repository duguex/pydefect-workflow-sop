---
name: potcar-segment-swap-format-discipline
description: "Swap or rebuild POTCAR segments (e.g. Ga→Ga_d) in VASP calc dirs without corrupting the file: keep verbatim PAW_PBE line-first segment bytes (TITEL=-prefixed reconstruction makes VASP misparse and shifts energies by thousands of eV), rebuild all dirs, verify OUTCAR POTCAR:/TITEL consistency + energy sanity, then rebuild cpd hull + analyze chain. Use when 换赝势/POTCAR 段/统一变体, or after a POTCAR rebuild produced absurd energies or chemical potentials."
---

# POTCAR segment swap discipline (Ga→Ga_d style)

## The trap (measured 2026-08-15, 2026 batch)

VASP POTCAR files have TWO segment-header styles:
- **Line-first (correct, original library format):** `  PAW_PBE Ga_d 06Jul2010` followed by `13.0`, `parameters from PSCTR are:`, SHA256/COPYR/VRHFIN/EATOM header block, then a `TITEL = PAW_PBE ...` line, parameters, data, `End of Dataset`.
- **TITEL=-prefixed (what naive regex splits produce):** segment starts directly at `   TITEL  = PAW_PBE Ga_d ...` with the header block missing.

Reconstructing POTCARs by splitting on `(?=TITEL)` yields TITEL=-prefixed segments. VASP parses them misaligned: energies shift by thousands of eV per atom (measured Ga2O3: −8423 eV vs correct −63.89 eV; Ga metal −8373 vs −13.5). This poisons the CPD convex hull (impurity μ collapses to absurd values like Fe −164), standard_energies, and every formation energy. The defect chain (original-format POTCARs) then no longer shares the energy zero with rebuilt cpd/unitcell dirs.

## 0. POTCAR 块边界(核心陷阱, potcar-block-swap-consistency-audit)

POTCAR 文件结构: 每个势块 = 版权+元数据(VRHFIN/EATOM/LEXCH)+ `TITEL = PAW_PBE Xx` 行 + ... + `End of Dataset`。**块的真正终止是 `End of Dataset`, 不是下一个 TITEL**。TITEL 行**不在块首**——块头元数据在 TITEL 之前。按 TITEL 到下一 TITEL 切割会把下一块的头部(如 `VRHFIN =Sb...`)切进上一块 → 坏 POTCAR → VASP `forrtl: severe (59): list-directed I/O syntax error`。

```python
def split_blocks(text: str) -> dict[str, str]:
    titels = [(m.group(0).split()[-1], m.start())
              for m in re.finditer(r"TITEL\s*=\s*PAW_PBE\s+\S+", text)]
    ends = [m.start() + len("End of Dataset") for m in re.finditer("End of Dataset", text)]
    assert len(titels) == len(ends), f"{len(titels)} titels vs {len(ends)} ends"
    return {name: text[s:e] for (name, s), e in zip(titels, ends)}
```
交换后验证: 每块 `rstrip().endswith("End of Dataset")` + TITEL 集合符合预期 + 无重复。

## Correct rebuild procedure

1. **Extract verbatim segments** from a known-good original POTCAR (per system, e.g. `defect/perfect/POTCAR`):
   ```python
   import re
   segs = {}
   for m in re.finditer(r"^  PAW_PBE (\S+) (\S+).*?End of Dataset", c, re.S | re.M):
       segs[m.group(1)] = m.group(0)   # keeps line-first header + EOD, byte-exact
   ```
   Elements missing from that file (e.g. Fe when perfect has none): take from a defect dir POTCAR of the same era (defect/Fe_Ga1_0/POTCAR).
2. **Rebuild** in the original element order (read the old TITEL sequence), replace the target element's segment (`Ga` → `Ga_d`), join `seg.rstrip() + "\n"` per segment. Verify `TITEL` sequence and `End of Dataset` count == segment count.
3. **Rerun every dir** (crisp submit with ABSOLUTE paths — relative `--dir` fails with "Local directory vanished"), `--skip-prefill` to keep geometry.
4. **Verify each new OUTCAR**: `POTCAR: PAW_PBE Ga_d ...` line must match the `TITEL = PAW_PBE Ga_d ...` line (they disagree ⇒ wrong format was uploaded), plus final `TOTEN` sane vs siblings (~−3..−8 eV/atom for normal compounds; Ga metal ~−3.4/atom with Ga_d).
5. **Rebuild the chain**: unitcell.yaml (`rm` then build_unitcell_yaml — exists-skip), CPD hull (delete target_vertices/standard_energies/chem_pot_diag/composition_energies to regress phase, then `vasp-sop cpd run -f <formula> .`), delete pbes + all correction.json/defect_energy_info.{yaml,json} + defect_energy_summary.json (analyze short-circuits on summary; dei skips existing correction), then `vasp-sop defect analyze .`.
6. **Sanity-gate formation energies**: q-dependence must be linear in VBM shift (Δ = ΔVBM × q); q=0 entries shift only by chemical-potential micro-adjustments (~0.01 eV). Absurd values (±1000+ eV) mean energy-zero mismatch persists — check POTCAR: vs TITEL again.

## 一致性审计（以 OUTCAR 回显为准）

磁盘文件可被 loop 改写或已清理——**OUTCAR 回显是唯一权威**:
```python
import re
t = Path(rel/'OUTCAR').read_text(errors='replace')
titels = re.findall(r"TITEL\s*=\s*PAW_PBE\s+(\S+)", t)
encut = re.search(r"^\s*ENCUT\s*=\s*(\S+)", t, re.M)
```
- 按 (系统, 腿) 分组对比: cpd 主相/参考相 vs defect/perfect 的逐物种势类型(ZVAL/ENMAX 一并看)。

**批次既定模式(2026_undergo_spin_defect)**:
- cpd 腿: 每种元素默认势 + ENCUT=520(或各相裸 ENMAX 混合)
- defect 腿: 同一元素更重势 + ENCUT=400(裸 ENMAX)
- 已知不一致例: Ga vs Ga_d(ZVAL 3 vs 13)。**这是全批次既定模式**(SrGa4O7:Fe 已完成系统同款), 统一需 defect 腿全部重跑; 只换 cpd 侧会造出新的不一致。

## Quick checks along the way

- `grep -m3 "POTCAR: " OUTCAR` — must show the intended variant (Ba_sv/Ga_d/Y_sv... preserved, not stripped).
- Old polluted runs: `free energy TOTEN` first line is the initial-wavefunction value, always read the LAST TOTEN.
- `NELECT` must match Σ ZVAL×atoms (Ga_d=13); a mismatch means the wrong ZVAL data was loaded.
- Library Ga_d (POT_GGA_PAW_PBE/Ga_d, "9 entries") can OOM (SIGNAL 9) where the in-repo 8-entry variant runs fine — prefer extracting from existing original POTCARs over the library file.

## 提交纪律（大批量重提）

- 大批量(>10)同时挤同一集群(duguex_5)节点内存会 OOM: MPI rank killed by signal 9, exit 255; 或 POTCAR 相关 exit 59。
- 分批(每批 ~3 个)+ `crisp submit --ntasks 16`(默认 48 核/节点)。
- `QOSMaxSubmitJobPerUserLimit` 失败是队列限流, 等队列排空后重提即可(会自动恢复)。
- 所有在飞任务先取消再动 POTCAR, 否则运行中读到半截文件。
- 审计 mtime drift: INCAR 新于 OUTCAR = 改写后未重跑; cpd 相 OUTCAR 可能被已取消任务的输出覆盖(回传后)——判断"磁盘输入 vs 结果"一致性时小心。