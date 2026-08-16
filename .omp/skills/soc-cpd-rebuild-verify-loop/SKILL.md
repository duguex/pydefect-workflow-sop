---
name: soc-cpd-rebuild-verify-loop
description: "Force-rebuild vasp-sop CPD chemical-potential artifacts and prove the rebuild happened, with the full cpd-dir protocol audit (ISPIN/LDAU/LSORBIT from OUTCAR echo, including non-NSW0 phases) before trusting standard_energies. Use when μ looks unchanged after cpd re-runs, diagnosing deep-negative formation energies in SOC batches, or after any cpd leg protocol change."
---

# SOC CPD rebuild + verification loop

Proven on 2026 batch (4 SOC systems, 85+24 cpd phases, 2026-08-14/15). Prevents silently shipping stale or polluted chemical potentials. 含 SOC cpd 协议平价纪律(soc-cpd-protocol-parity-rebuild)。

## 1. Full cpd protocol audit + parity check (BEFORE touching anything)

Scan ALL cpd dirs — including non-NSW0 phases (NSW=50 单点残余 carry old-protocol OUTCARs and pin the hull):

```python
# per dir, from OUTCAR (authoritative echo, NOT INCAR):
conv  = 'reached required accuracy' in tail[-200_000:]
ispin = re.search(r"ISPIN\s*=\s*(\d+)", t)     # defect legs run ISPIN=2
soc   = bool(re.search(r"LSORBIT\s*=\s*T", t)) # SOC run flag
ldau  = bool(re.search(r"LDAU\s*=\s*T", t))    # Gd phases: LDAU + LDAUL/LDAUU/LMAXMIX
```
- **协议平价检查（重跑 cpd 前必做）**: 8-12 单点 INCAR 是 vise 重新生成的协议, 可能丢 ISPIN/LDAU——与 defect 腿（μ 的消费者）不一致。**以各系统 defect/defect 腿 + stage1 vasp-cache 的 incar_json 为权威协议**（缺陷腿 ISPIN=2; LSORBIT 下 VASP 自动非共线但 defect 腿都显式 ISPIN=2, OUTCAR 回显 `ISPIN  =      1` 即协议降级）。
- Gd-containing phases need U=5 on Gd (pattern per-phase from vasp-cache stage1 `incar_json`, e.g. `LDAUL 3 -1`, `LDAUU 5 0`, `LMAXMIX 6`; LDAUL 模式从 stage1 cache 逐相抄, 如 `3 -1` = Gd+1 元素)。
- `LSORBIT = F` in final OUTCAR = SOC leg was never submitted (common after stage1 non-SOC + protocol repatch — the repatch must re-add `LSORBIT=.TRUE. ISYM=-1` AND resubmit; otherwise Bi phases are missing SOC stabilisation and μ shifts +2–12 eV)。
- **已收敛相也要核**: `grep -m1 "ISPIN\|LSORBIT\|LDAU" OUTCAR` 回显才算数。计划"只改 NSW"的前提必须先用 cache incar_json 验证。
- Cache-hit check: `entries.source_path like '%/<sys>/cpd/<fam>%' and converged_ionic=1 order by length(contcar_blob) desc` — stage1 geometry may already be in POSCAR (cache prefill) even when CONTCAR looks unrelaxed.

## 2. Force CPD artifact rebuild (compute_chemical_potentials is a NO-OP otherwise)

`compute_chemical_potentials(cpd_root, cfg, Composition(cfg.formula))` skips everything when `target_vertices.yaml` exists (全部阶段被 `if not target_vertices.is_file()` 门挡住). Move aside (backup) ALL of:
`target_vertices.yaml composition_energies.yaml relative_energies.yaml standard_energies.yaml chem_pot_diag.json cpd.pdf`
then re-run. Verify regenerated mtimes + `standard_energies.yaml` diff vs backup.

```python
for f in ["target_vertices.yaml","composition_energies.yaml","relative_energies.yaml",
          "standard_energies.yaml","chem_pot_diag.json","cpd.pdf"]:
    shutil.move(cpd/f, cpd/f"{f}.pre_fix")   # 备份移开(.pre_fix 与 base 的备份命名同义)
compute_chemical_potentials(cpd, cfg, Composition(cfg.formula))
```

