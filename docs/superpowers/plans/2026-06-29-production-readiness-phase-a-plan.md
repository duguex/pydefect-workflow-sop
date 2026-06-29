# VASP SOP 生产就绪 — Phase A 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使 vasp-sop 生产实例缓存可达、管线可运行、38 个体系均可被 batch pipeline 识别

**Architecture:** 先修两个阻塞性 P0 bug，再递归扫描已收敛目录填充新版 JSONStore 缓存，然后补 P4 体系的 plan.yaml、生成新体系的 VASP 输入，最后全线 dry-run 验证

**Tech Stack:** Python 3.10+, pymatgen, maggma JSONStore, pydefect, vasp-sop CLI

## Global Constraints

- 所有修改必须通过现有 130 个测试：`python3 -m pytest tests/ -v`
- 不删除/覆盖生产实例中任何现有计算数据
- ProcessPoolExecutor 场景下的线程安全必须保证
- cache 的 content_hash 必须使用 `_content_hash()` 当前版本

---

## 文件结构

| 文件 | 职责 | 修改原因 |
|------|------|----------|
| `vasp_sop/core/cache.py:51-72` | `_get_stores()` 懒加载 | 加锁修复 #45 |
| `vasp_sop/cli/main.py:517-531` | `cache put -r` 的 `_classify` | 加 cache_lookup 守卫修复 #42 |
| `tests/test_cache.py` | cache 模块测试 | 新增 `_get_stores` 并发测试 |
| `tests/test_cli.py` | CLI 测试 | 新增 `cache put -r` 跳过已缓存测试 |

---

### Task 1: 修复 #45 — `_get_stores` 懒加载并发竞争

**Files:**
- Modify: `vasp_sop/core/cache.py:44-72`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `threading.Lock`
- Produces: 线程安全的 `_get_stores()`

- [ ] **Step 1: 确认 Bug 存在**

```python
# 读取当前代码确认无锁
```

- [ ] **Step 2: 修复 `_get_stores()`**

修改 `vasp_sop/core/cache.py:44-72`：

```python
import threading  # 在文件顶部 import 块中

# ── Store singletons (lazy-init) ───────────────────────────────────────

_meta_store = None
_blob_store = None
_stores_lock = threading.Lock()
_CACHE_KEY = ["formula", "content_hash"]


def _get_stores():
    """Return (meta_store, blob_store), creating on first access."""
    global _meta_store, _blob_store
    with _stores_lock:
        if _meta_store is None:
            from maggma.stores import JSONStore
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            _meta_store = JSONStore(
                paths=[str(CACHE_ROOT / "meta.json")],
                key=_CACHE_KEY,
                read_only=False,
            )
            _meta_store.connect()
        if _blob_store is None:
            from maggma.stores import JSONStore
            CACHE_ROOT.mkdir(parents=True, exist_ok=True)
            _blob_store = JSONStore(
                paths=[str(CACHE_ROOT / "blobs.json")],
                key="content_hash",
                read_only=False,
            )
            _blob_store.connect()
    return _meta_store, _blob_store
```

- [ ] **Step 3: 运行现有测试确认无回归**

```bash
python3 -m pytest tests/test_cache.py -v
```

Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/core/cache.py
git commit -m "fix: _get_stores lazy-init race (threading.Lock)

Add threading.Lock to _get_stores() to prevent concurrent first-call
from corrupting meta.json under ProcessPoolExecutor. Fixes #45."
```

---

### Task 2: 修复 #42 — `cache put -r` 不跳过已缓存目录

**Files:**
- Modify: `vasp_sop/cli/main.py:500-550`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cache_lookup()` (already imported at module level)
- Produces: `_classify()` 返回三种状态：`"cached"`, `"converged"`, `"unconverged"`

- [ ] **Step 1: 修复 `_classify` 和主循环逻辑**

修改 `vasp_sop/cli/main.py:517-531`：

```python
            def _classify(d: Path) -> tuple[str, Path]:
                from vasp_sop.core.cache import cache_lookup
                if cache_lookup(d) is not None:
                    return "cached", d
                text = (d / "OUTCAR").read_text()
                if "General timing and accounting" in text[-4096:]:
                    return "converged", d
                return "unconverged", d

            with ThreadPoolExecutor(max_workers=16) as pool:
                futures = {pool.submit(_classify, d): d for d in all_dirs}
                with tqdm(total=len(all_dirs), desc="Scanning", unit=" dirs") as pbar:
                    for future in as_completed(futures):
                        status, d = future.result()
                        if status == "converged":
                            to_cache.append(d)
                        elif status == "unconverged":
                            unconverged.append(d)
                        # "cached": skip silently
                        pbar.update(1)
```

- [ ] **Step 2: 添加打印信息让用户知道跳过了多少已缓存的**

Phase 3 报告之前增加：

```python
            # Phase 2.5: report cached count
            cached_count = len(all_dirs) - len(to_cache) - len(unconverged)
            if cached_count:
                print(f"  {cached_count} directories already cached, skipped.")
```

- [ ] **Step 3: 运行现有测试确认无回归**

```bash
python3 -m pytest tests/test_cli.py -v
```

Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add vasp_sop/cli/main.py
git commit -m "fix: cache put -r skips already-cached dirs

Add cache_lookup guard to _classify() so cache put --recursive
does not re-parse OUTCARs for directories already in the cache.
Saves 10-100x CPU on repeated runs. Fixes #42."
```

---

### Task 3: 生产实例缓存重建

**Files:** 无代码修改

- [ ] **Step 1: 确认 cache 当前为空**

```bash
vasp-sop cache status --verbose
```

Expected: "0 entries"

- [ ] **Step 2: 运行 cache put --recursive**

```bash
vasp-sop cache put --recursive \
  /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

