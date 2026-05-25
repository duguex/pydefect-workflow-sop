# Diamond 2Va_C1 端到端验证记录
2026-05-23, pydefect-complex v0.1.1 + SOP v2.4.0

## 项目
- 路径: /mnt/shared/.../diamond/, 216-atom diamond supercell
- 泛函: PBEsol, ENCUT=400, vasp_gam, Gamma-only KPOINTS
- 集群: duguex_113, 64 cores

## 生成
```python
maker = ComplexDefectMaker.from_supercell_info(
    'defect/supercell_info.json',
    dopants=['N','B','O','Si'], max_distance=4.0, charges=[0],
)
```

## VASP 参数一致性坑
首次用默认 vise vasp_set → SIGMA=0.1, KPOINTS=2x2x2, NSW=20。
与已有单缺陷 SIGMA=0.02, Gamma-only, NSW=50 不一致。
修复: 从 Va_C1_0/vise_log.yaml 提取原始参数重新生成 + 复制 KPOINTS。

## 结果
- VASP: ~3 min, 收敛
- E_form (q=0): 11.81 eV
- eFNV: pc=0.0, alignment=-0.0

## 踩坑
1. defect_entry.json plain dict → monty DefectEntry
2. VASP 参数不一致 → vise_log.yaml 提取 + KPOINTS 复制
3. crisp output/ → cp 到根目录