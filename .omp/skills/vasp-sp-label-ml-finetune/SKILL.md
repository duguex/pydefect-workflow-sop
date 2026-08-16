---
name: vasp-sp-label-ml-finetune
description: "用 /mnt/shared 自产 VASP SP 标签（c606 mptrj/perovskite 批次）微调 MACE/CHGNet：转换→切分→离群过滤→微调配方→Si 对照。用于\"用自产标签训力场/微调 MACE 或 CHGNet\"类请求。"
---

# VASP SP 标签 → MACE/CHGNet 微调（2026-08-14/15 验证配方）

## 数据管线
1. 转换：`python3 ~/ml-index/scripts/convert_vasp_relax_to_extxyz.py --batch all --workers 4`（需 pymatgen+ase 的 python，用 `~/.conda/envs/dgkan_rocm_3.11/bin/python`）。产物 `/mnt/shared/datasets/self-vasp-relax/{mptrj,perovskite}/*.extxyz` + manifest TSV（f=力是否真实挂载；⚠️ c606 批次有截断力块目录——力块行数≠NIONS，能量有效力缺失）。
2. 切分：`mptrj-train-800.xyz` / `mptrj-valid-200.xyz`（ase write 单文件多帧；回读用 `read(path, index=':')`——`list(read(path))` 只给第一帧的原子）。
3. **过滤（必须）**：能量离群 `-15.0 ≤ E/atom ≤ 0.0`（c606 标签有 −39.4/+0.53 eV/atom 离群会打爆训练）；无 forces 帧跳过（ase≥3.29 `get_forces()` 直接抛异常，先查 `a.calc.results`）。

## MACE 微调（torch_cu126 env：mace 0.3.15 + torch 2.12 cu126）
```bash
~/.conda/envs/torch_cu126/bin/python -m mace.cli.run_train \
  --name=mp0-ft-sp --foundation_model=medium-mpa-0 --E0s=foundation \
  --energy_key=energy --forces_key=forces \
  --train_file=.../mptrj-train-800.xyz --valid_file=.../mptrj-valid-200.xyz \
  --energy_weight=1.0 --forces_weight=100.0 --lr=0.001 \
  --batch_size=6 --max_num_epochs=30 --swa --device=cuda --num_workers=0 \
  --model_dir=verify/out/mp0-ft-sp
```
- **lr=0.01 不稳**（loss 0.7-1.1 震荡、RMSE_E 涨到 1.7eV）→ 必须 1e-3。
- 键参数：MACE 默认找 `REF_energy/REF_forces`，extxyz 用 `energy/forces` 需显式 `--energy_key/--forces_key`。
- 缺 `Atomic energies must be provided` → `--E0s=foundation`。
- stage-2（ep21 起 energy weight×1000）会继续压 RMSE_E。
- 结果参照（800 帧 c606 SP）：valid RMSE_E 350 / RMSE_F 235 meV；Si fcc 对照 foundation −2.892 → FT stage1 −3.654 → stage2 −4.094 eV/atom（标签-基础模型系统偏移 ~0.4 eV/atom，混合元素冒烟规模下部分纠正）。

## CHGNet 微调（torch_cu126 env + repo 版 chgnet）
- **PyPI 0.3.0 wheel 被 yank**（权重缺失 + ase 3.29 ExpCellFilter 崩）→ 用 repo 版：`pip install -e . --no-deps`。
- API（repo 版与 0.3 wheel 不同）：`StructureData(structures, energies=总eV, forces, structure_ids, graph_converter)`（**不是** StructureDataset）+ `get_train_val_test_loader(return_test=False)`（返回 2 元组）+ `Trainer.train(train_loader, val_loader, save_dir=...)`。
- ckpt 嵌套：`torch.load(...)['model']['state_dict']` 才能 `load_state_dict`；`CHGNet.load()` 只支持预训练名（无 path 参数）。
- 结果参照（486 帧滤离群）：val e_MAE 124 meV/atom；Si 对照 −0.77 → −2.49 eV/atom。尾段 loss 尖峰（3.6万+）= 标签离群残余，模型未毁。

## 对照验收（Si fcc 同 cell，a=5.46 立方）
MACE 用 `MACECalculator(model_paths=...)` float64；CHGNet 用 `Structure.from_ase_atoms(bulk('Si','fcc',a=5.46,cubic=True))` + predict_structure；ALIGNN 用 `Graph.atom_dgl_multigraph` + 模型返回 dict 取 `out` 键。
基线：MACE-MPA-0 −2.892、CHGNet −0.77、ALIGNN-FF −2.4573 eV/atom。
