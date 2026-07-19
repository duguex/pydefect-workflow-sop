# vasp-cache v0.3.0 适配与 Canonical Target 生命周期修复

> **迁移注意**：handoff 现在强制 canonical target 有 7 文件；历史 legacy 项目
> 若 `cpd/<target>/` 缺 INCAR/KPOINTS/POTCAR，需先 `batch generate-inputs` 补齐
> 再 `batch run`，否则 CPD preflight 会失败。

**日期:** 2026-07-19
**项目:** vasp-sop ↔ vasp-cache 集成
**测试:** 251 passed, 7 skipped

> **验收边界**：mock CPD 的 cache-only pipeline test 已通过（断言零 VASP submit +
> handoff）；`_run_pipeline` legacy 路径代码已修改但无专项集成测试；真实
> CsEuCl3 `compute_chemical_potentials`（pydefect）未执行。

---

## 1. vasp-cache v0.3.0 API 适配

### 问题
vasp-sop 的 `vasp_sop/core/cache.py` 适配层使用已删除的旧版 vasp-cache API
（`content_hash`, `fingerprint._incar_fingerprint`, `parse.MAX_LATTICE`,
`parse._extract_tags`, `paths.cache_root`），导入失败。

### 修改

**`vasp_sop/core/cache.py`** — 全部 API 替换为 v0.3.0：

| 旧 API | 新 API |
|--------|--------|
| `vc_fetch(src_dir)` | `fetch(key, target_dir)` |
| `vc_put(src_dir, formula, task_name)` | `put(src_dir)` 返回 identity key |
| `vc_content_hash(src_dir)` | `identity_for_directory(dir).key` |
| `vc_has(src_dir)` | `has(src_dir)` （签名兼容） |
| `vc_query(formula, functional, calc_type, ...)` | `query(formula, limit)` 不支持旧 filter，非零参数抛 `ValueError` |
| `vc_get_meta(src_dir)` | `get_meta(input_dir=src_dir)` |
| `fingerprint._incar_fingerprint` | 移除（无调用方） |
| `parse.MAX_LATTICE` | 本地定义 `MAX_LATTICE = 25.0` |
| `parse._extract_tags` | 移除（无调用方） |
| `paths.cache_root` | 移除（v0.3.0 使用 `VASP_CACHE_ROOT` 环境变量或默认 `~/.cache/vasp_cache`） |

**`restore_from_cache` staging fix**：vasp-cache `fetch()` 拒绝覆盖已存在目录。
改为 fetch 到临时 staging 目录，成功后复制输出到目标，`finally` 清理。

**`--cache-root` CLI flag**：`vasp-sop cache` 子命令新增 `--cache-root` 选项，
透传给底层所有 vasp-cache 函数。

**`_handle_cache` CLI 重写**：status/query/verify 使用 v0.3.0 字段名
（`final_energy`, `converged_ionic`, `identity_key`, `source_path`, `created_at`）。
移除旧 `query` 的 `--functional/--calc-type/--bandgap-min/--max-lattice/--tags` flag。
移除对已删除 `_get_stores()`/`_parse_and_build()` 的引用。

### 验证
- 竞争相 CsCl 全链路：`put → status → query → cache_lookup → restore_from_cache`
- 恢复后 OUTCAR/CONTCAR byte-identical
- 共享 cache `/mnt/shared/vasp_cache` 当前状态以 `vasp-sop cache --cache-root /mnt/shared/vasp_cache status --verbose` 为准（测试期间曾有 9→13 条重复，备份 `.bak.*` 已保留）

---

## 2. Canonical Target 生命周期修复

### 问题
vasp-sop 的 target 结构优化计算在两个目录中有不同表示：

| 目录 | 作用 | 文件完整性 |
|------|------|-----------|
| `cpd/CsEuCl3_mp-1213256` | CPD target | 缺 INCAR/KPOINTS/POTCAR |
| `unitcell/structure_opt` | 被 handoff 当作 source | 完整 7 文件 |

问题链：
1. `handoff_target_results` 方向错误：从 structure_opt **拷到** target（上表中方向反了）
2. `_run_pipeline` 提前调用 `_prepare_all_inputs` 在 structure_opt 生成输入——此时 target 还未计算
3. `_batch_generate_inputs --unitcell` flag 被 parser 定义但函数完全忽略
4. STRUCTURE_OPT 阶段不提交 target VASP
5. `batch submit` 在 `_handle_batch` 中无 dispatch

