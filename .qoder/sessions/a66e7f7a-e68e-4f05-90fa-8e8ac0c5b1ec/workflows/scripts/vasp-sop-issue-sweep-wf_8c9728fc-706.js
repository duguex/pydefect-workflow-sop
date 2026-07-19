
export const meta = {
  name: 'vasp-sop-issue-sweep',
  description: 'Fix all open GitHub issues for vasp-sop: P0 first, P2 in parallel, P1 chain sequential, docs last',
  phases: [
    { title: 'P0 Critical', detail: '#96 daemon NameError fix' },
    { title: 'P2 Parallel', detail: '#97 #99 #100 #104 #94 independent fixes in worktrees' },
    { title: 'P1 Chain', detail: '#103 → #95 → #93 sequential domain/wave/CPD refactor' },
    { title: 'Docs & Features', detail: '#98 #90 #101 documentation and regression tests' },
    { title: 'Enhancements', detail: '#51 #40 larger feature work' },
  ],
}

const REPO = '/home/duguex/vasp_sop'

// ─── Phase 1: P0 Critical ────────────────────────────────────────────
phase('P0 Critical')
log('Fixing #96: daemon mode NameError (P0)')

const fix96 = await agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #96 — "Fix daemon mode NameError: _batch_start/_lifecycle_stop undefined"

PROBLEM: cli/main.py calls _batch_start (~line 829), _lifecycle_stop (~line 845), and is_stop_requested (~line 1867) which are never imported. These cause NameError when batch start/stop/loop are invoked.

FIX:
1. Read vasp_sop/core/batch_lifecycle.py to confirm the actual exported names (daemonize, stop, is_stop_requested).
2. Add the proper import at the top of vasp_sop/cli/main.py:
   from vasp_sop.core.batch_lifecycle import daemonize as _batch_start, stop as _lifecycle_stop, is_stop_requested
3. Verify there are no other undefined references to these names in main.py.
4. Run: python3 -m pytest tests/ -x -q to ensure nothing breaks.
5. If tests fail, fix them.

Write the code fix. Commit with message: "fix: import batch_lifecycle names to resolve daemon NameError (#96)"`, {
  label: '#96 NameError fix',
  phase: 'P0 Critical',
})

// ─── Phase 2: P2 Parallel (worktree-isolated) ───────────────────────
phase('P2 Parallel')
log('Launching 5 parallel P2 fixes in isolated worktrees')

const p2Results = await parallel([
  // #97 JobStore connection reuse
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #97 — "JobStore: replace per-call connection with instance-level reuse"

PROBLEM: JobStore opens/closes a new SQLite connection for every method call. In the batch loop, dozens of JobStore() instantiations per system per cycle create unnecessary I/O overhead.

REQUIREMENTS:
1. Read vasp_sop/core/job_store.py to understand current implementation.
2. Add instance-level connection reuse: open connection in __init__, add __enter__/__exit__ for context manager support.
3. Ensure backward compatibility: per-call usage (creating new JobStore() each time) still works — the connection should be opened lazily or in __init__ and closed in close()/__exit__.
4. In vasp_sop/cli/main.py, find the batch loop and make it create ONE JobStore instance per cycle and pass it through, instead of calling JobStore() repeatedly.
5. Run: python3 -m pytest tests/ -x -q — all tests must pass.
6. Commit: "feat: JobStore instance-level connection reuse with context manager (#97)"

Write the code.`, { label: '#97 JobStore reuse', phase: 'P2 Parallel', isolation: 'worktree' }),

  // #99 Code hygiene
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #99 — "Code hygiene: convergence logic dedup + tests/conftest.py"

PROBLEM:
1. Convergence/max-force parsing is duplicated across vasp/io.py, cli/main.py, defect/compute.py (functions like _parse_max_f, _max_f, check_converged).
2. No tests/conftest.py — 22 test files each set up cache isolation independently.

