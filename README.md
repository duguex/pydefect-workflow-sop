# vasp-sop

**VASP point-defect high-throughput pipeline orchestrator** (v0.1.0, MIT).

Given a mature scientific project directory (with its project configuration and existing calculation inputs), `vasp-sop` orchestrates the calculations and analyses required by the project: competing-phase search, chemical-potential analysis, unitcell properties, supercell/defect enumeration, VASP execution, and formation-energy analysis. Established scientific tools such as `pydefect`, `doped`, and `phonopy` are inputs/components of this orchestration layer.

A chemical formula and optional dopants can be used by convenience commands to initialize a project configuration; they are not the core input model of the orchestration layer.

**Not** a DFT engine, materials database, or Slurm replacement. Submits individual calculation units through **`crisp`** (or mpirun). Result reuse is integrated into CRISP through **[vasp-cache](https://github.com/duguex/vasp-cache)** and is linked separately as a component reference.

---

## Quick start

```bash
# install (editable)
pip install -e .

# dry-run a project tree (plan.yaml present)
vasp-sop batch run /path/to/project --dry-run

# advance systems for real
vasp-sop batch run /path/to/project

# result reuse (crisp-owned)
crisp cache status

# generate a read-only evidence report from current files
vasp-sop report /path/to/project
```

Tests:

```bash
python3 -m pytest tests/
```

---

## Pipeline (short)

| Stage | Meaning |
|-------|---------|
| STRUCTURE_OPT | Structure optimization of host |
| COMPETING | Competing phases |
| CHEM_POT_DIAGRAM | Chemical potential diagram post |
| UNITCELL_DEFECT | Unitcell props + defect supercells + VASP |
| COMPLETE | Terminal |

Three-wave VASP scheduling and JobStore details: [FEATURES.md](FEATURES.md), [docs/agent-conventions.md](docs/agent-conventions.md).

---

## Documentation roles

| Audience | File | Role |
|----------|------|------|
| **Humans** | This README | Install + one-command start |
| **Coding agents** | [AGENTS.md](AGENTS.md) | **Canonical** agent rules (short) |
| Deep conventions | [docs/agent-conventions.md](docs/agent-conventions.md) | Architecture, patterns, known issues (ex-AGENTS dump) |
| Feature inventory | [FEATURES.md](FEATURES.md) | JobStore, phases, capabilities |
| Project write-up | [PROJECT.md](PROJECT.md) | Longer product narrative |

No root `CLAUDE.md` (by policy: do not invent unless you request scheme A).

---

## Layout (top level)

| Path | Role |
|------|------|
| `vasp_sop/` | Package + CLI |
| `tests/` | pytest suite |
| `docs/` | Agent conventions + other notes |
| `issues/` | Tracked issues |
| `unitcell/` | Unitcell-related assets |
| `libs/` | Vendored/helpers as present |

---

## Related

- Cache backend: `vasp-cache`  
- HPC submit path: `crisp` (see your cluster docs)
