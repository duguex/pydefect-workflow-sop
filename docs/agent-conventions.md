# Agent coding conventions (vasp_sop)

> **Audience**: coding agents changing this repo.
> **Entrypoint**: root [`AGENTS.md`](../AGENTS.md).
> **Human start**: root [`README.md`](../README.md).

> 架构文档: [`README.md`](README.md) — `BatchOrchestrator.run` 流程、`System.phase()` 阶段机、`advance_one_system` 各阶段操作、JobStore 状态模型、CONTCAR 重启逻辑。

## 项目定位

vasp-sop (v0.1.0, MIT) 是一个 **VASP 点缺陷高通量计算管线框架**。

### 要解决的问题

点缺陷 DFT 计算（空位、替位、填隙等）流程极其繁琐：MP 下载竞争相 → 化学势相图 → 超胞构建 → 枚举几十个缺陷/电荷态 → 每个配 VASP 输入 → 提交 ~100 个作业 → CONTCAR 重启循环 → eFNV 修正 → 形成能计算 → 出图。涉及 6+ 个独立工具（pydefect、doped、vise、pymatgen、Maggma、MP API），手动串联极易出错、无法规模化。

vasp-sop 是一个**编排层**，把整条链路封装成一条命令。给定化学式和可选掺杂元素，自动完成从完美晶胞优化到形成能/能级图谱的端到端计算。

### 核心能力

| 维度 | 实现 |
|---|---|
| 一次配置 | `plan.yaml` 定义化学式、掺杂、泛函、超胞参数 |
| 自动阶段推进 | `STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE` 状态机 |
| 一键批量 | `vasp-sop batch run .` 串行推进所有体系 |
| 三波 VASP 调度 | Wave 1: 结构优化 → Wave 2: 竞争相+能带+DOS+介电+缺陷全并行 → Wave 3: 后处理 |
| 容错 | CONTCAR 重启（最多 20 次）、12 种错误诊断、单体系失败不阻塞其他 |
| 结果缓存 | vasp-cache v0.3.0 SQLite identity cache |
| 后处理编排 | 11 步 pydefect 管线 → `defect_energy_summary.json` |

### 不是什么

- ❌ **不是 DFT 代码** — 它调 VASP，不做电子结构计算
- ❌ **不是材料数据库** — 它读写数据，不存储材料知识
- ❌ **不是作业调度器** — 它走 `crisp`/`mpirun` 提交，不取代 Slurm

### 一句话

**把 VASP 点缺陷计算从手动脚本串联变成可配置、可串行推进、可复现的高通量管线。**

## Project Overview