REQUIREMENTS:
1. Find all duplicated max-force/convergence parsing functions (grep for _parse_max_f, _max_f, parse_max_force, check_converged).
2. Consolidate into a single parse_max_force(outcar_path) in the appropriate module (vasp_sop/core/ or vasp_sop/defect/ — wherever the existing canonical one lives).
3. Update all callers to use the canonical function.
4. Create tests/conftest.py with shared fixtures:
   - isolated_cache (monkeypatch cache paths to tmp)
   - mock_crisp (mock subprocess/crisp calls)
   - sample_project (minimal project tree with plan.yaml)
5. Migrate at least 5 test files to use the shared fixtures.
6. Run: python3 -m pytest tests/ -x -q — all tests must pass.
7. Commit: "refactor: dedup convergence parsing + add tests/conftest.py (#99)"

Write the code.`, { label: '#99 code hygiene', phase: 'P2 Parallel', isolation: 'worktree' }),

  // #100 defect_new junk dirs
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #100 — "defect_new/ and junk dirs pollute scans and JobStore"

PROBLEM:
1. defect_new/ parallel trees inflate JobStore and calculation counts.
2. Non-Name_Charge junk dirs under defect/ get counted in scans and accidentally submitted.
3. Analyze uses loose "_" in name filter for defect dirs.

REQUIREMENTS:
1. Read vasp_sop/defect/analysis.py and vasp_sop/cli/main.py to find where defect directories are scanned/iterated.
2. Add a helper function is_valid_defect_dir(path: Path) -> bool that returns True only if:
   - Directory name matches pattern: contains "_" AND both parts are non-empty (Name_Charge format)
   - OR directory contains defect_entry.json
3. Filter out defect_new/ explicitly — batch run, JobStore reconcile, and recovery should ignore it unless plan.yaml has an explicit opt-in key.
4. Apply the filter in all scan/submission paths.
5. Run: python3 -m pytest tests/ -x -q — all tests must pass.
6. Commit: "fix: filter defect_new and junk dirs from batch scans (#100)"

Write the code.`, { label: '#100 junk dir filter', phase: 'P2 Parallel', isolation: 'worktree' }),

  // #104 cache adapter cleanup
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #104 — "Interface: cache adapter cleanup"

All changes are in vasp_sop/core/cache.py and its callers.

FIXES NEEDED:
1. cache_lookup computes identity twice: Remove the _vc_has() call. Just call _vc_get_meta(input_dir=src_dir) directly — it returns None on miss. Catch IdentityInputError and return None.
2. vasp_results_put return value unchecked: In cli/main.py (search for vasp_results_put calls), check the return value. If None, log a warning.
3. query() zombie parameters: Remove functional, calc_type, tags_contains, bandgap_min, lattice_max, converged_only from the signature entirely. Remove the ValueError-raising block. Keep only formula, limit, cache_root.
4. _content_hash() is dead code: Remove it entirely.
5. Docstring at top says "default ~/.cache/vasp_cache" — verify and fix if wrong (vasp-cache default is ~/.cache/vasp_cache).
6. Run: python3 -m pytest tests/test_cache.py tests/test_cache_adapter.py -x -q — then full suite.
7. Commit: "refactor: cache adapter cleanup — remove dead code, fix double lookup (#104)"

Write the code.`, { label: '#104 cache cleanup', phase: 'P2 Parallel', isolation: 'worktree' }),

  // #94 batch observability
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #94 — "Batch observability and state hygiene"

THREE SUB-TASKS:
1. Non-blocking cache put: In cli/main.py, find where vasp_results_put / _cache_phase_results is called inside _advance_one_system. Move it to be non-blocking — either defer to end-of-round or wrap in try/except with a warning log so it never blocks system advance. (Do NOT add threading — just ensure exceptions don't halt the loop.)
2. batch status analyze column: Find the batch status display code. Add per-system analyze_status to the output (read from defect_energy_summary.json existence or similar artifact).
3. Retire _MP_FLAG: Find _MP_FLAG in vasp_sop/materials/mp.py and any references. Remove the constant and all usages. mp_state.json is the sole manifest.

Run: python3 -m pytest tests/ -x -q — all tests must pass.
Commit: "feat: batch observability — analyze column, non-blocking cache, retire _MP_FLAG (#94)"

Write the code.`, { label: '#94 observability', phase: 'P2 Parallel', isolation: 'worktree' }),
])

