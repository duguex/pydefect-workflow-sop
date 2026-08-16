---
name: vasp-potcar-swap-crosscheck-protocol
description: "手工替换 VASP 批次 POTCAR 段（如 Ga→Ga_d）后，用 vasp-sop 正式流程交叉检验：独立新目录 + vise 默认生成 + crisp 提交 + 同协议能量对比归因。用于用户要求\"重算已改目录/用 vasp_sop 流程交叉检验/验证手动修复\"类请求。"
---

# POTCAR 段替换后的正式流程交叉检验（2026-08-15 实证，Ga→Ga_d 19 目录）

## 何时用
手动替换 POTCAR 段（如 Ga→Ga_d）后，用户要求"用 vasp_sop 的流程重算交叉检验"或"独立另算"。

## 前置纪律（段替换本身）
- **只用仓库内 verbatim 段**：从 defect/perfect/POTCAR 按 `^  PAW_PBE <el> <date>`...`End of Dataset` 原样字节切取；**禁止 TITEL= 前缀重建**（VASP 解析错位，能量偏移 ~8000 eV）
- **禁止用库段**：`/mnt/shared/VASP_POT/POT_GGA_PAW_PBE/Ga_d/POTCAR` 是 9-entries 格式，提交即 OOM（KILLED BY SIGNAL 9）；仓库 POTCAR 是 8-entries 格式
- 含杂质元素（如 Fe）的 cpd 相：Fe 段从 `defect/Fe_Ga1_0/POTCAR` 等缺陷目录提取
- 段内容 md5 不必与库一致（头部注释可不同），数据部分（PSCTR 之后）才是计算输入

## 交叉检验流程（已验证）
1. **独立目录**：`/mnt/shared/home/2sidesniddle/vasp/xcheck_<date>/<体系>/<rel路径下划线化>/`——绝不碰现有计算
2. **结构**：从被改目录复制 CONTCAR → 新目录 POSCAR
3. **vise 默认生成**：`vasp-sop vasp inputs <dir>`（生成 INCAR/KPOINTS/POTCAR）。验证 POTCAR 的 Ga 段——vise 默认就是 Ga_d（库推荐），裸 Ga 是旧库时代残留
4. **提交**：`crisp submit --dir <绝对路径> --calculator vasp --skip-prefill`——**必须绝对路径**（相对路径报 "Local directory vanished"）
5. **ZBRENT 崩溃修复**：金属相/无带隙相 vise 默认 IBRION=2 线搜索崩（ZBRENT: fatal error in bracketing）→ `sed -i 's/IBRION = 2/IBRION = 1/' INCAR` 重提；另查 ENCUT ≥ max(ENMAX)（Ga_d ENMAX=282.691，裸 Ga 时代 ENCUT=175 会先崩 ZPOTRF）
6. **对比**：逐目录 `free energy TOTEN` 最后值（OUTCAR 尾，General timing 存在=完整）。同协议 Δ<0.02 eV 为一致；大差异归因协议而非赝势：SOC（LSORBIT=T vs 默认无，~1 eV/22 原子）、磁序（ISPIN/ISMEAR/EDIFF，Fe 体系可达 eV 级）、KPOINTS/ENCUT

## 陷阱
- `crisp verdict` 是快照会滞后——本地 OUTCAR 收敛+General timing 才是真相（fetch 竞态可把 completed 标 failed）
- 失败重试前先看 `%j.log` 尾部错误类型（ZPOTRF=ENCUT 不足；ZBRENT=IBRION；KILLED 9=库段格式）
- unitcell.yaml 重建：CLI `unitcell yaml` 有 formula 空崩 bug → 直接 `build_unitcell_yaml(uc, PipelineConfig(formula=..., root=cwd))`，且必须先 rm 旧 yaml（exists-skip）
- 链更新顺序：unitcell.yaml → 删 pbes/correction/defect_energy_info*/summary → cpd phase-regress（rm target_vertices.yaml 等）→ `cpd run -f <formula> .` → `defect analyze .`
- 缺陷链是否重算看零点匹配：缺陷 OUTCAR 的 Ga TITEL 与原版一致（Ga_d）且能量浅（−3~−7 eV/原子）→ 无需重算；若 standard_energies 与缺陷能量零点不匹配（深 vs 浅）→ 必须全链统一
