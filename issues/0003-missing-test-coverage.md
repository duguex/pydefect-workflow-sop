# Issue 0003: 新功能缺少测试覆盖

## 背景

近期合并的三个主要 feature（doped 超胞、SQLite 缓存、batch dry-run）在开发过程中出现了多次可被测试捕获的 bug：
- `_submit_or_skip` dry-run edit 删除了非 dry-run 路径（75 测试仍在跑，但没覆盖这个函数）
- `_cache_target_vasp` 被调用但未定义（潜伏 NameError）
- `calc_results_get` 在多次 rewrite 中被误删
- 测试污染真实数据库（monkeypatch 没生效）

当前 74 个测试覆盖了基本路径，但新功能的边界情况大量缺失。

## 需要补充的测试

### 1. SQLite 缓存（优先级高）

| 场景 | 当前状态 | 说明 |
|------|---------|------|
| 并发读写 | ❌ 缺失 | 14 进程同时 `calc_results_put` → SQLite WAL 是否安全？ |
| DB 损坏恢复 | ❌ 缺失 | `_get_db()` 打开损坏的 DB 应该报什么错？ |
| 超大 OUTCAR 解析 | ❌ 缺失 | 100MB+ OUTCAR 的 regex 提取和 Outcar 解析是否 OOM？ |
| `calc_results_put` 失败回退 | ❌ 缺失 | Outcar 解析失败但 regex 提取成功 → converged=1 但 outcar_json=null |
| `calc_cpd_put` yaml 损坏 | ❌ 缺失 | composition_energies.yaml 格式错误 → 应该跳过不报错 |
| `_get_db` 首次初始化 | ❌ 缺失 | 空目录 → 创建 DB + 建表 |
| `override_cache_root` 隔离 | ⚠️ 间接覆盖 | 被 test_cache.py fixture 使用，但没有独立测试 |

### 2. batch dry-run（优先级中）

| 场景 | 当前状态 | 说明 |
|------|---------|------|
| `--dry-run` 一轮退出 | ❌ 缺失 | `if dry_run: break` 曾被误删导致死循环 |
| `_is_cached` 生效 | ❌ 缺失 | `_competing_dirs` 返回空当缓存命中 |
| CPD cache path | ❌ 缺失 | 所有竞争相命中缓存 → 跳过 `pydefect_vasp mce` |
| `_cache_phase_results` | ⚠️ 间接覆盖 | 被 backfill 测试覆盖，但独立提交场景未测 |

### 3. doped 超胞（优先级低）

| 场景 | 当前状态 | 说明 |
|------|---------|------|
| 复杂结构 | ❌ 缺失 | 只测了 NaCl（2 atoms primitive），没测多元素体系 |
| `get_ideal_supercell_matrix` 返回 None | ❌ 缺失 | fallback 到 pydefect |
| doped 未安装 | ❌ 缺失 | `ImportError` → warning + fallback |
| `min_image_distance` 不同取值 | ❌ 缺失 | 5Å / 10Å / 15Å |
| `SpacegroupAnalyzer` 高 symprec | ❌ 缺失 | `symprec=0.1` 在低对称性结构上是否稳定 |

### 4. infrastructure（优先级中）

| 场景 | 当前状态 | 说明 |
|------|---------|------|
| `vasp-sop cache status` | ❌ 缺失 | 空 DB、有数据、verbose 三种模式 |
| `vasp-sop cache verify` | ❌ 缺失 | DB 一致、有孤儿条目 |
| test isolation | ⚠️ 修了一半 | test_cache.py 的 `_isolate_cache` 用 `override_cache_root`，但所有 test 文件都需要独立隔离 |

## 建议标准

后续 PR 合并条件：
1. 新代码的每个公开函数至少有 1 个 happy path 测试
2. 每个 `except` 分支至少有 1 个测试触发
3. 测试不依赖真实 `~/.vasp_sop/`（用 `override_cache_root` 隔离）
4. `pytest tests/` 全部通过
