# Repository Guidelines

## Project Overview

vasp-sop (v0.1.0, MIT) is a Python framework for high-throughput VASP point-defect
calculations. Given a chemical formula and optional dopant elements, it automates
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
  │   └── _advance_one_system()  ← per-system state machine
  │       ├── TARGET      → submit structure_opt VASP
  │       ├── COMPETING   → submit/check competing phases
  │       ├── CPD_POST    → run chemical potential diagram
  │       └── UC_DF       → build defect structures + submit VASP
  │
  ├── defect build .       ← standalone defect structure generation
  ├── cache status/verify  ← SQLite cache inspection
  └── materials fetch      ← MP query + download
```

### Key Modules

| Module | Path | Role |
|--------|------|------|
| CLI | `vasp_sop/cli/main.py` | argparse dispatch, batch orchestrator |
| Config | `vasp_sop/core/config.py` | `PipelineConfig` dataclass, `plan.yaml` |
| Jobs | `vasp_sop/core/jobs.py` | VASP submission (crisp/local), `run_local()` |
| State | `vasp_sop/core/state.py` | Pipeline state machine, `StateStore` |
| Cache | `vasp_sop/core/cache.py` | SQLite-backed VASP/CPD result cache |
| Builder | `vasp_sop/defect/builder.py` | Supercell + defect enumeration + VASP inputs |
| CPD | `vasp_sop/defect/cpd.py` | Competing phase diagram pipeline |
| Unitcell | `vasp_sop/defect/unitcell.py` | Perfect-cell band/DOS/dielectric |
| Analysis | `vasp_sop/defect/analysis.py` | Formation energy post-processing |
| Pipeline | `vasp_sop/defect/pipeline.py` | Three-wave VASP orchestration |
| VASP I/O | `vasp_sop/vasp/io.py` | `check_converged()`, `prepare_inputs()` |
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
  │   ├── _generate_structures()  ← pydefect_vasp de
  │   └── _generate_vasp_inputs() ← ThreadPoolExecutor(8)
  ├── compute.run_vasp() → OUTCAR per defect
  └── analysis.analyze() → defect_energy_summary.json
```

## Key Directories

```
vasp_sop/
├── cli/main.py          ← CLI entry point (1344 lines)
├── core/                ← Config, jobs, state, cache
├── defect/              ← Pipeline stages (cpd, unitcell, builder, compute, analysis)
├── vasp/                ← VASP I/O layer (check_converged, prepare_inputs)
└── materials/           ← Materials Project integration

tests/
├── test_cache.py        ← Cache tests (MP + SQLite)
├── test_cli.py          ← CLI + dry-run tests
├── test_builder.py      ← Supercell builder tests
├── test_config.py       ← Config validation + round-trip
├── test_defects.py      ← Convergence detection tests
├── test_jobs.py         ← Subprocess + input-ready tests
├── test_import.py       ← Smoke tests
└── test_state.py        ← Pipeline state machine tests

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

## Code Conventions & Patterns

### Configuration

- Project config is `plan.yaml` with a nested dict structure
- Loaded via `PipelineConfig.from_yaml(path)` which returns a validated dataclass
- `from_plan(dict)` / `to_plan()` handle the dict↔dataclass conversion
- `DEFAULT_PLAN` provides the template for `generate_config()`
- Validation via `__post_init__` with `ValueError`

### Cache

- SQLite at `~/.vasp_sop/cache.db` with two tables: `calc_results`, `cpd_results`
- `calc_results_put(formula, mpid, src_dir)` — parses OUTCAR/vasprun.xml/INCAR/CONTCAR/KPOINTS via pymatgen, stores as JSON blobs
- `calc_results_get(formula, mpid)` — returns `Optional[dict]` with parsed data
- Never write to `CALC_CACHE` directory (backward-compat constant only)
- For testing: `override_cache_root(tmp_path)` isolates cache to temp directory
- `_is_cached(pd: Path) -> bool` checks if a phase dir is in the global cache

### Error Handling

- CLI commands catch top-level exceptions and log via `logger.exception()`
- Batch pipeline: errors in `_advance_one_system` are caught per-system, not propagated (one failing system doesn't block others)
- `run_local()` raises `RuntimeError` on non-zero exit or timeout
- `check_converged()` returns `bool`, never raises
- Cache functions catch exceptions during pymatgen parsing, log warnings, store what they can

### Parallelism

- `ProcessPoolExecutor(max_workers=14)` for per-system parallelism in batch run
- `ThreadPoolExecutor(max_workers=8)` for per-directory VASP input generation
- Process workers import `_advance_one_system` at module level (pickle-safe)
- Caching: `_cache_phase_results()` called after job completion in polling loop

### Batch Pipeline State Machine

Each system cycles through phases determined by `_phase()`:

```
TARGET → COMPETING → CPD_POST → UC_DF → DONE
```

- Phase is determined by filesystem state (OUTCAR presence, convergence, target_vertices.yaml, etc.)
- `batch run` polls and advances all systems via `ProcessPoolExecutor`
- `--dry-run` does one pass and exits (no VASP submission)
- Cache hit in TARGET/COMPETING skips submission (saves `.target_submit.json` with `"cached"`)

### Job Management (CRISP)

**CRITICAL:** All HPC operations MUST go through `crisp` CLI, never through
`scancel`/`sbatch`/`squeue` directly. Load `skill://crisp` before any cluster
operation.

