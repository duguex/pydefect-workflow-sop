# Next Actions — 2026-08-11 阶段收尾

> **CaAl4O7 相位 COMPLETE**（08-11 01:41 修复后）：72/72 defect → 22 形成能 + transition_levels + HTML；analyze full。
> **SrAl4O7 analyze full 72/72（22 类型含 5 个 Fe）**——Fe 形成能补齐（化学势图自动重建）；相位差最后 1 个 cpd 相 Sr[FeO2]2（自动 ionic restart 中，3 次上限后需参数决策：EDIFFG=-0.005 过严，力停在 0.03-0.04）。
> **本阶段修复链**（commit a417f59→**662e7d9**）：stale-converged 重判 / sidecar v2→v3（NELM 门只认最后离子步）/ cr 一致性门 / NELM=30 协议 / HTML 凸包+耳切 / **相位门反位过滤（ADR 0013）** / **cpd 相 ionic 自动续算（上限 3）** / **化学势图 stale 自动重建+重 analyze** / **analyze 类型覆盖门（防假 full）** / dei 接受 .yaml。480 passed。
> **已验证结论**：perfect 参数漂移（SIGMA 0.1→0.02 重算）能量差 3e-8 eV——零影响；主流 defect 单步电子 16-30（LOOP/LOOP+ 计数，勿用 grep "F="——误匹配 NGXF=）；Al13Fe4 的 NELM 警告在早期离子步（后续收敛）→ 自动转 converged，**无需重算**。
> **待办**（详见 /tmp/handoff-vasp-sop-2026-08-11.md）：
> 1. **Sr[FeO2]2**（SrAl4O7 唯一卡点）：fd313379（EDIFFG=-0.01 + duguex_5 长 QOS）跑中，等结果。
> 2. **La2Zr2O7 dielectric**：87a8b50f（修复协议 NSW=1/无 SOC/LREAL=False/长 QOS）跑中，预计 ~2h；完成后 La2Zr2O7 unitcell 三任务齐（band/dos 已收敛）。
> 3. 两阶段 SOC（ADR 0014 机制已实现，5 个 SOC 体系排到时启用）
> 4. Y2Ti2O7 Bi 缺陷重建（defect_in 早于 plan dopant Bi）
> 5. CaAl4O7 perfect/calc_results.json 缺失（无害，analyze 短路不补；将来完整 analyze 自动补）
> 6. 2025 恢复：11 个 NELM 警告目录重算 + perfect 参数漂移检查（INCAR↔OUTCAR 回显）——**注意 NELM 门 refine 后部分目录可能自动转 converged（先重判再重算）**
> 7. poll giving-up 语义残留（不影响收敛，纯清理）
> 8. 填隙：全部 10 体系 plan `interstitials: false`——需要时开 plan + 重建（用户未定）
> 9. 缓存停用（用户搁置）
> 10. 已知现象：crisp agent.db 历史记录有清理/丢失机制（vasp-sop JobStore 为权威，不影响正确性）

# Next Actions — +U/SOC 批次（2026-08-10，**已执行** 13:30）

> **批次已完成**（2026-08-10 13:30）：INCAR 重生成 1559+17+80 目录（+U Fe=3/Zn=5、SOC LSORBIT）、续算重置 471 已收敛（清输出+CONTCAR→POSCAR+retry）、failed 重提 733、verify_nelect 修复（ADR 0013 门跳过被筛目录，commit 已落）。
> **BaAl2B2O7 Fe 缺陷修复**：fingerprint 触发重建（dopant Fe 生效——Fe_Al1/Fe_Ba1 等保留类已生成带 U；Fe_O* 35 个被 ADR 0013 门排除）。
> **晚间补充（16:30）**：**cpd 无 U 发现并修复**——vise CLI 的 set_hubbard_u 只对 defect task 生效，cpd（structure_opt 模板）INCAR 无 LDAU → Fe/Gd 相能量与 defect 不一致（形成能偏移）。`patch_incar_u`（io.py，U 表 Fe=3/Gd=5/Zn=5…）+ cpd 生成接入 + 存量 15 个 SrGa4O7:Fe 含 Fe cpd 已补丁（CONTCAR 续算起点）；COMPETING 提交改 ADR 0007 terminal 语义（failed cpd 只 auto_retry 一次，杜绝 ZBRENT 目录每 poll 重提——FeO 曾 56 次）。430 passed。已重启 loop。
> **剩余**：观察 +U/SOC 重算收敛（Fe 磁矩 ~4-5μB 抽查）、形成能对比、链播种恢复（根收敛后自动）；**Y2Ti2O7 cpd 含旧版 vise 的 Ti U=4 而 defect 无 U——不一致待用户拍板（Ti 政策）**。

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
- **阴-阳错位反位已排除（ADR 0013）**：is_valid_defect_dir 门自动跳过——被筛目录不重生成、不重提
- **NSW=100**（用户 2026-08-10 确认）：|q|≥5 反位等首轮 20 步不收敛的硬尾目录重生成时 NSW=100（vasp-sop-defect-convergence-nsw 配方）
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

## 两阶段 SOC（ADR 0014，2026-08-10 决策）

- **机制已实现**（commit）：`stage2_soc` plan 开关（默认 False）——阶段 1 非 SOC 收敛后自动补算（Bi_* 续算 / 其余 NSW=0 单点）；prepare_inputs 在 stage2_soc 模式下不加 LSORBIT
- **执行纪律**：单体系串行（按完成度）：CaAl4O7 → SrAl4O7 → Gd2GaSbO7:Bi → La2Zr2O7 → Y2Sn2O7 → La2SrSc2O7 → Y2Ti2O7（含 Bi 缺陷重建）；不破坏已算成果
- **遗留**：5 个 SOC 体系 INCAR 曾去 SOC（605 个）+ cancel 36 个——按用户指示保持现状；实施两阶段时统一处理（已算的补记 soc_done 避免重算）
- **Y2Ti2O7**：defect_in.yaml 早于 plan dopant Bi——含 Bi 缺陷缺失，实施时重建（BaAl2B2O7 同型案例）

## 单体系串行执行（2026-08-10 18:25 起）

- loop 已隔离：`vasp-sop-loop.service` ExecStart 加 `--exclude` 其余 9 体系——**只推进 CaAl4O7**（Batch run: 1 systems）
- CaAl4O7：72 有效 67 收敛 + Va_Al3_-3 running（最后一个）——收敛后 wave3 自动收尾（cpd 后处理已跑、unitcell 全收敛）→ COMPLETE
- 切换下一个体系：改 service（去掉目标体系的 --exclude）→ daemon-reload → restart
- 其他体系在跑的 8 个尾巴（Y2Ti2O7 4/BaAl2B2O7 1/La2Zr2O7 1/SrAl4O7 1）跑完自然结束，不 cancel