## 3. Force analyze rebuild (pydefect exists-skip)

`vasp-sop defect analyze` skips dirs with existing `calc_results.json`/`correction.json`/`defect_energy_info.yaml` and short-circuits when `defect_energy_summary.json` exists. Backup+delete per-dir parse artifacts + root `defect_energy_summary.json`/`analyze_status.json`/`transition_levels.json` (save backup of old summary), re-run. A missing summary during report gen → promote `defect_energy_summary.partial.json` only after verifying type-gaps empty (20/20, no nulls).

## 4. Prove the rebuild (μ unchanged ≠ nothing happened)

- Per-phase `composition_energies.yaml` old-vs-new diff: phases with protocol changes MUST move (Gd+LDAU: expect >0.1 eV; adding SOC to Bi phases: 0.1–3 eV/phase). Zero movement across the board = stale artifacts or already-correct baseline.
- Cross-check compE vs OUTCAR `energy without entropy` (cell formula match; mce divides by cell multiplicity).
- Old μ can be correct even when cpd dirs look like "NSW=0 single points" — 8-12 single points ran on vasp-cache-prefill relaxed geometry with loop-patched INCARs (patch_incar_u adds LDAU/ISPIN). Verify against the 8-12 %j.log F= values before concluding μ is wrong.
- Chemical-potential vertices move <1 meV → deep-negative E_f is NOT a μ problem. Decompose E_f instead: raw `E_def(q) − E_perfect` from OUTCAR (same-electron-count comparison), μ terms from target_vertices (`μ_La−μ_Sr` etc.), `q·ε_F`, corrections. If raw replacement-energy difference is huge (−14 eV for a single substitution) with clean geometry/magnetic/NELECT/POTCAR checks, it is physical (e.g. hole polaron on O-2p + metastable host, e_above_hull +0.6 eV/f.u.).
- **μ 平移检验（深负归因）**: 重建后对比前后 summary(已有旧拷贝):
  - **E_f 只平移 Δμ_杂质−Δμ_主体、深度不变**(如 La_Sr1 q=1: −6.36→−6.57 = 恰 Δμ_La−Δμ_Sr)→ 深负是 **defect 腿电子结构内在**, 不要再查 cpd/化学势
  - 反位对深负相消化学势, 若对值也平移 → 同样归因 defect 腿

## 4b. 反位对物理性判定证据链（全过才标物理）

1. git 快照对比: md5 不同 ≠ 结构不同（归一化重写）; 用逐原子距离, ≤0.01Å 即同一结构
2. 替换位身份: 匈牙利指派（scipy linear_sum_assignment, 按物种）找未配对原子——恰 1 个杂质落对方位（<0.5Å）, 其余同物种 ≤0.2Å
3. 对结构互异: La_Sr 与 Sr_La 原子数不同（Sr7La17 vs Sr9La15）, 无塌缩
4. 磁态: OSZICAR `mag=` ≈0, 无漂移
5. 弛豫轨迹: 步数少 + 能量降小（已弛豫）或正常下降, 无大尺度重构
6. OUTCAR 回显 ≡ INCAR（无执行漂移）

## 5. ZBRENT NSW-exhausted stall (marker-less "completed")

`ZBRENT: accuracy reached` + step→0 + maxF < EDIFFG but no "reached required accuracy": `convergence_verdict` accepts via force_gate (max_f), but if a literal marker is required, heal with `EDIFF = 1e-6` (issue #119 pattern) + CONTCAR restart → converges at step 1.

## 6. 提交纪律（重建/重跑时）

- `crisp submit --dir <dir> --calculator vasp --tag cpu --skip-prefill`（--skip-prefill 保持显式几何控制）
- 大批量提交会撞 `QOSMaxSubmitJobPerUserLimit`（per-user 提交上限）→ 失败可安全重提, 等队列泄洪或加背压
- loop 自愈路径: ionic restart（force_gate/nsw 耗尽）+ ZBRENT `EDIFF=1e-6`; 重提前 `restart_from_contcar`