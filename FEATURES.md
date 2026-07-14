# Feature Catalog — vasp-sop v0.1.0

> User-visible capabilities of the vasp-sop high-throughput VASP point-defect
> pipeline framework. Each section catalogs a capability area with CLI commands,
> configuration knobs, pipeline stages, output artifacts, and automated
> behaviors. Source cross-references are collected in the
> [Feature Coverage Map](#appendix-feature-coverage-map).

---

## 1. CLI Commands

`vasp-sop` exposes 8 top-level subcommands with 24 total sub-actions:

| Subcommand | Actions | Description |
|---|---|---|
| `batch` | `run` | Multi-system pipeline — advance all systems until completion |
| | `status` | Show per-system phase table |
| | `generate-inputs` | Generate VASP inputs for all systems that need them |
| | `submit` | Submit VASP calculations for all systems |
| | `progress` | Per-system completion percentage |
| `defect` | `init` | Generate `plan.yaml` with inference and dynamic comments |
| | `run` | Run the full point-defect pipeline for one system |
| | `resume` | Resume pipeline from saved state (legacy — use `batch run`) |
| | `status` | Show pipeline stage status for a project |
| | `build` | Standalone defect structure generation (supercell + inputs) |
| | `analyze` | Standalone defect post-processing (not yet implemented) |
| `pipeline` | `run` | Single-system pipeline (requires `--config plan.yaml`) |
| `materials` | `fetch` | Download competing phases from Materials Project |
| | `phases` | List cached competing phases in a CPD directory |
| | `poscar` | Download a single POSCAR by MP-ID |
| | `cache list` | List cached MP download combinations |
| | `cache clear` | Clear MP cache |
| `vasp` | `inputs` | Generate VASP inputs via `vise` |
| | `check` | Check VASP completion (OUTCAR existence + convergence) |
| `cpd` | `energies` | Compute composition energies from VASP outputs |
| | `diagram` | Solve and plot the chemical-potential phase diagram |
| `unitcell` | `yaml` | Generate `unitcell.yaml` from completed VASP outputs |
| `cache` | `put` | Cache a VASP calculation directory (auto-detects formula/task) |
| | `query` | Semantic cross-project cache search with 6 filters |
| | `status` | Show cache statistics and list entries |
| | `verify` | Check dual-store consistency |
| | `migrate` | Migrate from old SQLite cache (`cache.db`) to JSONStore |

**Global flags:**

- `--version` — print `vasp-sop {version}`
- `-v` / `--verbose` — enable debug logging

**Notable flag — `batch run --dry-run`:**

Processes all pipeline stages (phase detection, input generation, defect
structure building) but skips every VASP submission. Useful for previewing
what a live run would do without consuming cluster resources.

---

## 2. Batch Pipeline Orchestration

The batch pipeline (`vasp-sop batch run .`) manages multiple independent
material systems in a shared project root directory.

### State Machine Phases

Each system cycles through a deterministic phase sequence:

```
STRUCTURE_OPT  →  COMPETING  →  CHEM_POT_DIAGRAM  →  UNITCELL_DEFECT  →  COMPLETE
```

Phase is determined by filesystem state — OUTCAR presence, convergence
status, presence of `target_vertices.yaml`, etc. — not from a database.

### Three-Wave VASP Scheduling

|---|---|---|
| 1 | STRUCTURE_OPT | Submit structure_opt VASP; generate all other inputs locally while it runs |
| 2 | COMPETING + UNITCELL + DEFECT | Submit competing phases, unitcell (band/dos/dielectric), perfect, and all defect jobs in parallel |
| 3 | POST-PROCESSING | pydefect analysis, formation energy summary, unitcell YAML generation |

### Key Behaviors

- **Per-system isolation** — a failure in one system does not block others
- **Parallel execution** — `ProcessPoolExecutor(max_workers=14)` across systems
- **Cache-aware skip** — saves `.target_submit.json` with `"cached"` when STRUCTURE_OPT or COMPETING phase results are found in the global cache
- **Dry-run mode** — `--dry-run` processes all pipeline stages without submitting any VASP jobs
- **Orphaned-output cleanup** — stale crisp output directories (`output/`) are detected and consolidated during system advancement
- **Infinite-loop protection** — `_MAX_ITERATIONS` gate prevents unbounded polling
- **Exclusion filter** — `--exclude` flag skips specific systems by directory name

---

## 3. Configuration & Plan Generation

Configuration lives in a `plan.yaml` file per system, managed by the
`PipelineConfig` dataclass.

### `plan.yaml` Schema (19 configurable keys)

```yaml
project:
  formula: ""               # Chemical formula (required)
  dopant_elements: []       # List of dopant elements
  poscar_src: ""            # POSCAR source path or MP-ID
parameters:
  functional: pbesol        # XC functional (pbesol, pbe, scan, hse, etc.)
  encut: ~                  # Plane-wave cutoff (auto-detected if null)
  hubbard_u: false          # Enable DFT+U (auto-detected)
  pp: []                    # POTCAR variant overrides per element
supercell:
  tool: doped               # Backend: "doped" or "pydefect"
  min_atoms: 200            # Minimum supercell atoms (pydefect)
  max_atoms: 600            # Maximum supercell atoms (pydefect)
  min_distance: 10.0        # Minimum image distance in Å (doped)
defects:
  interstitials: false      # Generate interstitial defects
  interstitial_indices: []  # Indices for targeted interstitials
  complex_n: 1              # Complex n-body defect order (≥2 enables)
  max_distance: 5.0         # Maximum distance for remote defects in Å
corrections:
  O2: 1.374                 # O₂ molecule energy correction (eV)
  Cl2: 1.228                # Cl₂ molecule energy correction (eV)
  F2: 0.924                 # F₂ molecule energy correction (eV)
energy_adjust_step: 0.01    # Energy adjustment increment for unstable phases
```

### Auto-Generation

`vasp-sop defect init --formula GaN --dopant Mg Si` produces a complete
`plan.yaml` with:

- **ENCUT auto-detection** — reads `ENMAX` from downloaded POTCAR, sets 1.3× the maximum
- **DFT+U auto-detection** — enabled when transition metals or f-elements are present
- **POTCAR variant listing** — enumerates available PAW_PBE variants per element
- **Competing phase auto-detection** — queries Materials Project via `pydefect_vasp mp`

### Format Support & Validation

- **Legacy JSON migration** — `from_legacy_json()` reads old `info.json` format
- **Flat-to-nested auto-conversion** — `_flat_to_nested()` handles legacy flat configs
- **Validation on load** — `__post_init__` raises `ValueError` for invalid formulas, supercell bounds, or parameter ranges
- **Round-trip stability** — all three supercell keys (`min_atoms`, `max_atoms`, `min_distance`) are always emitted so data is never silently dropped

---

## 4. Chemical Potential Diagram (CPD)

The CPD stage establishes the chemical potential environment for defect
formation energy calculations.

### Workflow

1. **Competing phase download** — `pydefect_vasp mp` queries Materials Project
   with a 0.5 meV/atom hull energy threshold
2. **VASP submission** — all competing phases submitted as a parallel batch
3. **Composition energy computation** — `pydefect` processes OUTCARs to extract
   raw energies per phase
4. **Molecule corrections** — empirical corrections applied to diatomic gas
   molecules (O₂: +1.374 eV, Cl₂: +1.228 eV, F₂: +0.924 eV)
5. **Phase diagram solving** — pydefect solves the convex hull to determine
   chemical potential ranges where the target is stable
6. **Plotting** — energy convex hull and target vertex diagrams (optional)

### Special Cases

- **Single-element systems** — synthetic `target_vertices.yaml` and
  `chem_pot_diag.json` generated without VASP computation
- **Binary compounds** — synthetic 1D CPD output for 2-element systems
- **4-element systems** — skipped with warning (pydefect half-space > 3D
  limitation), handled by dimension check
- **Unstable target phase** — iterative energy adjustment loop with
  configurable step size (`energy_adjust_step`)
- **Single-element target** — automatic synthetic vertex generation

### Output Artifacts

| File | Contents |
|---|---|
| `composition_energies.yaml` | Raw VASP energies per competing phase |
| `standard_energies.yaml` | Molecule-corrected standard formation energies |
| `target_vertices.yaml` | Computed chemical potential vertices for the target species |
| `chem_pot_diag.json` | Serialized chemical-potential diagram data |

---

## 5. Supercell Construction & Defect Generation

The defect builder (`vasp_sop/defect/builder.py`) constructs the supercell,
enumerates defect structures, and generates VASP input files.

### Dual Supercell Backends

| Tool | Constraint | Selection |
|---|---|---|
| `doped` | `min_distance` (minimum image distance) | Default — canonical for distance constraints |
| `pydefect` | `min_atoms` / `max_atoms` | Atom-count bounds only; no image distance limit |

Selection via `plan.yaml` `supercell.tool` key.

### Supported Defect Types (6)

1. **Vacancy** — missing atom at a lattice site
2. **Substitution** — atom replaced by a different species (including dopants)
3. **Interstitial** — extra atom at a high-symmetry site or charge-density extrema
4. **Antisite** — atom swapped with a site of another species
5. **Complex (n-body)** — multiple defects combined, generated via
   `ComplexDefectMaker` (activated when `complex_n ≥ 2`)
6. **Remote** — defect placed at a specified distance from another defect

### Interstitial Placement

When `defects.interstitials` is enabled, sites are identified from:

- DOS charge-density local extrema (`volumetric_data_local_extrema.json`)
- The `interstitial_indices` configuration list

### Parallel Input Generation

VASP inputs (INCAR, POSCAR, POTCAR, KPOINTS) for all defect directories are
generated concurrently using `ThreadPoolExecutor(max_workers=8)`.

### Symmetry-Aware Site Grouping

`StructureSymmetrizer` (from `vise.util`) groups equivalent sites, handling
centering, time-reversal symmetry, and angle tolerance — produces sorted
`equivalent_atoms` indices for defect enumeration.

---

## 6. VASP Job Management

The job layer handles VASP submission, monitoring, and lifecycle across two
backends.

### Dual Submission Backend

| Backend | Mechanism | Use Case |
|---|---|---|
| **crisp** | `crisp submit` CLI | HPC cluster (Slurm) — production |
| **local** | `subprocess.Popen` + `mpirun` | Local/development machines |

Backend auto-detected: crisp preferred if `crisp` is on `$PATH`.

### Job Handle Hierarchy

```
VaspJob  (poll, done, task_name, work_dir)
  ├── LocalVaspJob   — wraps subprocess.Popen, checks return code
  └── CrispVaspJob   — wraps crisp task, queries crisp for status
```

### Input Readiness

`_vasp_input_ready(path)` checks for four required files:

- `INCAR`, `POSCAR`, `POTCAR`, `KPOINTS`

### Convergence Detection (`check_converged`)

See full rules: [`docs/architecture/06-convergence.md`](docs/architecture/06-convergence.md).

- **Structural relaxation** (`IBRION∈{1,2,3}`, `NSW>1`): ionic convergence.
  - VASP authority string: `reached required accuracy - stopping structural energy minimisation`
  - Implementation: last-block `max|F| ≤ |EDIFFG|` when `EDIFFG<0` (aligned with that message on production data); parameters from **OUTCAR** not restarted INCAR
- **Non-relaxation** (`NSW≤1` or `IBRION∉{1,2,3}`): **not** force-based — `General timing` only
- Looks for OUTCAR at `{dir}/OUTCAR` then **`{dir}/output/OUTCAR`** (crisp layout)
- Returns `bool` (never raises)

`check_task_complete(path, task_type)`: band/dos also require `vasprun.xml` (root or `output/`); dielectric is timing-only.

### Error Diagnosis (11 Modes)

Diagnosed by scanning the OUTCAR tail (~64 KB) for known error patterns:

| Mode | Pattern |
|---|---|
| `positive_energy` | total energy is positive |
| `frozen_job` | EDDDAV / ZPOTRF / EDDRMM / ZHEGV / CNORMN / FEXCF |
| `scf_no_converge` | Sub-Space-Matrix is not Hermitian |
| `brion_error` | BRION / BRMIX computational errors |
| `real_optlay` | REAL_OPTLAY: internal error |
| `edwav` | WARNING in EDWAV: call to DAV |
| `pssyevx` | ERROR in subspace rotation PSSYEVX |
| `bz_inequiv` | internal error in BZ_INEQUIV |
| `rhosyg` | RHOSYG: internal error |
| `posmap` | POSMAP: internal error |
| `point_group` | point group mismatch between supercell and perfect cell |

**Fix suggestions** provided for 5 common modes (positive energy, frozen job,
SCF no convergence, BRION error, EDWAV error) via `recommended_fix()`.

### Batch Wait

`wait_all(jobs, poll_interval)` — polls all jobs at a configurable interval
(default 60 s), raises `RuntimeError` on the first job failure (fail-fast).

### Output Consolidation

`move_crisp_outputs(work_dir)` — promotes crisp `output/` into the work dir,
then removes `output/`. **Per-file mtime wins**: if both root and `output/` have
the same name, the **newer** copy is kept (avoids a stale root OUTCAR blocking a
fresh fetch). Convergence helpers still read `output/` if root is missing.



---

## 7. Formation Energy Analysis

The formation-energy post-processing pipeline (`vasp_sop/defect/analysis.py`)
runs 11 sequential pydefect steps to produce the final defect energy summary.

### Pipeline Steps

| Abbrev | Command | Purpose |
|---|---|---|
| `cr` | `pydefect_vasp cr` | Collect calculation results from all defect directories |
| `efnv` | `pydefect efvn` | Energy-free NV correction using the perfect cell |
| `dsi` | `pydefect dsi` | Extract defect structure information |
| `dvf` | `pydefect_util dvf` | Compute defect volume fractions |
| `pbes` | `pydefect_vasp pbes` | Perfect band-edge state (VBM/CBM) |
| `beoi` | `pydefect beoi` | Band-edge orbital information |
| `bes` | `pydefect bes` | Band-edge state for each defect |
| `dei` | `pydefect dei` | Defect energy information with corrections |
| `des` | `pydefect des` | Defect energy summary |
| `cs` | `pydefect cs` | Calc summary for all defects |
| `pe` | `pydefect pe` | Phase equilibrium for each chemical-potential vertex |

### Skipping Condition

If `defect_energy_summary.json` already exists **and** status is `full`, the
pipeline is skipped. Incomplete finals are demoted to
`defect_energy_summary.partial.json` (issue #0007).

### Readiness / honesty

- Ionic convergence via `check_converged` (OUTCAR NSW + force gate)
- `pydefect_vasp cr` / efnv require `vasprun.xml` or existing `calc_results.json`
  (issue #0010); OUTCAR-only dirs are tracked as `missing_vasprun`
- `analyze_status.json` exposes `n_converged`, `n_corrected`, `n_dei`,
  `missing_vasprun`, `missing_calc_results`, etc. (issue #0013)
- CLI: `vasp-sop defect analyze <project_dir>` (issue #0014)

### Output Artifact

| File | Contents |
|---|---|
| `defect_energy_summary.json` | Formation energies (only when **full**) |
| `defect_energy_summary.partial.json` | Demoted incomplete summary |
| `analyze_status.json` | Machine-readable QA counters |
| `calc_summary.json` | Per-calculation summary metadata |

### Dependencies

Requires all three upstream artifacts: `unitcell.yaml`, `standard_energies.yaml`,
and `target_vertices.yaml`.

---

## 8. Results Cache

The cache layer (`vasp_sop/core/cache.py`) stores parsed VASP results for
cross-project reuse and querying.

### Architecture

Maggma `JSONStore` dual-store at `~/.vasp_sop/`:

| Store | File | Content |
|---|---|---|
| **Meta** | `meta.json` | Lightweight metadata: formula, content_hash, total_energy, bandgap, converged, calc_type, n_sites, space_group, tags, source_dir |
| **Blobs** | `blobs.json` | Large parsed VASP output: outcar_dict, vasprun_dict, structure_dict, incar_dict, kpoints_dict |

### Core Operations

**`vasp_results_put(src_dir, formula, content_hash, task_name)`**
- Parses VASP outputs via `TaskDoc.from_directory()` (primary) with regex
  fallback for minimal OUTCARs
- Writes to both meta and blob stores
- Best-effort: parsing exceptions are caught and logged

**`vasp_results_get(formula, key)`**
- Returns merged meta+blob dict for exact (formula, key) match
- `key` may be `content_hash`, `task_name`, or `mp_id`

**`cache_lookup(src_dir)`**
- Convenience: auto-detects formula + content_hash, delegates to `get`

### Query API (6 Filters)

`query(formula, functional, calc_type, tags, bandgap_min, lattice_max,
       converged_only)` — MongoDB-style cross-project search:

| Filter | Type | Description |
|---|---|---|
| `formula` | string | Chemical formula (exact match) |
| `functional` | string | XC functional (PBE, HSE, SCAN, etc.) |
| `calc_type` | string | Type (Static, Relax, Dielectric, etc.) |
| `tags` | string | Comma-separated (DFT+U, spin, etc.) |
| `bandgap_min` | float | Minimum bandgap in eV |
| `lattice_max` | float | Max lattice constant filter (default: 25.0 Å) |
| `converged_only` | bool | Only return converged calculations |

### Auto-Fingerprinting

`_content_hash(src_dir)` produces a deterministic hash from:

- Formula (from POSCAR)
- K-point grid specification
- INCAR fingerprint keys (14: ENCUT, PREC, ISMEAR, SIGMA, ISIF, LDAU,
  LDAUTYPE, LDAUU, LDAUJ, LDAUL, GGA, IVDW, LASPH, METAGGA)
- POTCAR species + functional combination

### Job State Tracking

Unified SQLite database at `~/.vasp_sop/jobs.db` (WAL mode) with two tables:

| Table | Purpose |
|---|---|
| `job_history` | Per-calculation final status: `submitted` / `converged` / `failed` (with `reason`, `attempt` count) |
| `tracked` | Active submissions awaiting polling (dirs submitted to crisp but not yet completed) |

Legacy `submissions.db` was merged into `jobs.db`.

- **`list_cache(limit=50)`** — most recent cache entries
- **`cache_stats()`** — aggregate totals: formulas, entries, per-functional breakdown
- **`migrate_from_sqlite()`** — one-shot migration from old `cache.db`
- **`MAX_LATTICE`** — size guard (default 25.0 Å, tunable) to keep large
  surfaces/2D systems out of the cache

---

## 9. Materials Project Integration

The materials layer (`vasp_sop/materials/mp.py`) provides MP data access with
a two-tier caching system.

### Phase Discovery

`fetch_candidate_phases(formula, dopants, cpd_root)`:
- Runs `pydefect_vasp mp` to download competing phases from MP
- Filters by intrinsic elements plus dopants
- Hull energy threshold: 0.5 meV/atom
- Returns target directory path

### Phase Listing

`list_phases(cpd_root, intrinsic_elements)`:
- Scans CPD directories for phase directories
- Returns structured info: formula, name, space group, energy above hull, MP-ID
- Filters to relevant phases (intrinsic elements + target)

### POTCAR Management

`list_potcar_variants(formula, dopants)`:
- Enumerates available PAW_PBE POTCAR variants per element
- Returns dict with suggested default variant (lower energy) and alternatives

### Parameter Inference

| Feature | Method | Logic |
|---|---|---|
| **ENCUT** | `detect_encut(potcar_path)` | 1.3 × max `ENMAX` from POTCAR |
| **DFT+U** | `needs_hubbard_u(poscar_path)` | Transition-metal or f-element presence check |

### Caching Architecture

Two-tier local filesystem cache at `~/.vasp_sop/mp_cache/`:

| Level | Key | Content |
|---|---|---|
| Combo cache | Element set (sorted, hyphenated) | Full `cpd/` directory tree |
| Per-formula cache | MP-ID | `POSCAR` + `POTCAR` file |

Cache operations are transparent: `mp_combo_get/put/restore` and
`mp_poscar_get/put` handle all I/O.

---

## 10. Pipeline State & Resume

State tracking uses **JobStore** (`vasp_sop/core/job_store.py`) — a unified SQLite database at `~/.vasp_sop/jobs.db`.

Unlike the legacy `StateStore` (file-based `.pipeline_state.json`), JobStore records per-calculation VASP job status:

| Status | Meaning |
|---|---|
| `submitted` | Submitted to crisp, awaiting completion |
| `converged` | OUTCAR converged (ionic relaxation met or single-point completed) |
| `failed` | Given up after max retries or VASP crash; `reason` field explains why |

System-level phase (`STRUCTURE_OPT` → `COMPLETE`) is derived in real-time from JobStore + marker files by `_phase()`.

### Resume

`vasp-sop batch run .` is idempotent — each cycle checks current phase, submits needed jobs, and collects completed results.

## 11. Defect Compute Loop

The defect VASP runner (`vasp_sop/defect/compute.py`) handles the long-running
VASP execution for defect supercells with automatic restart and recovery.

### CONTCAR Restart Loop

```
For each defect directory:
  1. Check convergence via OUTCAR (check_converged)
  2. If not converged and CONTCAR exists:
     a. Copy CONTCAR → POSCAR
     b. Set ISTART=1 in INCAR
     c. Double NSW (max 3200)
     d. Re-submit VASP
  3. Repeat up to 20 attempts
```

### Stall Detection

- Tracks maximum force (`max_f`) across attempts per defect
- **Stall threshold**: max_f dropped by < 1% compared to the previous attempt
  (`cur_f >= old_f * 0.99`)
- Stalled jobs are excluded from submission for one cycle

### Auto-Recovery

When a defect is detected as stalled:

1. **POTIM increase**: current POTIM × 1.5 (capped at 5.0)
2. CONTCAR-based restart with the updated INCAR
3. Error diagnosis: `diagnose_failure()` scans OUTCAR tail for known patterns
4. Fix suggestion: `recommended_fix()` prints a human-readable remediation hint
5. Stalled jobs remain excluded until their next progress check

### Parallel Submission

- Perfect cell + all unconverged defect directories submitted as a batch
- All jobs polled concurrently in 60-second intervals
- Individual job failures are logged but do not abort the batch
- Only non-stalled jobs are submitted each cycle

### Termination

- Loop exits when all directories converge or no progress is possible
- After 20 attempts, remaining incomplete directories are logged as warnings
- Gallium (`still_incomplete`) is reported but does not raise

---

## Appendix: Feature Coverage Map

| Section | Primary Source File(s) | Key Functions / Classes |
|---|---|---|
| 1 CLI | `vasp_sop/cli/main.py` | `main()`, 8 `_add_*_parser()` functions, 4 `_handle_*()` dispatch functions |
| 2 Batch Orchestration | `vasp_sop/cli/main.py` | `_batch_run()`, `_advance_one_system()`, `_phase()` |
| 3 Configuration | `vasp_sop/core/config.py` | `PipelineConfig`, `generate_config()`, `DEFAULT_PLAN` |
| 4 CPD | `vasp_sop/defect/cpd.py` | `run_cpd()`, `compute_chemical_potentials()`, `apply_molecule_corrections()`, `adjust_unstable_phase()` |
| 5 Supercell & Defect Gen | `vasp_sop/defect/builder.py` | `build_all()`, `_build_supercell_doped()`, `_build_supercell_pydefect()`, `construct_complex_defects()` |
| 8 Results Cache | `vasp_sop/core/cache.py` | `vasp_results_put()`, `vasp_results_get()`, `query()`, `_content_hash()` |
| 9 MP Integration | `vasp_sop/materials/mp.py` | `fetch_candidate_phases()`, `list_phases()`, `list_potcar_variants()`, `detect_encut()`, `needs_hubbard_u()` |
| 10 Pipeline State | `vasp_sop/core/job_store.py` | `JobStore` (SQLite — `converged`/`failed` + `tracked` table) |
| 11 Defect Compute Loop | `vasp_sop/defect/compute.py` | `run_vasp()`, `_collect_jobs()`, `_max_f()`, stall detection, POTIM auto-recovery |

---

*Generated from vasp-sop v0.1.0 source tree. Re-run the catalog generation
workflow when the codebase changes to keep this document current.*