```bash
crisp submit              # Submit VASP job
crisp cancel -n TASK_NAME # Cancel job
crisp jobs                # List all jobs
```

### Testing

- Framework: pytest (no conftest.py)
- Cache isolation: `override_cache_root(tmp_path / ".vasp_sop")` in `@pytest.fixture(autouse=True)`
- No pytest markers, no parameterization
- Each scenario is a separate test method
- `tmp_path` fixture used universally for file I/O tests
- `monkeypatch` used in CLI tests to prevent real VASP submission

## Important Files

| File | Purpose |
|------|---------|
| `vasp_sop/cli/main.py` | CLI entry, batch orchestrator, `_advance_one_system` |
| `vasp_sop/core/config.py` | `PipelineConfig`, `plan.yaml` generation |
| `vasp_sop/core/cache.py` | SQLite cache (calc_results + cpd_results) |
| `vasp_sop/core/jobs.py` | VASP submission, `run_local()` |
| `vasp_sop/core/state.py` | Pipeline state machine |
| `vasp_sop/defect/builder.py` | Supercell + defect generation |
| `vasp_sop/defect/cpd.py` | Chemical potential diagram |
| `vasp_sop/defect/pipeline.py` | Three-wave orchestration |
| `vasp_sop/vasp/io.py` | `check_converged()`, `prepare_inputs()` |
| `pyproject.toml` | Dependencies, entry point, pytest config |
| `PROJECT.md` | Detailed pipeline SOP (Chinese) |
| `tests/test_cache.py` | Cache test suite with isolation fixture |

## Runtime Requirements

- Python >= 3.10
- Dependencies: pymatgen, pydefect, vise, numpy, pandas, pyyaml
- HPC cluster access with `crisp` scheduler agent
- Materials Project API key (`MP_API_KEY` or `PMG_MAPI_KEY`)
- VASP binary via singularity container (`/mnt/shared/vasp_latest.sif`)

## Known Issues

- `cache_target_results` in CPD_POST calls `calc_results_put` then `calc_cpd_put`;
  if the target dir lacks OUTCAR (e.g. cached result restored without files),
  the put silently skips. Verify converged flag before calling.
- CPD target composition lookup in `relative_energies.yaml` can fail
  intermittently (pydefect key format instability). Issue #23 tracks this.
- 4-element CPD diagrams fail in pydefect (halfspace >3D). Handled via
  dimension check in `cpd.py`.