log(`P2 phase complete: ${p2Results.filter(Boolean).length}/5 agents succeeded`)

// ─── Phase 3: P1 Chain (sequential, depends on #96 being done) ──────
phase('P1 Chain')
log('Starting P1 chain: #103 → #95 → #93')

// #103 Domain abstractions
const fix103 = await agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #103 — "Domain abstraction: System model, pydefect interface, state/data separation"

This is the LARGEST refactor. Implement all three parts:

PART A — System class (vasp_sop/core/system.py):
Create a System class:
- __init__(self, root: Path, config) with self.root, self.config, self.name = root.name
- Properties: cpd_dir, uc_dir, defect_dir, target_dir (resolve from cpd/ or unitcell/structure_opt)
- phase() method: move the _phase() logic from cli/main.py here (filesystem-based phase detection)
- defect_dirs() method: return filtered list (only valid Name_Charge dirs, no defect_new)
- Keep it simple — this is a data holder + phase detection, not a god class.

PART B — Pydefect adapter (vasp_sop/defect/pydefect_adapter.py):
Create adapter functions that wrap pydefect CLI calls:
- calc_results(dirs, cwd) -> list of result dicts
- efnv(dirs, cwd, ...) -> list of result dicts  
- defect_energy_summary(cwd, ...) -> dict
For now, these can still call subprocess (the libs/ integration is future work), but centralize the interface.

PART C — State markers:
- Add a state.json concept: System.phase() should check for {root}/state.json first, fall back to filesystem inference.
- Add a helper System.save_phase(phase: str) that writes state.json.
- Do NOT migrate all callers yet — just create the infrastructure.

IMPORTANT: Do NOT rewrite cli/main.py to use System yet (that's #95). Just create the new modules and ensure they work standalone.
Run: python3 -m pytest tests/ -x -q
Commit: "feat: System model + pydefect adapter + state.json infrastructure (#103)"

Write the code.`, { label: '#103 domain abstractions', phase: 'P1 Chain' })

// #95 Wave decoupling (depends on #103)
const fix95 = await agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #95 — "Extract batch orchestrator from cli/main.py"

CONTEXT: Issue #103 has been implemented — vasp_sop/core/system.py with System class now exists. Read it first.

REQUIREMENTS:
1. Create vasp_sop/core/orchestrator.py with three wave functions:
   - wave1_optimize(sys: System, js: JobStore, dry_run: bool) -> None
   - wave2_submit(sys: System, js: JobStore, dry_run: bool) -> None  
   - wave3_postprocess(sys: System, dry_run: bool) -> dict (status info)

2. Extract the logic from _advance_one_system in cli/main.py into these functions:
   - wave1: STRUCTURE_OPT phase (target submission, convergence check, cache restore)
   - wave2: COMPETING + UNITCELL_DEFECT submission (competing dirs, UC tasks, defect submission)
   - wave3: CHEM_POT_DIAGRAM + post-processing (CPD compute, defect analysis)

3. Each wave function should:
   - Have explicit preconditions (check required artifacts exist)
   - Use System properties for directory access (not raw dict)
   - Be independently callable

4. Refactor _advance_one_system to:
   - Create System from the dict
   - Determine phase via sys.phase()
   - Call appropriate wave function(s)
   - Move eager defect-building from wave1 to a prepare step in wave2

5. Keep cli/main.py as thin CLI dispatch — argparse + call orchestrator.
6. Run: python3 -m pytest tests/ -x -q — all tests must pass.
7. Commit: "refactor: extract wave1/2/3 orchestrator from cli/main.py (#95)"

Write the code. This is a large refactor — be careful to preserve all existing behavior.`, { label: '#95 wave decoupling', phase: 'P1 Chain' })

// #93 CPD correctness (depends on #95)
const fix93 = await agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #93 — "CPD correctness: entrypoint, composition selection, phase gate audit"

