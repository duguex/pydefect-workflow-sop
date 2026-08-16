---
name: poscar-geometry-provenance-audit
description: "Audit whether defect/perfect directory POSCAR geometries match their git-snapshot provenance (manual/latest/baseline CONTCAR), with numeric-normalized fingerprints and pymatgen StructureMatcher for periodic-equivalence. Use when asking 结构用的对吗 / 验证恢复几何 / 排查 POSCAR 来源不明，especially after crisp fetch rewrote POSCAR in normalized format."
---

# POSCAR 几何来源审计

判断 defect 目录当前 POSCAR 是否来自正确的 git 快照（03:16 manual / LATEST cycle / BASELINE），用于恢复几何验证与"结构用的对吗"类问题。

## 坑（必须先知道）

1. **字符串/坐标字符串哈希必假阳性**：crisp fetch 回的文件是规范化格式（短小数位），git 快照是 VASP 原始格式（16 位小数）。md5 或未归一化的坐标字符串比较会把格式差异误判为结构不同。
2. **周期性等价假象**：原子排序/晶胞向量选择不同会造成大位移（如 14 Å）——用 StructureMatcher 判等价，RMS≈1e-14 才是真相同。
3. **脚本缓存 key 必须含 rel**：按 (system, commit, rel) 缓存 git show 结果，否则所有目录比较的是第一个目录的 CONTCAR。

## 正确流程

### 1. 数值归一化指纹（粗筛）

```python
def fingerprint(text):
    lines = text.splitlines()
    if len(lines) < 9: return None
    lat = [round(float(x), 4) for l in lines[2:5] for x in l.split()]
    nat = sum(int(x) for x in lines[6].split())
    start = 8
    if lines[7].strip().upper().startswith(("S","SELECTIVE")): start = 9
    coords = [round(float(x), 4) for l in lines[start:start+nat] for x in l.split()[:3]]
    return hashlib.md5(repr(lat + coords).encode()).hexdigest()
```

对每个目录：POSCAR 指纹 vs `git -C <体系> show <commit>:defect/<dir>/CONTCAR` 的 manual（03:16）/latest（cycle）/baseline 三快照指纹。

### 2. StructureMatcher 终审（指纹不匹配的少数）

```python
from pymatgen.core import Structure
from pymatgen.analysis.structure_matcher import StructureMatcher
m = StructureMatcher(ltol=0.3, stol=0.5, angle_tol=5)
fit = m.fit(Structure.from_file(POSCAR), Structure.from_file(git_cont))
rms = m.get_rms_dist(a, b)  # ≈1e-14 = 同一结构
```

### 3. 判定表

| 结果 | 含义 |
|---|---|
| == manual | 中招目录（03:16 收敛几何恢复）✓ |
| == latest | Batch B / cycle 快照收敛几何 ✓ |
| StructureMatcher 等价 | 原子排序/取向不同，物理结构相同 ✓ |
| 真不匹配 | 异常——查 POSCAR mtime（fetch 时刻=输入副本；提交时刻=本地准备）与作业链 |

## 参考

- 快照 commit：manual = 各体系 03:16 manual snapshot（Gd2GaSbO7:Bi=74575fc, La2SrSc2O7=8b6787d, La2Zr2O7=f36193e, Y2Sn2O7=5204cb6, Y2Ti2O7=c7ae2d9）；latest = batch_b 脚本 LATEST 字典；baseline = 各体系最早快照
- 2026-08-12 实测：308 目录全过（259==manual + 47==latest + 2 等价），0 真异常
