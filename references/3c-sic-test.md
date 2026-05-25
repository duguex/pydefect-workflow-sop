# 3C-SiC PyDefect 全功能测试结果

> 3C-SiC (cubic, F-43m, MP#8062, a=4.354 Å, PBEsol band gap 1.23 eV)
> 项目路径: `~/materials-research/pydefect-test/3C-SiC/`
> 测试日期: 2026-05-17
> 最终结果: ✅ 全部 22/22 子命令测试通过

## 项目结构

```
3C-SiC/
├── unitcell/
│   ├── structure_opt/          # 结构优化 ✅ a=3.0823 Å, E=-15.915 eV
│   ├── band/                   # 能带计算 ✅ vasprun.xml
│   ├── dos/                    # DOS 计算 ✅ DOSCAR
│   ├── dos_aecgar/             # DOS + AECCAR ✅ 含 AECCAR0/1/2
│   └── dielectric/             # 介电计算 ✅ ε=10.41
├── cpd/                        # 竞争相
│   ├── Si_mp-149/              # Si 优化 ✅ E=-11.42 eV (Si2)
│   ├── C_mp-3347313/           # C 优化 ✅ E=-19.29 eV (C2)
│   ├── SiC_mp-8062/            # 复用 unitcell 结果
│   ├── composition_energies.yaml
│   ├── standard_energies.yaml  # Si: -5.71, C: -9.65 eV/atom
│   ├── target_vertices.yaml    # A(Si-rich) / B(C-rich)
│   └── cpd.pdf                 # 化学势相图
├── defect/
│   ├── supercell_info.json     # 2×2×2 = 64 atoms
│   ├── SPOSCAR                 # 超胞
│   ├── defect_in.yaml          # 4种缺陷 × 28电荷态
│   ├── perfect/ + 28 defect dirs (全部算完 ✅)
│   ├── perfect_band_edge_state.json
│   └── 各目录: calc_results.json, correction.json,
│       defect_structure_info.json, defect_energy_info.yaml,
│       band_edge_orbital_infos.json, band_edge_states.json,
│       eigenvalues.pdf, correction.pdf
├── unitcell.yaml                # Eg=1.229 eV, ε=10.41
├── defect_energy_summary.json   # 形成能汇总
├── transition_levels.json       # 缺陷跃迁能级
├── energy_A.pdf                 # 形成能图 (Si-rich)
├── energy_B.pdf                 # 形成能图 (C-rich)
└── volumetric_data_local_extrema*.json  # AECCAR间隙位搜索
```

## 缺陷形成能 (Si-rich)

| 缺陷 | 最低能 q | Ef (eV) | 跃迁能级 |
|:--|:--:|:--:|:--|
| Va_C (C空位) | +2 | 1.352 | (2/1) @ 1.13 eV |
| Si_C (Si替C) | +4 | 2.356 | (1/0) @ 0.19 eV |
| C_Si (C替Si) | 0 | 3.512 | 无稳定跃迁 |
| Va_Si (Si空位) | 0 | 8.153 | (0/-1) @ 0.75 eV |

## 命令覆盖清单

### pydefect (14/14)
s, ds, ai, pi, sre, cv, pc, dsi, efnv, bes, dei, des, cs, pe

### pydefect_vasp (8/8)
u, mp, mce, le, de, cr, pbes, beoi

## 实测发现的关键问题 & workaround

1. **crisp output 目录**: crisp 下载结果到 `output/` 子目录，但 `pydefect_vasp cr/beoi` 等命令在当前目录找 `vasprun.xml`/`OUTCAR`/`PROCAR`。需 `cp output/* .` 或 `ln -s` 后解析。
2. **ENCUT 一致性**: `vise vs -t defect` 按 POTCAR ENMAX 设 ENCUT（C:400 eV, Si:245 eV），但完美晶体可能用了更高值（520 eV）。缺陷 ENCUT 必须和 bulk 一致，否则形成能系统偏移。
3. **bes 命令归属**: `band_edge_states` (bes) 是 `pydefect` 命令，不是 `pydefect_vasp`。
4. **间隙位搜索**:
   - 致密结构（金刚石/闪锌矿）用 AECCAR0+AECCAR2 可能找不到间隙位
   - workaround: 单用 AECCAR2（价电荷密度），降 min_dist=0.3, tol=0.3, threshold_abs=0
   - `append_interstitial` 要求 `-p` 的结构与 `supercell_info.json` 中 `unitcell_structure` 完全匹配
5. **`make_composition_energies -y`**: `-y` 是**读取**已有 yaml，不是输出路径。