CONTEXT: Issues #103 and #95 have been implemented. Read vasp_sop/core/system.py and vasp_sop/core/orchestrator.py first.

REQUIREMENTS:
1. Deterministic composition selection: In vasp_sop/defect/cpd.py, find where target composition is selected. Make it deterministic — when multiple compositions exist for a formula, select the one with lowest energy-per-atom. Log which was chosen and why.

2. CPD-only entrypoint: Add a CLI command or function that allows running ONLY the CPD phase:
   - vasp-sop cpd run <system_dir> -f FORMULA (or similar)
   - This should: create System, run wave2 (competing phases only), then wave3 (CPD solving), stop before UC/defect.
   - If adding a CLI subcommand is too invasive, at minimum create a standalone function cpd_only(root, formula, config) in orchestrator.py.

3. Phase gate audit: In the CHEM_POT_DIAGRAM → UNITCELL_DEFECT transition, add explicit checks:
   - target_vertices.yaml must exist and be non-empty
   - standard_energies.yaml must exist
   - Log a clear error if gates are not met (do not silently advance)

4. Add cpd_excluded_phases.yaml support: if this file exists in the system root, skip those phases during competing phase submission.

5. Run: python3 -m pytest tests/ -x -q
6. Commit: "feat: CPD correctness — deterministic selection, phase gates, cpd-only mode (#93)"

Write the code.`, { label: '#93 CPD correctness', phase: 'P1 Chain' })

// ─── Phase 4: Docs & Features ────────────────────────────────────────
phase('Docs & Features')
log('Updating documentation and adding regression tests')

const docsResults = await parallel([
  // #98 Sync docs
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #98 — "Sync documentation with current implementation"

REQUIREMENTS:
1. Read FEATURES.md. Remove/replace:
   - Any mention of maggma, JSONStore, meta.json, blobs.json → replace with "vasp-cache v0.3.0 (SQLite identity cache)"
   - Any mention of ProcessPoolExecutor(max_workers=14) → replace with "serial batch loop"
   - Ensure cache section describes the actual vasp-cache adapter (vasp_sop/core/cache.py)
2. Read docs/agent-conventions.md. Ensure cache section matches implementation.
3. Read vasp_sop/core/cache.py query() docstring — ensure it says "formula + limit only" and matches actual behavior.
4. Do NOT change code behavior — documentation only.
5. Commit: "docs: sync FEATURES.md and conventions with vasp-cache implementation (#98)"

Write the changes.`, { label: '#98 doc sync', phase: 'Docs & Features', isolation: 'worktree' }),

  // #90 Quick start guide
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Fix GitHub issue #90 — "新用户不知道第一步做什么——缺少快速上手指南"

Create QUICKSTART.md at the repo root with:
1. 环境检查 (Environment check):
   - Python version requirement
   - pip install -e . 
   - Verify: vasp-sop --help
   - MP API key setup (env var)
   - vasp-cache availability check
2. 最短路径 (Shortest path from zero to result):
   - vasp-sop materials fetch <mp-id> — get a structure
   - Create project dir with plan.yaml (show minimal example)
   - vasp-sop batch run . --dry-run — preview
   - vasp-sop batch run . — execute
   - Check results: vasp-sop cache status
3. CLI 命令概览 (Command overview table)
4. 常见问题 (Common issues / troubleshooting)

Read the actual CLI (vasp_sop/cli/main.py argparse section) and README.md to ensure accuracy.
Write in Chinese (matching the issue language) with English CLI commands.
Commit: "docs: add QUICKSTART.md for new users (#90)"