vasp-sop depends on [vasp-cache](https://github.com/duguex/vasp-cache)
for VASP calculation result storage and deduplication.
Given a chemical formula and optional dopant elements, it automates
the end-to-end pipeline: competing phase search → chemical potential diagram →
unitcell properties → supercell construction → defect enumeration → VASP
submission → formation energy analysis.

The project manages ~38 host-material systems across 30+ competing phases each,
submitting and tracking hundreds of VASP jobs through the HPC cluster scheduler
`crisp`.

## Architecture & Data Flow

```
CLI (vasp-sop command)
  │
  ├── batch run .          ← multi-system pipeline orchestrator
  │   └── advance_one_system()  ← per-system state machine (core/orchestrator.py)
  │       ├── STRUCTURE_OPT → submit structure_opt VASP
  │       ├── COMPETING      → submit/check competing phases
  │       ├── CHEM_POT_DIAGRAM → run chemical potential diagram
  │       └── UNITCELL_DEFECT → build defect structures + submit VASP
  │
  ├── defect build .       ← standalone defect structure generation
  ├── cache status/query/verify/migrate
  └── materials fetch      ← MP query + download
```

### Three-Wave VASP Scheduling

The batch orchestrator in `core/orchestrator.py` (`BatchOrchestrator` / `advance_one_system`) uses a three-wave model:

| Wave | Phase | Work |
|------|-------|------|
| 1 | STRUCTURE_OPT | structure_opt + generate all inputs while target runs |
| 2 | COMPETING + UC + DEFECT | competing phases, band/dos/dielectric, perfect, all defects |
| 3 | POST-PROCESSING | pydefect analysis, formation energy summary |

Wave 2 submits all independent VASP jobs in parallel (competing phases + unitcell
sub-tasks + defect calculations). Wave 3 runs after all Wave 2 jobs complete.

### Key Modules

| Module | Path | Role |
|--------|------|------|
| CLI | `vasp_sop/cli/main.py` | argparse dispatch (8 subcommands); thin wrapper around `BatchOrchestrator` |
| Config | `vasp_sop/core/config.py` | `PipelineConfig` dataclass, `plan.yaml` I/O, `generate_config()` |
| Jobs | `vasp_sop/core/jobs.py` | VASP submission (crisp/local), `VaspJob` hierarchy, `run_local()` |
| State | `vasp_sop/core/job_store.py` | JobStore (SQLite) — per-calculation VASP job states (`submitted`/`converged`/`failed`), plus `tracked` table for active submissions |
| Cache | `vasp_sop/core/cache.py` | vasp-cache adapter (SQLite identity + BLOB) |
| Builder | `vasp_sop/defect/builder.py` | Supercell (doped/pydefect) + defect enumeration + VASP inputs |
| CPD | `vasp_sop/defect/cpd.py` | Competing phase diagram pipeline |
| Unitcell | `vasp_sop/defect/unitcell.py` | Perfect-cell band/DOS/dielectric |
| Analysis | `vasp_sop/defect/analysis.py` | Formation energy post-processing (10 pydefect steps) |
| Batch orchestrator | `vasp_sop/core/orchestrator.py` | Three-wave VASP orchestration (`BatchOrchestrator`, `advance_one_system`, `wave1_optimize`/`wave2_submit`/`wave3_postprocess`) |
| Compute | `vasp_sop/defect/compute.py` | Defect VASP execution with CONTCAR restart loop |
| VASP I/O | `vasp_sop/vasp/io.py` | `check_task_complete()`, `prepare_inputs()`, `restart_from_contcar()`, `has_vasprun()` |
| VASP Errors | `vasp_sop/vasp/errors.py` | Error pattern diagnosis (12 modes), fix suggestions |
| Materials | `vasp_sop/materials/mp.py` | MP API integration, parameter inference |

### Data Flow

```
plan.yaml → PipelineConfig
  │
  ├── materials.fetch_candidate_phases() → cpd/  (POSCAR downloads)
  ├── cpd.run_cpd() → composition_energies.yaml → standard_energies.yaml → target_vertices.yaml
  ├── unitcell.build_unitcell_yaml() → unitcell.yaml  (VBM/CBM/ε)
  ├── builder.build_all() → defect/  (supercell + structures + inputs)
  │   ├── _build_supercell()      ← doped or pydefect
  │   ├── _generate_defect_list() ← pydefect ds
  │   └── _generate_vasp_inputs() ← serial per-directory
  ├── compute.run_vasp() → OUTCAR per defect  (CONTCAR restart loop, max 20 attempts)
  └── analysis.analyze() → defect_energy_summary.json
```

### Batch Pipeline State Machine

Each system cycles through phases determined by `System.phase()`:

```
STRUCTURE_OPT → COMPETING → CHEM_POT_DIAGRAM → UNITCELL_DEFECT → COMPLETE
```

- Phase is determined by filesystem state (OUTCAR presence, convergence, target_vertices.yaml, etc.)
- `--dry-run` does one pass and exits (no VASP submission)
- Cache hit in STRUCTURE_OPT/COMPETING skips submission (saves `.target_submit.json` with `"cached"`)
- Errors in `advance_one_system` are caught per-system — one failure doesn't block others

## Key Directories

```
vasp_sop/
├── cli/main.py          ← CLI entry point; thin wrapper around `BatchOrchestrator`
├── core/                ← Config, jobs, state, cache
├── defect/              ← Pipeline stages (cpd, unitcell, builder, compute, analysis, _legacy)
├── vasp/                ← VASP I/O layer + error diagnosis (convergence, prepare_inputs, diagnose_failure)
├── materials/           ← Materials Project integration
└── __init__.py          ← version = "0.1.0"

tests/
├── test_cache.py        ← Cache tests (vasp-cache adapter + MP)
├── test_cli.py          ← CLI + dry-run + batch status tests
├── test_builder.py      ← Supercell builder tests
├── test_config.py       ← Config validation + YAML round-trip
├── test_cpd.py          ← CPD regression tests (issues #0001, #0002)
├── test_defects.py      ← Convergence detection 7-case matrix
├── test_jobs.py         ← Subprocess + input-ready tests
├── test_parser.py       ← VASP parsing layer tests (TaskDoc + regex)
├── test_errors.py       ← Error diagnosis tests (12 modes)
├── test_job_store.py    ← JobStore tests

issues/                  ← GitHub issue references (local copies)
```

## Development Commands

```bash
# Run all tests
python3 -m pytest tests/

# Run specific test file
python3 -m pytest tests/test_cache.py -v

# Run single test
python3 -m pytest tests/test_cli.py::TestCachePutGet::test_roundtrip -v

# Run with print output
python3 -m pytest tests/test_cli.py -v -s

# Run batch dry-run (no VASP submission)
vasp-sop batch run /path/to/project --dry-run

# Run batch for real
vasp-sop batch run /path/to/project

# Check cache status
vasp-sop cache status --verbose
```

### CLI Subcommands

| Command | Description |
|---------|-------------|
| `batch run` | Multi-system pipeline orchestrator (main workflow) |
| `pipeline` | Single-system pipeline |
| `defect build` | Standalone defect structure generation |
| `cache status/query/verify` | vasp-cache SQLite inspection & query |
| `materials fetch` | Materials Project query + download |
| `vasp` | VASP operations |
| `cpd` | CPD stage standalone |
| `unitcell` | Unitcell stage standalone |

## Code Conventions & Patterns

### Configuration

- Project config is `plan.yaml` with a nested dict structure
- Loaded via `PipelineConfig.from_yaml(path)` which returns a validated dataclass
- `from_plan(dict)` / `to_plan()` handle the dict↔dataclass conversion
- `DEFAULT_PLAN` provides the template for `generate_config()`
- Validation via `__post_init__` with `ValueError`
- Legacy JSON format supported via `from_legacy_json()` migration
- All three supercell keys (`min_atoms`, `max_atoms`, `min_distance`) are emitted unconditionally
  so round-trip never silently drops data — downstream code reads only what it cares about

### Cache

- vasp-cache v0.3.0 — SQLite identity cache at ``$VASP_CACHE_ROOT``:
  - 6-layer SHA-256 identity: formula, INCAR, structure, KPOINTS, POTCAR, lattice
  - zlib-compressed BLOBs: OUTCAR, vasprun.xml, CONTCAR
  - Structured extracts: final_energy, converged_ionic, total_mag, n_ionic_steps
- ``vasp_results_put(src_dir, *, cache_root=None, overwrite=False)`` — stores
  VASP results via vasp-cache identity; returns key or None
- ``vasp_results_get(formula, key)`` — retrieves cached metadata
- ``cache_lookup(src_dir)`` — directory-based cache check
- ``restore_from_cache(src_dir)`` — fetches OUTCAR/CONTCAR/vasprun from cache
- ``restore_from_key(key, target_dir)`` — fetches via explicit key (atomic)
- ``query(formula=..., limit=...)`` — cross-project query (formula + limit only)
- ``list_cache()`` / ``cache_stats()`` — listing and aggregate statistics
- ``override_cache_root(path)`` — isolates both vasp-cache and SOP paths for testing

### Error Handling

- CLI commands catch top-level exceptions and log via `logger.exception()`
- Batch pipeline: errors in `advance_one_system` are caught per-system, not propagated
- `run_local()` raises `RuntimeError` on non-zero exit or timeout
- `convergence_verdict()` returns `ConvergenceVerdict` dataclass; never raises
- Cache functions catch exceptions during pymatgen parsing, log warnings, store what they can
- Per-system isolation: one failing system doesn't block others in batch mode

### Execution Model

- **Serial batch advancement**: systems advance one-by-one in `BatchOrchestrator.run` — no process pool, no orphan workers
- **Serial input generation**: VASP input file preparation runs in serial (avoids NFS thundering-herd on shared storage)
- **VASP-level parallelism**: individual VASP jobs each request multiple MPI ranks via Slurm (`crisp submit -n TASK_NAME`)
- VASP jobs run independently per-phase, submitted via crisp or local subprocess

### Supercell Construction

Dual backend selected by `config.supercell_tool`:

| Tool | Constraint | CLI flag |
|------|-----------|----------|
| `doped` | Minimum image distance (`min_distance`) | Preferred — canonical for distance constraints |
| `pydefect` | Atom count bounds (`min_atoms`/`max_atoms`) | No `--min_distance` flag in pydefect CLI |

- `_build_supercell_doped` delegates symmetry site grouping to `vise.util.structure_symmetrizer.StructureSymmetrizer`
  (handles centering, time-reversal, angle tolerance; sorts `equivalent_atoms` indices)

### Job Management (CRISP)

All cluster job operations go through the `crisp` CLI (never raw `sbatch`/`scancel`):

```bash
crisp submit              # Submit VASP job
crisp cancel -n TASK_NAME # Cancel job
crisp jobs                # List all jobs (JSON output)
```

**Layout (current crisp):** results land **in the work dir** (no `output/`
subdir). Slurm stdout/stderr log: **`{slurm_job_id}.log`**.
`move_crisp_outputs()` remains for **legacy** `output/` trees only (mtime merge).


### VASP Convergence Detection

`convergence_verdict()` in `vasp_sop/vasp/convergence.py` (detail: `docs/architecture/06-convergence.md`):

- **Relaxation vs non-relaxation:** force/EDIFFG only for ionic relax
  (`IBRION∈{1,2,3}` and `NSW>1`). Single-point / DFPT / NSW≤1 use job completion
  (`General timing`), never ionic forces.
- **Structural relax (VASP semantics):** look for
  `reached required accuracy - stopping structural energy minimisation` in the log/OUTCAR.
  Code currently implements the equivalent force gate; production scans match that string.
- **OUTCAR location:** `{calc}/OUTCAR` or **`{calc}/output/OUTCAR`** (crisp pull path).
- Run tags (NSW/IBRION/EDIFFG) preferred from **this OUTCAR**, not a rewritten INCAR after restart.
- Returns `bool`, never raises; tests in `tests/test_defects.py`

CONTCAR restart (`restart_from_contcar` / poll unconverged):
- Copies CONTCAR → POSCAR, sets ISTART=1
- **User policy:** re-runs for missing `vasprun` must **not** rewrite NSW/IBRION (same physics params)
- Stalled detection / attempt limits in `_handle_unconverged_poll` and `defect/compute.py`


### Imports

- Standard library + third-party imports at top of file
- Heavy/conditional imports (`pydefect`, `vise`, `pymatgen`) inside functions
  (deferred to call-time, which also makes `monkeypatch.setattr` on module
  attributes work for testing)
- Local module imports use full `vasp_sop.xxx` paths

### Persistence

- Job state: `~/.vasp_sop/jobs.db` (SQLite — `job_history`: per-calculation `submitted`/`converged`/`failed`; `tracked`: active submissions awaiting polling)
- Results cache: vasp-cache v0.3.0 (SQLite identity + BLOB)
- MP combo cache: `~/.vasp_sop/mp_cache/` (POSCARs + POTCARs on disk)
- State is filesystem-based: phase determined by OUTCAR existence, convergence, YAML files

## Testing & QA

### Framework

- pytest (no conftest.py, no pytest markers, no parameterization)
- Each scenario is a separate test method
- `tmp_path` fixture used universally for filesystem I/O tests

### Fixture Patterns

| Fixture | Usage |
|---------|-------|
| `tmp_path` | Filesystem sandbox per test |
| `monkeypatch` | Mock VASP/crisp/pydefect subprocess calls |
| `capsys` | Capture stdout for CLI output assertions |
| `@pytest.fixture(autouse=True)` | Test-class-level heavy patching |

### Cache Isolation

```python
@pytest.fixture(autouse=True)
def _isolate_cache(self, tmp_path):
    from vasp_sop.core.cache import override_cache_root
    override_cache_root(tmp_path / ".vasp_sop")
```

Additional manual reassignment of module-level path globals may be needed
(cache module imports evaluate before monkeypatch takes effect).

### Mocking Strategy

- External subprocess calls (`crisp`, `sbatch`, `pydefect`, `vise`) are
  mocked via `monkeypatch.setattr` on `subprocess.run` or the module import path
- No real subprocess calls in tests
- MPI/VASP binaries never invoked
- Test data is synthetic (dummy OUTCAR files, fake JSON payloads)
- `_make_system_dict()` helper constructs system state dicts without real filesystem

### Test Coverage

Well-tested:
- Cache layer (hit/miss/partial/overwrite, SQLite + MP)
- Config validation + YAML round-trip
- VASP convergence detection (7-case matrix)
- Pipeline state persistence
- CLI dry-run modes and batch status reporting
- Supercell builder (doped + pydefect, error cases, sorted equivalent_atoms)

Notable gaps:
- `submit_vasp()` / `run_vasp()` have no unit tests
- MP network download paths uncovered
- No end-to-end integration tests
- State error-handling paths uncovered

### Test Naming Conventions

- Class: `Test<Feature>` (e.g., `TestCrispActiveDirs`)
- Method: descriptive snake_case (e.g., `test_dry_run_skips_crisp_subprocess`)
- Docstring: one line describing the scenario and issue reference
- Helper methods: prefixed with `_` (e.g., `_make_system`, `_make_uc_df_system`)

## Important Files

| File | Purpose |
|------|---------|
| `vasp_sop/cli/main.py` | CLI entry; thin wrapper calling `BatchOrchestrator.run` and `advance_one_system` |
| `vasp_sop/core/config.py` | `PipelineConfig`, `plan.yaml` generation |
| `vasp_sop/core/cache.py` | vasp-cache v0.3.0 adapter |
| `vasp_sop/core/job_store.py` | JobStore, per-calculation state tracking |
| `vasp_sop/defect/builder.py` | Supercell + defect generation |
| `vasp_sop/defect/cpd.py` | Chemical potential diagram |
| `vasp_sop/core/orchestrator.py` | Batch orchestrator / three-wave schedule (`BatchOrchestrator`, `advance_one_system`, `wave1_optimize`/`wave2_submit`/`wave3_postprocess`) |
| `vasp_sop/defect/compute.py` | Defect VASP execution with CONTCAR restart + error diagnosis |
| `vasp_sop/defect/analysis.py` | Formation energy post-processing |
| `vasp_sop/vasp/io.py` | `check_task_complete()`, `prepare_inputs()`, `restart_from_contcar()`, `has_vasprun()` (convergence moved to `vasp/convergence.py`) |
| `vasp_sop/vasp/errors.py` | VASP error pattern diagnosis (12 modes), `diagnose_failure()` |
| `vasp_sop/materials/mp.py` | MP query + parameter inference |
| `pyproject.toml` | Dependencies, entry point, pytest config |
| `PROJECT.md` | Detailed pipeline SOP (Chinese) |
| `FEATURES.md` | User-facing feature catalog (CLI, pipeline, config, all capabilities) |

## Runtime Requirements

- Python >= 3.10
- Dependencies: pymatgen, pydefect, vise, numpy, pandas, pyyaml, emmet-core, vasp-cache
- HPC cluster access with `crisp` scheduler agent
- Materials Project API key (`MP_API_KEY` or `PMG_MAPI_KEY`)
- VASP binary via singularity container (`/mnt/shared/vasp_latest.sif`)
- `crisp` CLI must be on PATH for HPC operations

## Production Instances

### `2025_undergo_spin_defect`

**Path:** `/mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect/`
**Systems:** 40 个目录，29 个有 `plan.yaml` 的活跃系统
**Batches:** 首批 P4（10 个）+ 第二批新材料（29 个）

| Phase | 数量 | 系统 |
|-------|------|------|
| ✅ COMPLETE | 8 | AlN, BaO, CaO, CeO₂, GaN, MgO, MoS₂, SrO |
| ▶️ UNITCELL_DEFECT | 30 | most remaining hosts (VASP/postprocess incomplete) |
| ⏳ STRUCTURE_OPT | 2 | CaMg₂(SO₄)₃, ZnO |
| 备注 | | hBN/SiC 等缺 band/dos `vasprun.xml` 已重提；后处理常卡在 perfect/vasprun.xml |

**常用命令：**

```bash
# 推进所有系统
cd /mnt/shared/home/2sidesniddle/vasp/2025_undergo_spin_defect
vasp-sop batch run .

# 干运行预览
vasp-sop batch run . --dry-run

# 查看批处理状态
vasp-sop cache status --verbose
```
## Known Issues

- `cache_target_results` in CHEM_POT_DIAGRAM calls `calc_results_put` then `calc_cpd_put`;
  if the target dir lacks OUTCAR (e.g. cached result restored without files),
  the put silently skips. Verify converged flag before calling.
- CPD target composition lookup in `relative_energies.yaml` can fail
  intermittently (pydefect key format instability). See `issues/0001-srte-cpd-target-lookup-false-positive-failure.md`.
- 4-element CPD diagrams fail in pydefect (halfspace >3D). Handled via
  dimension check in `cpd.py`. See `issues/0002-skip-4d-cpd-diagram.md`.
- Dry-run UNITCELL_DEFECT phase reports "already complete" when `defect_energy_summary.json`
  exists, "would post-process" when artifacts are present but analysis hasn't run,
  and "post-process blocked" when artifacts are missing.
- `compute.run_vasp()` stalled detection threshold (99% of previous max-force)
  may trigger false positives for some systems.
