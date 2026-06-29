# VASP SOP 生产就绪 — Phase A 设计

> 日期: 2026-06-29
> 项目: vasp-sop (`/home/duguex/vasp_sop/`)
> 生产实例: `/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/`

## 背景

vasp-sop v0.1.0 近期完成了一系列关键重构（content_hash 替代 task_id 作为缓存主键、POTCAR 指纹加入哈希、缓存作为 VASP 完成状态的唯一真相源、orphan sweep 等）。生产实例有 38 个宿主体系，其中 29 个有 `plan.yaml`、10 个继承的 P4 体系缺 `plan.yaml`。

当前阻塞点：新版 maggma JSONStore 缓存 (`~/.vasp_sop/meta.json`) 为**空**，导致 `cache_lookup()` 对任何目录都返回 None → batch pipeline 认为所有体系处于 TARGET 阶段。

## 两阶段策略

- **Phase A（本文档）：** 缓存重建 + 补 plan.yaml + 管线验证。后处理仍依赖磁盘 OUTCAR。
- **Phase B（后续）：** cache 作为完整数据源，后处理可直接从 cache 恢复，不依赖原始 OUTCAR。

## Phase A 设计

### Step 0 — 修复 P0 Bug

**目标：** 修复两个直接影响 cache 填充和 batch pipeline 可靠性的 P0 缺陷。

#### #42: `cache put -r` 不跳过已缓存目录

**问题：** `cli/main.py:517` 的 `_classify()` 不检查 `cache_lookup()`，每次 `cache put -r` 都全量重解析所有 OUTCAR（10–100× CPU 浪费）。

**修复：** `_classify()` 中增加 `cache_lookup` 守卫：

```python
def _classify(d: Path) -> tuple[str, Path]:
    from vasp_sop.core.cache import cache_lookup
    if cache_lookup(d) is not None:
        return "cached", d
    text = (d / "OUTCAR").read_text()
    if "General timing and accounting" in text[-4096:]:
        return "converged", d
    return "unconverged", d
```

Phase 2（分类）中新增 `"cached"` 状态的处理——直接跳过，不加入 `to_cache`。

#### #45: `_get_stores` 懒加载并发竞争

**问题：** `cache.py:51-72` 的 `_get_stores()` 是 check-then-act 无锁代码。ProcessPoolExecutor 多 worker 同时首次调用时，两个 worker 都看到 `_meta_store is None`，都连同一个 TinyMongo 后端，导致 `meta.json` 损坏。

**修复：** 加 `threading.Lock`：

```python
_stores_lock = threading.Lock()

def _get_stores():
    global _meta_store, _blob_store
    with _stores_lock:
        if _meta_store is None:
            _meta_store = JSONStore(...)
            _meta_store.connect()
        if _blob_store is None:
            _blob_store = JSONStore(...)
            _blob_store.connect()
    return _meta_store, _blob_store
```

**验证：** `python3 -m pytest tests/ -v` 全过（已有 130 个测试）。额外增加 `_get_stores` 并发测试确认无竞争。

### Step 1 — 缓存重建

**目标：** 将生产实例所有已收敛 VASP 计算结果填充到新 JSONStore 缓存。

**执行：**