Write the file.`, { label: '#90 quickstart', phase: 'Docs & Features', isolation: 'worktree' }),

  // #101 Gold-sample regression
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #101 — "Gold-sample regression checks for publishable formation energies"

REQUIREMENTS:
1. Create tests/test_production.py with:
   - pytest.mark.skipif(not os.environ.get("VASP_SOP_PROD_ROOT"), reason="production tree not available")
   - Test: defect_energy_summary.json exists and parses for gold systems
   - Test: every converged charge state has correction.json
   - Test: |correction| < configurable bound (default 1.0 eV)
   - Test: charge states are continuous (no gaps > 2)
2. Gold systems: GaN, AlN (document in test docstring)
3. The test reads from VASP_SOP_PROD_ROOT/{system}/defect/ 
4. Add a CLI one-liner in the docstring: how to regenerate formation-energy figure
5. Run: python3 -m pytest tests/test_production.py -v (should skip gracefully without env var)
6. Commit: "test: gold-sample regression checks for formation energies (#101)"

Write the code.`, { label: '#101 gold-sample tests', phase: 'Docs & Features', isolation: 'worktree' }),
])

log(`Docs phase complete: ${docsResults.filter(Boolean).length}/3 agents succeeded`)

// ─── Phase 5: Enhancements ───────────────────────────────────────────
phase('Enhancements')
log('Implementing #51 (auto-healing) and #40 (doped charge states)')

const enhResults = await parallel([
  // #51 VASP error auto-healing
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #51 — "VASP error auto-healing: from diagnosis to self-correcting restart"

Read vasp_sop/vasp/errors.py (or find the error diagnosis module) and vasp_sop/defect/compute.py first.

REQUIREMENTS:
1. Create vasp_sop/vasp/auto_heal.py with:
   - A correction registry: dict mapping error_type -> correction function
   - Correction functions for: positive_energy, frozen_job, scf_no_converge, edwav, brion_error
   - Each correction modifies INCAR tags (use existing INCAR read/write utilities)
   - apply_correction(work_dir, error_type, attempt_number) -> bool
   - Staged escalation: least invasive first, escalate on repeated failure
   - Fallback: copy CONTCAR → POSCAR for unknown errors

2. Integrate into the CONTCAR restart loop in compute.py:
   - After diagnose_failure() identifies error type, call apply_correction()
   - Respect max attempt limit (existing cap of 20)
   - Log what correction was applied

3. Add INCAR patching helpers if not already centralized:
   - read_incar(path) -> dict
   - write_incar(path, params: dict)
   - patch_incar(path, **kwargs) — modify specific tags

4. Create tests: tests/test_auto_heal.py with synthetic scenarios
5. Run: python3 -m pytest tests/ -x -q
6. Commit: "feat: VASP error auto-healing with staged corrections (#51)"

Write the code.`, { label: '#51 auto-healing', phase: 'Enhancements', isolation: 'worktree' }),

  // #40 doped charge states
  () => agent(`You are working in the vasp-sop repository at ${REPO}.

TASK: Implement GitHub issue #40 — "用 doped 的价态预判策略替换 pydefect ds 的默认价态逻辑"

Read vasp_sop/defect/builder.py (_generate_defect_list) and vasp_sop/core/config.py first.

REQUIREMENTS:
1. In vasp_sop/defect/builder.py, modify _generate_defect_list():
   - Try to import doped.generation (guess_defect_charge_states, get_vacancy_charge_states)
   - If available: use doped's probability model to determine charge states
   - If not available: fall back to existing pydefect ds behavior (graceful degradation)
   
2. Add config support in vasp_sop/core/config.py:
   - charge_state_gen_kwargs: dict with optional keys:
     - probability_threshold (default 0.0075)
     - padding (default 1)
     - use_doped (default True, falls back if import fails)

3. The integration should:
   - Read supercell_info.json for host structure info
   - Call doped's charge state prediction
   - Write defect_in.yaml with the predicted charge states
   - Log which method was used (doped vs pydefect fallback)

4. Add tests: tests/test_charge_prediction.py
   - Test fallback when doped not installed (mock ImportError)
   - Test config parsing
5. Run: python3 -m pytest tests/ -x -q
6. Commit: "feat: doped charge state prediction with pydefect fallback (#40)"

Write the code.`, { label: '#40 doped charges', phase: 'Enhancements', isolation: 'worktree' }),
])

log(`Enhancements phase complete: ${enhResults.filter(Boolean).length}/2 agents succeeded`)

// ─── Summary ─────────────────────────────────────────────────────────
phase('Docs & Features')
log('All phases complete. Summary of work done across all issues.')
