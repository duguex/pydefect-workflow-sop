# defect_entry.json 修复脚本

## 问题

pydefect-complex v0.1.0 之前 `_write_defect_entry_json` 写的是 plain dict：
```json
{"name": "2Va_C1.001", "charge": 0, "full_name": "2Va_C1.001_0", "defect_center": null}
```

pydefect 后处理用 monty 反序列化，期望 `@module`/`@class` 字段。plain dict 会导致：
```
AttributeError: 'dict' object has no attribute 'charge'
```

## 修复

v0.1.1 已修复（写入 monty 序列化的 `DefectEntry`）。对已生成的老目录，用以下脚本修复：

```python
from pydefect.input_maker.defect_entry import DefectEntry
from monty.serialization import dumpfn
from pathlib import Path

for d in Path("defect/").glob("*+*/"):  # complex defect dirs only
    if not (d / "defect_entry.json").exists():
        continue
    name = d.name.split("_")[0]  # "2Va_C1.001"
    charge = int(d.name.split("_")[-1])  # 0
    de = DefectEntry(
        name=name, charge=charge,
        structure=None,
        site_symmetry="1",
        defect_center=(0.0, 0.0, 0.0),
        perturbed_sites=[],
        perturbed_site_symmetry="1",
    )
    dumpfn(de, d / "defect_entry.json")
    print(f"Fixed {d.name}")
```

## 验证

```bash
pydefect efnv -d <entry>_0/ -pcr perfect/calc_results.json -u ../unitcell/unitcell.yaml
```