```bash
vasp-sop cache put --recursive \
  /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

使用 `scan_converged_dirs()`（cache.py:615）递归遍历生产目录，对每个含收敛 OUTCAR 的目录调用 `vasp_results_put()`，以新版 `_content_hash()` 计算哈希并写入 meta.json + blobs.json。

**验证指标：**

| 检查项 | 方法 | 预期 |
|--------|------|------|
| 缓存非空 | `vasp-sop cache status --verbose` | N entries, N > 0 |
| GaN 可查 | `vasp-sop cache query --formula GaN` | ≥1 条 converged=True |
| 未收敛不出现 | `vasp-sop cache query --formula GeS2 --converged-only` | 0 条 |
| 条目完整性 | 对一条记录展开 inspect | 含 formula, content_hash, total_energy, bandgap, calc_type, tags, source_dir |

### Step 2 — GaN 端到端验证

**目标：** 选数据最完整的 GaN 走通从 plan.yaml 生成到 batch 识别的全流程。

**子步骤：**

1. **生成 plan.yaml** — GaN 有 `info.json`（legacy 格式），通过 `PipelineConfig.from_legacy_json()` 迁移 → `plan.yaml`
2. **cache 辅助填充** — 确保 GaN 的 cpd 目标相、unitcell、defect 目录全部被 cache put 收录
3. **dry-run 验证** — `vasp-sop batch run . --dry-run` 产出：
   - GaN 阶段 = `DONE`（已有 `defect_energy_summary.json`）
   - 不触发任何 VASP 提交
   - 日志无崩溃/超时
4. **新特性回归** — 确认以下不报错：
   - Orphan sweep（批量前扫描僵死 crisp 输出）
   - `_get_db` 线程安全（fadd628）
   - `--poll` 参数

### Step 3 — 剩余 P4 体系补 plan.yaml

**目标：** 10 个继承体系均可被 batch pipeline 识别和处理。

**分类处理：**

| 体系 | 数据源 | 方法 |
|------|--------|------|
| GaN, MgO | `info.json` | `from_legacy_json()` 直接迁移 |
| AlN | `basic_info.json` | 提取 formula + dopant → 调用 `generate_config()` |
| diamond, CaO, MoS2, SiC, ZnO | 有 cpd/ + unitcell/ 目录 | 从 POSCAR/CONTCAR 推断 formula → 调用 `generate_config()` |
| hBN, orth-SiC | 无 cpd/ 目录 | 用 `defect init` 初始化结构，生成 plan.yaml |

**约束：** 不删除、不覆盖现有 cpd/defect/unitcell 目录和其中的已有计算结果。

### Step 4 — 新体系 VASP 输入生成

**目标：** 29 个已有 plan.yaml 的 Groppfeldt 体系具备可提交的 VASP 输入。

**执行：**

```bash
# 逐个体系执行（可在 Step 2 完成后批量）
vasp-sop vasp inputs /path/to/system -x pbesol
```

**验证：** 每个体系目录下存在 `INCAR`、`KPOINTS`、`POTCAR`、`POSCAR`。

### Step 5 — 全线 dry-run

**目标：** 确认 batch pipeline 在所有 38 个体系上稳定运行。

**执行：**

```bash
vasp-sop batch run \
  /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect \
  --dry-run
```

**验收标准：**

| 标准 | 说明 |
|------|------|
| 不超时 | 3 分钟内完成（之前 3 分钟超时因 cache 为空导致 TaskDoc 大量 I/O） |
| 阶段正确 | 各体系按实际计算状态分类 |
| 无崩溃 | 单个体系错误不传播 |
| 输出可读 | 每体系一行 `name: PHASE` 摘要 |

### Step 6 — 生产提交（可选）

在 dry-run 确认无误后，可择机启动真实提交：

```bash
vasp-sop batch run /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
```

## 架构决策记录

### ADR-1: cache put --recursive 而非 migrate_from_sqlite

旧 SQLite 缓存的 `content_hash` 格式与新版不同（新版包含 POTCAR 指纹、KPOINTS 类型归一化）。迁移旧数据会产生不匹配的哈希，`cache_lookup` 仍无法命中。因此直接重新扫描目录、以新版哈希重写是最可靠的方式。

### ADR-2: P4 体系 plan.yaml 从现有数据推断

继承体系已包含大量计算结果（cpd/ 竞争相 VASP、unitcell/ 带/DOS/介电），重新初始化（`defect init`）会重新下载 MP 结构并覆盖 cpd/ 目录。保留现有数据仅补充 plan.yaml 是侵入性最低的方式。

### ADR-3: Phase A/B 分离

不在一轮中同时解决缓存填充和后处理从缓存读取两个问题，降低单次交付的复杂度和风险。

### ADR-4: P0 Bug 前置修复

GitHub Issue 中有 51 个 Open Issue（含 5 个 P0）。其中 #42 和 #45 直接影响 cache 填充和 batch pipeline 的可靠性。将这两个 Bug 的修复作为 Step 0，确保后续步骤的基础设施稳定。其余 3 个 P0（#43、#44、#48）或已修复或不阻塞 Phase A，留待后续处理。