### 修改

**handoff 方向反转**（`vasp_sop/defect/cpd.py`）：
- `handoff_target_results(cpd_target, structure_output)`: 从 `cpd_target`（canonical）拷到 `structure_output`（镜像）
- `ensure_target_results`: 验证 cpd_target 有 7 文件，然后 handoff
- 所有 7 文件（POSCAR/INCAR/KPOINTS/POTCAR/OUTCAR/CONTCAR/vasprun）前置校验
- 输出文件无条件覆盖（stale 结构被 canonical 覆盖）

**STRUCTURE_OPT 提交 + cache 恢复**（`vasp_sop/cli/main.py`）：
```
check_converged(td)          → record converged
cache_lookup(td) → restore  → record converged
input_ready(td)              → _submit_or_skip(td, "target")
```
- `restore_from_cache` 失败不标记完成
- 阶段末尾重新评估 phase 继续到 COMPETING

**`batch submit` dispatch**：
- `_handle_batch` 新增 `elif args.batch_action == "submit"` 分支

**_run_pipeline 启动**：
- 移除提前的 `_prepare_all_inputs(uc_root, target_dir, config)` 调用
- 新增 canonical target `prepare_inputs(target_dir, config)`（使用 `input_ready` 检查）
- 恢复 defect `_build_defects`

**`_batch_generate_inputs --unitcell`**：
- 实现 post-handoff 检查：需要 `unitcell/structure_opt/CONTCAR`
- 使用 `_target_dir()` 精确解析 target（基于 `plan.yaml` 的 `poscar_src`）
- 无 CONTCAR → skip 并输出计数

**`_copy_input_from_opt` CONTCAR 优先**（`vasp_sop/defect/unitcell.py`）：
- `band/dos/dielectric` 任务从 `CONTCAR`（优化后结构）复制 POSCAR
- 仅 CONTCAR 缺失时回退到 `POSCAR`

### 验证
- CPD tests: 18 passed（handoff 反转 + 7 文件校验 + self-handoff 幂等）
- `test_structure_opt_cache_hit_skips_vasp_submission`: target cache 命中 → 零 `submit_vasp`
- `test_cache_only_full_pipeline_zero_vasp_submit`: STRUCTURE_OPT → CPD，零 VASP（mock CPD）
- `test_target_dir_gets_inputs_generated`: batch generate 创建 target 输入
- `test_unitcell_skips/generates`: `--unitcell` flag 行为正确

---

## 3. 已知边界

| 边界 | 说明 |
|------|------|
| `_run_pipeline` legacy 单体系路径 | 代码已修但未执行专项集成测试 |
| 真实 CPD（`compute_chemical_potentials`/pydefect） | 零 VASP test 中 mock 了 CPD 计算 |

---

## 4. 代码改动清单

| 文件 | 改动 |
|------|------|
| `vasp_sop/core/cache.py` | v0.3.0 API 全量替换；`restore_from_cache` staging fix；`MAX_LATTICE` 本地定义 |
| `vasp_sop/core/logging.py` | managed FileHandler 替代布尔 gate；`teardown_file_logging` |
| `vasp_sop/cli/main.py` | handoff 调用方向；STRUCTURE_OPT 提交+cache；`batch submit` dispatch；`--cache-root`；`_handle_cache` 重写；`_run_pipeline` target 输入；`--unitcell` 实现；phase re-eval 恢复 |
| `vasp_sop/defect/cpd.py` | handoff 方向 target→structure_opt；7 文件前置校验 |
| `vasp_sop/defect/unitcell.py` | `_copy_input_from_opt` CONTCAR 优先 |
| `tests/test_cache.py` | v0.3.0 模式重写（7 文件 helper） |
| `tests/test_cache_adapter.py` | 7 文件 fixture |
| `tests/test_cli.py` | cache/CPD/batch/unitcell/lifecycle 测试更新 |
| `tests/test_logging.py` | managed handler fixture |
| `tests/test_parser.py` | 移除（已删除的 vasp-cache v0.2 API） |
| `tests/test_unitcell.py` | CONTCAR 优先测试 |
