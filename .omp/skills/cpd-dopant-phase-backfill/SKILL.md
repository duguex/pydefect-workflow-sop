---
name: cpd-dopant-phase-backfill
description: Backfill dopant-element competing phases into a vasp-sop cpd directory when plan.yaml dopant_elements changed after cpd generation (dei KeyError / wave3 partial with n_dei=0).
---

# CPD dopant phase backfill

When `plan.yaml` `project.dopant_elements` changed AFTER the cpd directory was generated, the competing-phase set lacks dopant-containing phases. Wave3 `dei` then fails with `KeyError: '<dopant>'` (pydefect `make_defect_energy_info` needs `standard_energies.yaml` entries for every element in the defect composition difference) → `analyze_status.json` shows `status: partial`, `n_dei: 0`.

根因背景: cpd.py **没有** dopant 变更检测(defect 有 fingerprint 机制, cpd 没有)——所有 Fe 掺杂体系(SrAl4O7/BaAl4O7/SrGa4O7:Fe 等)同型风险; BaAl2B2O7 因 cpd 重建过(dopant 生效)而正常。

## Symptom check

```bash
grep -c "^<dopant>:" <sys>/cpd/standard_energies.yaml   # 0 = missing
# or run dei manually on one doped dir:
cd <sys>/defect && pydefect dei -d Fe_Al1_0 -pcr perfect/calc_results.json \
  -u ../unitcell/unitcell.yaml -s ../cpd/standard_energies.yaml --verbose
# → KeyError: '<dopant>'
```

## 0. Staleness 诊断(先判定哪一侧, 30s-几分钟)

### cpd 侧(wave3 partial 诊断, cpd-dopant-staleness-wave3)
1. 看 `defect/analyze_status.json`: `n_corrected` > 0 但 `n_dei` = 0, status=partial; 缺陷总结反复生成又 demote; batch_run.log: `post-process partial`
2. 手动验证: 上述 dei 命令 → `KeyError: '<元素>'` = 缺失的掺杂元素参考
3. 对照 `cpd/standard_energies.yaml` 键集 vs plan `dopant_elements`
4. 确认 cpd 生成早于 plan 变更: `stat target_vertices.yaml vs plan.yaml` mtime

### defect 侧(缺陷目录缺失/未生效, defect-dopant-staleness-check)
plan `dopant_elements` 只影响**新建**缺陷集; fingerprint(`defect_generate_flag`/`defect_in.yaml` mismatch)应触发重建但会静默失火; 若失火时 `verify_nelect` 被排除目录阻塞, 构建永不完成(实测两次: BaAl2B2O7 Fe, Y2Ti2O7 Bi: 0 dopant defects)。
```bash
D=/mnt/shared/home/2sidesniddle/vasp/2026_undergo_spin_defect/<Sys>
stat -c '%y %n' $D/defect/defect_in.yaml $D/plan.yaml   # defect_in 必须新于 plan dopant 编辑
head -20 $D/defect/defect_in.yaml                       # entries 是宿主缺陷(无 dopant)
grep -c '^Bi_' $D/defect/defect_in.yaml                # 0 → dopant 从未生效
ls $D/defect/ | grep -c '^Bi_'                          # 目录确认
```
经验规则:
- `defect_in.yaml` mtime < plan dopant 编辑时间 → dopant 未生效
- dopant 是**唯一** U 元素的体系(如 Y2Ti2O7:Bi)会显示 dopant 目录有 `LDAUU`、宿主目录无;若**任何**目录都无 LDAU → 怀疑缺 dopant 目录
- 复活路径: 先修 `verify_nelect` 阻塞(fingerprint 重建的门), 再删 `defect_in.yaml`(fingerprint mismatch 强制重建)——**绝不手改缺陷清单**

## Procedure (cpd 补相; do NOT run pydefect_vasp mp in place — it crashes on existing dirs)

1. **Fetch into tmp** (all phases for the full chemical system, including dopant):
   ```bash
   rm -rf /tmp/cpd_fetch_<sys> && mkdir -p /tmp/cpd_fetch_<sys>
   cd <vasp_sop repo> && .venv/bin/python - <<'EOF'
   from pathlib import Path
   from vasp_sop.materials import fetch_candidate_phases
   fetch_candidate_phases(["<elem1>", "<elem2>", ..., "<dopant>"], Path("/tmp/cpd_fetch_<sys>"))
   EOF
   ```
2. **Move only new dirs** (existing converged host phases stay untouched):
   ```python
   existing = {p.name for p in cpd.iterdir() if p.is_dir()}
   for p in sorted(Path("/tmp/cpd_fetch_<sys>").iterdir()):
       if p.is_dir() and p.name not in existing:
           shutil.move(str(p), str(cpd / p.name))
   ```
3. **Submit the new phases** (prepares inputs + DFT+U patch + crisp submit):
   ```python
   from vasp_sop.core.config import PipelineConfig
   from vasp_sop.defect.cpd import _submit_cpd_batch
   config = PipelineConfig.from_yaml(root / "plan.yaml", root=root)
   _submit_cpd_batch(root / "cpd", [<new phase names>], config)
   ```
   Verify +U landed: `grep -E "LDAU|ISPIN" <cpd>/FeO_mp-*/INCAR` (dopant U, e.g. Fe=3).
4. **Wait for convergence** (small cells, ~30-60 min), then recompute chemical potentials:
   ```bash
   vasp-sop cpd energies <sys>/cpd -f <formula>   # compute_chemical_potentials
   ```
   (or `vasp-sop cpd diagram <sys>/cpd` if phase-diagram solve needed)
5. Wave3 auto-reruns on the next loop poll; dei/des now succeed → defect summary → COMPLETE.

## Pitfalls

- `fetch_candidate_phases` into the live cpd dir crashes with `FileExistsError` — pydefect's `make_poscars_from_query` does not skip existing dirs. Always fetch into tmp first.
- cpd.py has NO fingerprint/rebuild detection for dopant changes (unlike defect builder) — the gap silently sits until dei fails. Known affected: all Fe-doped systems whose cpd predates the plan change (CaAl4O7 fixed 2026-08-10; SrAl4O7/BaAl4O7/SrGa4O7:Fe pending).
- Host phases already converged are kept — only the new dopant phases get computed.
- `mol_O2` / molecule phases: fetch includes them; keep existing ones.
- `pydefect dei` 参数是 `-s`（不是 `-se`）；`-se` 会被拆成 `-s e` → FileNotFoundError 'e'。
- dei 批量命令（_run_batches 并行化后）一批失败会中止整批——先修缺失元素再跑。
- **修复需用户拍板**(重建 cpd / 手动补少量参考相如 Fe 金属氧化物); 建议给 cpd.py 加 dopant 变更检测(像 defect 的 fingerprint)。
- defect 侧重建后验证: `grep -c '^Bi_' defect_in.yaml` 匹配预期 dopant 位点数, 新目录 INCAR 含 dopant 元素的 `LDAUU`。