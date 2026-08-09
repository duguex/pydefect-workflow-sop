# Next Actions — +U/SOC 批次（2026-08-10，等 2026 根播种结束）

> Last updated: 2026-08-10. 触发点：2026 根 submit 队列接近 0（链式播种消化完）。
> 相关 ADR：0012（+U 永远打开）、0010（链式播种）。交接：/tmp/handoff-vasp-sop-2026-08-10.md。

## 执行顺序

```
INCAR 重生成（+U+SOC） → 续算重置 → 验证闭环
```

## 1. INCAR 重生成（+U + SOC 同批）

- **+U 体系**（永远打开已代码化——重生成自动带 LDAU）：Fe×4（BaAl4O7/CaAl4O7/SrAl4O7/SrGa4O7:Fe）+ ZnO + **BaAl2B2O7:Fe**（dopant Fe 已配，cpd 56 相含 Fe 21）
- **SOC 体系**（plan 已配 soc: true——重生成自动 LSORBIT+ISYM=-1）：Gd2GaSbO7:Bi/La2SrSc2O7/La2Zr2O7/Y2Sn2O7/Y2Ti2O7（CsPbBr3 已有）
- 脚本：`/tmp/regenerate_incar_full.py <root>`（prepare_inputs 带 extra_uis="SIGMA 0.02 LORBIT 11"、charge=q）
- 验证：INCAR 有 `LDAU=True`+`LDAUU`（Fe=3/Zn=5）、`LSORBIT=.TRUE.`（SOC 体系）；`verify_nelect` 0 问题

## 2. 续算重置（用户定：保留 CONTCAR，非从头）

- 已收敛的：清 OUTCAR/vasprun（防 backfill 不重跑）→ **保留 CONTCAR** → `batch retry` → 提交前把 CONTCAR 复制为 POSCAR（+U 起点）
- 在跑的：跑完 restart 吃新 INCAR（自然续算）
- 触发：`crisp cancel --status submit` + 重置（Gd 36 先例；349 stale 先例）

## 3. 验证闭环

- Fe 磁矩局域化（Fe3+ ~4-5 μB）；与无 U 结果形成能对比（预期差 >0.1 eV）
- SOC 体系抽查 LSORBIT；收敛率/播种数据继续监控

## 已知陷阱

- Ba4Al2O7（27.6Å>25Å）在 BaAl4O7/BaAl2B2O7 已排除（crisp 拒收）
- OUTCAR 收敛 tail 窗口 256KB（勿改小）；JobStore 会 stale（用 reconcile/重置）
- defect ISPIN=2 是 vise 模板默认——勿显式覆盖
- hubbard_u plan 字段已废弃（永远打开）；soc 字段仍生效（plan 配置）
- 等待链的非根自动解锁——勿手动提交

---

# Next Actions — Architecture Repair

# Next Actions — Architecture Repair

> Last updated: 2026-07-20. Source: Architecture Review → GitHub #102.
> Read this file, then `gh issue view <N>` for full acceptance criteria.

## Execution Order

```
#96 → #103 → #95 → #93
```

P2 (#94, #97, #98, #99, #100, #101) interleaved anytime. #40, #51 independent.

---

## #96 — Fix daemon NameError (P0, ~10 min)

**File:** `vasp_sop/cli/main.py`

**Fix:** Add missing imports at top of file:
```python
from vasp_sop.core.batch_lifecycle import (
    daemonize as _batch_start,
    stop as _lifecycle_stop,
    is_stop_requested,
)
```

**Verify:** `vasp-sop batch start /tmp/test && vasp-sop batch stop /tmp/test` — no NameError.

---

## #103 — Domain abstractions (P1, largest item)

### 103a. System class

Create `vasp_sop/core/system.py`:

```python
class System:
    def __init__(self, root: Path, config: PipelineConfig):
        self.root = root
        self.config = config
        self.name = root.name

    @property
    def cpd_dir(self) -> Path: return self.root / "cpd"
    @property
    def uc_dir(self) -> Path: return self.root / "unitcell"
    @property
    def defect_dir(self) -> Path: return self.root / "defect"
    @property
    def target_dir(self) -> Path: ...  # resolve from cpd/ or unitcell/structure_opt

    def phase(self) -> str: ...  # land: see System.phase() in core/system.py (landed)
    def defect_dirs(self) -> list[Path]: ...  # filtered, no junk
```

Migrate `_advance_one_system` to accept `System` instead of dict.

### 103b. Pydefect adapter

Create `vasp_sop/defect/pydefect_adapter.py`:

```python
def calc_results(dirs: list[Path], cwd: Path) -> list[CrResult]: ...
def efnv(dirs: list[Path], cwd: Path, ...) -> list[EfnvResult]: ...
def defect_energy_summary(cwd: Path, ...) -> SummaryResult: ...
```

- Import from `libs/` directly (Python API, not subprocess)
- Return structured dataclass per-dir (success/failed/skipped + parsed values)
- Replace `run_local(f'pydefect_vasp cr ...')` calls in `analysis.py`

### 103c. Explicit state markers

Add `state.json` per system root:
```json
{"phase": "CHEM_POT_DIAGRAM", "updated": "...", "history": [...]}
```

`System.phase()` reads state.json first, falls back to filesystem inference for migration.

---

## #95 — Wave decoupling (P1, depends on #103)

Split `_advance_one_system` into:

```python
def wave1_optimize(sys: System, js: JobStore, dry_run: bool) -> None: ...
def wave2_submit(sys: System, js: JobStore, dry_run: bool) -> None: ...
def wave3_postprocess(sys: System, dry_run: bool) -> AnalyzeStatus: ...
```

- Each has explicit preconditions (assert artifacts exist)
- No cross-wave directory access except via `System` handoff methods
- Move eager defect-building from wave1 to wave2 (or a `prepare()` step)
- `_advance_one_system` becomes: determine phase → call appropriate wave

---

## #93 — CPD correctness (P1, depends on #95)

Once waves are independent:
```python
# vasp-sop cpd run <system_dir> -f FORMULA
sys = System(root, config)
wave2_submit(sys, ...)   # competing phases only
wave3_postprocess(sys)   # CPD solving, stops before UC/defect
```

Plus: deterministic composition selection (lowest energy-per-atom), `cpd_excluded_phases.yaml`.

---

## Quick wins (P2, no dependencies)

| Issue | What to do | File(s) |
|-------|-----------|---------|
| #98 | Rewrite FEATURES.md cache + batch sections | `FEATURES.md` |
| #99 | Extract `_parse_max_f` → `vasp.io.parse_max_force`; create `tests/conftest.py` | `vasp/io.py`, `tests/` |
| #97 | Add `__enter__`/`__exit__` to JobStore; pass instance through batch loop | `core/job_store.py`, `cli/main.py` |
| #94 | Add analyze_status column to `batch status` output; remove `_MP_FLAG` | `cli/main.py`, `materials/mp.py` |
| #100 | Filter `defect_new/` and non-`Name_Charge` dirs in scans | `defect/analysis.py`, `cli/main.py` |

---

## Design decisions (do not revisit)

- GitHub Issues = single issue tracker (no local `issues/`)
- `libs/` forks are canonical; integrate via Python import, not subprocess
- Wave decoupling = independent invocability, not just file moves
- Phase state → explicit `state.json`, filesystem as migration fallback