Expected: 扫描所有目录，分类 cached/converged/unconverged，缓存已收敛目录

- [ ] **Step 3: 验证缓存内容**

```bash
# 确认有数据
vasp-sop cache status --verbose

# 查 GaN
vasp-sop cache query --formula GaN

# 查未收敛的不出现
vasp-sop cache query --formula GeS2 --converged-only

# 确认无崩溃
echo "Cache rebuild OK"
```

- [ ] **Step 4: 验证 cache put -r 幂等性（第二次应跳过所有已缓存）**

```bash
vasp-sop cache put --recursive \
  /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

Expected: `N directories already cached, skipped.`，极快完成

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: populate production cache via cache put --recursive"
```

---

### Task 4: GaN plan.yaml 生成 + 端到端验证

**Files:** 生产实例的文件操作

- [ ] **Step 1: 从 GaN 的 info.json 迁移生成 plan.yaml**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/GaN
python3 -c "
from pathlib import Path
from vasp_sop.core.config import PipelineConfig
config = PipelineConfig.from_legacy_json(Path('info.json'), root=Path())
config.to_yaml(Path('plan.yaml'))
print('plan.yaml generated')
"
```

- [ ] **Step 2: 验证 plan.yaml 内容**

```bash
cat /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/GaN/plan.yaml
```

Expected: 含 formula: GaN, functional, supercell 等字段

- [ ] **Step 3: 运行 batch dry-run 确认 GaN 状态**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch run . --dry-run 2>&1 | grep GaN
```

Expected: `GaN: DONE` 或 `GaN               DONE`

- [ ] **Step 4: 确认首次 run 无崩溃**

整体 dry-run 应在 3 分钟内完成（不再超时），所有体系阶段正确。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: GaN plan.yaml + batch dry-run verification"
```

---

### Task 5: 剩余 P4 体系补 plan.yaml

**Files:** 生产实例多个目录下的 plan.yaml

- [ ] **Step 1: MgO — 从 info.json 迁移**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/MgO
python3 -c "
from pathlib import Path
from vasp_sop.core.config import PipelineConfig
config = PipelineConfig.from_legacy_json(Path('info.json'), root=Path())
config.to_yaml(Path('plan.yaml'))
print('MgO plan.yaml generated')
"
```

- [ ] **Step 2: AlN — 从 basic_info.json 提取，调用 generate_config**

```python
# cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/AlN
# basic_info.json: {"formula": "AlN", "dopant_element": ["Sc"], ...}

from pathlib import Path
import json
from vasp_sop.core.config import generate_config

info = json.loads(Path("basic_info.json").read_text())
formula = info["formula"]
dopant = info.get("dopant_element", [])
generate_config(
    project_dir=Path.cwd(),
    formula=formula,
    dopant_elements=dopant,
)
# generate_config 会运行 pydefect_vasp mp 下载竞争相 POSCAR
# 但 cpd/ 已存在 → 只补 plan.yaml
```

- [ ] **Step 3: diamond, CaO, MoS2, SiC, ZnO — 从 POSCAR 推断 formula**

```python
# 对每个体系，从 unitcell/structure_opt/POSCAR 或 cpd/ 下某 POSCAR 推断 formula
# 然后调用 generate_config

from pathlib import Path
from pymatgen.core import Structure

# 尝试从已有 POSCAR 推断
for cand in [Path("unitcell/structure_opt/POSCAR"),
             next(Path("cpd").iterdir()) / "POSCAR"]:
    if cand.is_file():
        struct = Structure.from_file(str(cand))
        formula = struct.composition.reduced_formula
        print(f"Inferred formula: {formula}")
        break
```

- [ ] **Step 4: hBN, orth-SiC — 使用 defect init（需要手动确认 formula/dopant）**

```bash
# hBN
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/hBN
vasp-sop defect init -f BN  # 需要确认 formula

# orth-SiC
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/orth-SiC
vasp-sop defect init -f SiC
```

- [ ] **Step 5: 运行所有测试确认无回归**

```bash
cd /home/duguex/vasp_sop
python3 -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: plan.yaml for all legacy P4 systems"
```

---

### Task 6: 新体系 VASP 输入生成

**Files:** 生产实例中各体系的 cpd/ 目录

- [ ] **Step 1: 批量生成 VASP 输入**

```bash
# 为所有有 plan.yaml 但缺输入的体系生成 INCAR/KPOINTS/POTCAR
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect

# 用 batch 内置的输入生成
vasp-sop batch generate-inputs .
# 或逐个体系
for d in BaTe Ca2Ge7O16 CaCO3 CeO2 MgCO3 Sr2MgSi2O7 SrO SrTe; do
    vasp-sop vasp inputs "$d" -x pbesol
done
```

- [ ] **Step 2: 验证输入完整性**

```bash
for d in */; do
    name=$(basename "$d")
    cpd_dir="$d/cpd"
    missing=0
    for pd in "$cpd_dir"/*/; do
        [ -f "$pd/INCAR" ] && [ -f "$pd/POSCAR" ] && [ -f "$pd/POTCAR" ] && continue
        missing=$((missing + 1))
    done
    echo "$name: $missing missing inputs"
done
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: generate VASP inputs for Groppfeldt systems"
```

---

### Task 7: 全线 dry-run 验证

- [ ] **Step 1: 运行全线 dry-run**

```bash
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch run . --dry-run
```

- [ ] **Step 2: 验证结果**

```bash
# 预期：
# - 3 分钟内完成（不超时）
# - 各体系阶段正确（DONE / UC_DF / COMPETING / CPD_POST）
# - 无崩溃日志
```

- [ ] **Step 3: Commit 最终状态**

```bash
git add -A
git commit -m "feat: full dry-run verification passed"
```
